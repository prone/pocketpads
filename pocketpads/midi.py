"""MIDI for PocketPads: controllers in, notes and clock out, through the ALSA sequencer.

Everything the app needs is three calls: MidiPort.start(), .poll() for incoming events, and
.note() / .transport() for outgoing ones. The port connects itself to every MIDI device the
system shows, and looks again every few seconds, so a controller can be plugged in at any time.

No Python MIDI package is needed: libasound is driven directly with ctypes. If the library or
the sequencer is missing (on macOS, say), the port simply reports itself unavailable.

Timing: the audio you hear is queued a few blocks ahead, so outgoing notes and clock are held
back by the same amount (`latency`) to land together with the sound.
"""
import ctypes
import ctypes.util
import os
import threading
import time
from collections import deque

NOTE_ON, NOTE_OFF, CONTROLLER, PROGRAM, SYSEX = 6, 7, 10, 11, 130
START, CONTINUE, STOP, CLOCK = 30, 31, 32, 36
CAP_READ, CAP_WRITE, CAP_SUBS_READ, CAP_SUBS_WRITE, CAP_NO_EXPORT = 1, 2, 32, 64, 128
EBUSY = 16
PORT_TYPE = (1 << 1) | (1 << 20)          # generic MIDI, application
OPEN_DUPLEX, NONBLOCK = 3, 1
SUBSCRIBERS, UNKNOWN, QUEUE_DIRECT = 254, 253, 253
NOTE_LENGTH = 0.12                         # seconds an outgoing pad note is held
BASE_NOTE = 36                             # pad 1 (C1 on most controllers)
SKIP_CLIENTS = ("system", "midi through", "pocketpads")


class Event(ctypes.Structure):
    """snd_seq_event_t: 28 bytes. Only the fields used here are named."""
    _fields_ = [("type", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("tag", ctypes.c_ubyte),
                ("queue", ctypes.c_ubyte), ("time", ctypes.c_ubyte * 8),
                ("src_client", ctypes.c_ubyte), ("src_port", ctypes.c_ubyte),
                ("dst_client", ctypes.c_ubyte), ("dst_port", ctypes.c_ubyte),
                ("data", ctypes.c_ubyte * 12)]


def decode(ev_type, data):
    """Turn a sequencer event into ("note", n, velocity) / ("off", n) / ("cc", number, value) / ("pc", number) /
    ("mmc", command) / ("start",) / ("stop",)."""
    if ev_type == NOTE_ON:
        return ("note", data[1], data[2]) if data[2] else ("off", data[1])
    if ev_type == NOTE_OFF:
        return ("off", data[1])
    if ev_type == CONTROLLER:
        return ("cc", int.from_bytes(bytes(data[4:8]), "little"), int.from_bytes(bytes(data[8:12]), "little", signed=True))
    if ev_type == PROGRAM:
        return ("pc", int.from_bytes(bytes(data[8:12]), "little", signed=True) & 127)
    if ev_type == SYSEX:
        # The event holds a length and a pointer to the bytes, good until the next event is read.
        n, ptr = int.from_bytes(bytes(data[0:4]), "little"), int.from_bytes(bytes(data[4:12]), "little")
        return mmc(ctypes.string_at(ptr, n)) if ptr and 0 < n <= 64 else None
    if ev_type in (START, CONTINUE):
        return ("start",)
    if ev_type == STOP:
        return ("stop",)
    return None


def mmc(raw):
    """A transport button sent as MIDI Machine Control (F0 7F id 06 command F7): ("mmc", command).
    Commands: 1 stop, 2 play, 3 deferred play, 6 record, 7 record off, 9 pause."""
    if len(raw) >= 6 and raw[0] == 0xF0 and raw[1] == 0x7F and raw[3] == 0x06:
        return ("mmc", raw[4])
    return None


class MidiPort:
    def __init__(self, log=lambda msg: None):
        self.log = log
        self.events = deque(maxlen=512)    # decoded input, for the UI thread
        self.out = deque(maxlen=512)       # note-ons waiting to go: (due time, channel, note, velocity)
        self.devices = []                  # names of connected devices
        self.error = ""
        self.out_channel = 0               # 0 = sending off, 1-16 = channel
        self.latency = 0.046
        self.bpm, self.playing = 120.0, False
        self.lib = self.seq = None
        self.port = -1
        self.running = False
        self._linked = set()
        self._wake = threading.Event()     # set on shutdown so the thread stops waiting at once

    # ----- setup
    def start(self):
        """Open the sequencer. Returns False (with .error set) if MIDI is not available here."""
        try:
            name = ctypes.util.find_library("asound") or "libasound.so.2"
            lib = ctypes.CDLL(name)
            lib.snd_seq_client_info_get_name.restype = ctypes.c_char_p
            seq = ctypes.c_void_p()
            if lib.snd_seq_open(ctypes.byref(seq), b"default", OPEN_DUPLEX, NONBLOCK) < 0:
                raise OSError("the ALSA sequencer would not open")
            lib.snd_seq_set_client_name(seq, b"PocketPads")
            port = lib.snd_seq_create_simple_port(seq, b"PocketPads", CAP_READ | CAP_WRITE | CAP_SUBS_READ | CAP_SUBS_WRITE,
                                                  PORT_TYPE)
            if port < 0:
                raise OSError("could not create a MIDI port")
            self.lib, self.seq, self.port = lib, seq, port
            self.client = lib.snd_seq_client_id(seq)
        except (OSError, AttributeError) as e:
            self.log("midi unavailable: %s" % (str(e)[:200] or type(e).__name__))
            self.error = "not available on this system"
            return False
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True

    def scan(self):
        """Connect to every MIDI device present, both directions. Safe to call again and again."""
        lib, seq = self.lib, self.seq
        cinfo, pinfo = ctypes.c_void_p(), ctypes.c_void_p()
        lib.snd_seq_client_info_malloc(ctypes.byref(cinfo))
        lib.snd_seq_port_info_malloc(ctypes.byref(pinfo))
        found, names = set(), []
        try:
            lib.snd_seq_client_info_set_client(cinfo, -1)
            while lib.snd_seq_query_next_client(seq, cinfo) >= 0:
                client = lib.snd_seq_client_info_get_client(cinfo)
                cname = (lib.snd_seq_client_info_get_name(cinfo) or b"").decode("utf-8", "replace")
                if client == self.client or cname.lower().startswith(SKIP_CLIENTS):
                    continue
                lib.snd_seq_port_info_set_client(pinfo, client)
                lib.snd_seq_port_info_set_port(pinfo, -1)
                while lib.snd_seq_query_next_port(seq, pinfo) >= 0:
                    port = lib.snd_seq_port_info_get_port(pinfo)
                    caps = lib.snd_seq_port_info_get_capability(pinfo)
                    if caps & CAP_NO_EXPORT:            # the port asks not to be connected to
                        continue
                    usable = False
                    for way, need, connect in (("in", CAP_READ | CAP_SUBS_READ, lib.snd_seq_connect_from),
                                               ("out", CAP_WRITE | CAP_SUBS_WRITE, lib.snd_seq_connect_to)):
                        if caps & need != need:
                            continue
                        key = (client, port, way)
                        # A failed connection is left out of `found`, so the next scan tries it again.
                        if key in self._linked or connect(seq, self.port, client, port) in (0, -EBUSY):
                            found.add(key)
                            usable = True
                    if usable and cname not in names:
                        names.append(cname)
        finally:
            lib.snd_seq_client_info_free(cinfo)
            lib.snd_seq_port_info_free(pinfo)
        if names != self.devices:
            self.log("midi devices: %s" % (", ".join(names) or "none"))
        self._linked, self.devices = found, names

    # ----- output (called from the UI or audio thread; only queues)
    def note(self, pad, velocity=1.0, offset=0):
        ch = self.out_channel
        if ch and self.running:
            n = int(min(127, max(0, BASE_NOTE + pad + offset)))
            self.out.append((time.perf_counter() + self.latency, ch, n, int(min(127, max(1, velocity * 100)))))

    def _send(self, ev_type, channel=1, note=0, velocity=0):
        ev = Event()
        ev.type, ev.queue = ev_type, QUEUE_DIRECT
        ev.src_port, ev.dst_client, ev.dst_port = self.port, SUBSCRIBERS, UNKNOWN
        if ev_type in (NOTE_ON, NOTE_OFF):
            ev.data[0], ev.data[1], ev.data[2] = channel - 1, note, velocity
        self.lib.snd_seq_event_output_direct(self.seq, ctypes.byref(ev))

    # ----- the MIDI thread
    def _run(self):
        lib, seq = self.lib, self.seq
        evp = ctypes.POINTER(Event)()
        next_scan = 0.0
        clock_on, next_tick, start_at = False, 0.0, None
        offs = {}                                  # (channel, note) -> when its note-off is due
        while self.running:
            now = time.perf_counter()
            try:
                if now >= next_scan:
                    self.scan()
                    next_scan = now + 3.0
                while lib.snd_seq_event_input(seq, ctypes.byref(evp)) >= 0 and evp:
                    got = decode(evp.contents.type, evp.contents.data)
                    if got:
                        self.events.append(got)
                sending = bool(self.out_channel)
                # Clock: 24 ticks per beat while the sequencer runs, started and stopped with it.
                # It is timed by this computer's clock, not the sound card's, so it can drift slowly.
                want = sending and self.playing
                if want and not clock_on:
                    clock_on, start_at = True, now + self.latency
                elif clock_on and not want:
                    clock_on, start_at = False, None
                    self._send(STOP)
                if start_at is not None and now >= start_at:
                    self._send(START)
                    next_tick, start_at = now, None
                if clock_on and start_at is None and now >= next_tick:
                    self._send(CLOCK)
                    next_tick = max(next_tick + 60.0 / self.bpm / 24.0, now - 0.05)
                # Notes: a note-off always goes out on the channel its note-on used, and a note that is
                # played again before its note-off is due gets that note-off first.
                later = []
                while self.out:
                    item = self.out.popleft()
                    if not sending:
                        continue
                    if item[0] > now:
                        later.append(item)
                        continue
                    key = (item[1], item[2])
                    if key in offs:
                        self._send(NOTE_OFF, item[1], item[2])
                    self._send(NOTE_ON, item[1], item[2], item[3])
                    offs[key] = now + NOTE_LENGTH
                self.out.extend(later)
                for key in [k for k, due in offs.items() if due <= now or not sending]:
                    self._send(NOTE_OFF, key[0], key[1])
                    del offs[key]
            except Exception as e:                 # MIDI must never take the app down
                self.error = "%s: %s" % (type(e).__name__, e)
                self.log("midi error: %s" % self.error)
                self._wake.wait(1.0)
            self._wake.wait(0.002)
        try:                                       # leave nothing hanging on the gear we were driving
            for key in offs:
                self._send(NOTE_OFF, key[0], key[1])
            if clock_on:
                self._send(STOP)
        except Exception:
            pass

    def poll(self):
        """Incoming events since the last call."""
        out = []
        while self.events:
            out.append(self.events.popleft())
        return out

    def stop(self):
        self.running = False
        self._wake.set()
        thread = getattr(self, "thread", None)
        if thread is not None:
            thread.join(timeout=1.5)
            if thread.is_alive():              # never close the sequencer under a thread still using it
                return
        if self.seq:
            self.lib.snd_seq_close(self.seq)
            self.seq = None


# ---------------------------------------------------------------- Bluetooth MIDI
# Bluetooth LE controllers do not show up in the ALSA sequencer unless the system's Bluetooth service was
# built with MIDI support, and on KNULLI it is not. So PocketPads talks to the service (BlueZ) itself:
# it finds controllers that offer the Bluetooth MIDI service, connects, and reads their notes directly.
BLE_SERVICE = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
BLE_CHAR = "7772e5db-3868-4112-a1a9-f2669d106bf3"
_LENGTH = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2}


def ble_parse(packet):
    """Events in one Bluetooth MIDI packet, in the form decode() gives. A packet is a header byte, then
    messages that each start with a timestamp byte; a message may leave out its status (running status)."""
    out, i, n, status = [], 1, len(packet), 0
    while i < n:
        if packet[i] & 0x80:                        # a timestamp, then usually a status byte
            i += 1
            if i < n and packet[i] & 0x80:
                st = packet[i]
                i += 1
                if st >= 0xF8:                      # realtime: one byte, and running status carries on
                    if st in (0xFA, 0xFB):
                        out.append(("start",))
                    elif st == 0xFC:
                        out.append(("stop",))
                    continue
                status = st
                if st == 0xF0:                      # system exclusive: data up to the next timestamp, then F7
                    j = i
                    while j < n and not packet[j] & 0x80:
                        j += 1
                    got = mmc(bytes([0xF0]) + bytes(packet[i:j]) + b"\xf7")
                    if got:
                        out.append(got)
                    i, status = j, 0
                    continue
                if st == 0xF7:
                    status = 0
                    continue
        need = _LENGTH.get(status & 0xF0)
        if need is None or i + need > n:            # nothing we read, or cut short: skip to the next timestamp
            i += 1
            while i < n and not packet[i] & 0x80:
                i += 1
            continue
        a, b = packet[i], packet[i + 1] if need == 2 else 0
        i += need
        kind = status & 0xF0
        if kind == 0x90:
            out.append(("note", a, b) if b else ("off", a))
        elif kind == 0x80:
            out.append(("off", a))
        elif kind == 0xB0:
            out.append(("cc", a, b))
        elif kind == 0xC0:
            out.append(("pc", a))
    return out


class BleMidi:
    """Bluetooth controllers in. start() begins a search; .devices names what is connected; what they play
    lands in the same queue as USB controllers' events. Needs the system's dbus Python module and BlueZ."""
    SEARCH_SECONDS = 12

    def __init__(self, events, log=lambda msg: None):
        self.events, self.log = events, log
        self.devices = []                  # names of connected controllers
        self.status = "off"                # one line for the PROJ page
        self.error = ""
        self.running = False
        self.search_until = 0.0
        self._links = {}                   # device path -> {"name", "fd"}
        self.raw_left = int(os.environ.get("PP_MIDI_LOG", 0))      # packets still to be written to the log
        self._scanning = False             # we switched the system's search on
        self._wake = threading.Event()

    def start(self):
        try:
            import dbus
            self.dbus = dbus
            self.bus = dbus.SystemBus(private=True)
            self.om = dbus.Interface(self.bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
            self._objects()
        except Exception as e:             # no dbus module, no BlueZ, Bluetooth switched off in the system
            self.log("bluetooth midi unavailable: %s" % (str(e)[:200] or type(e).__name__))
            self.error = self.status = "not available: turn Bluetooth on in the system menu"
            return False
        self.running = True
        self.search()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True

    def search(self):
        """Look for controllers for a few seconds (called at start, from the PROJ page, and when one drops out)."""
        if self.running:
            self.search_until = time.time() + self.SEARCH_SECONDS
            self._wake.set()

    def _objects(self):
        return self.om.GetManagedObjects(timeout=5)

    def _iface(self, path, name):
        return self.dbus.Interface(self.bus.get_object("org.bluez", path), name)

    def _adapter(self, objects):
        for path, ifs in objects.items():
            if "org.bluez.Adapter1" in ifs and ifs["org.bluez.Adapter1"].get("Powered"):
                return path
        return None

    def _set_status(self):
        names = [l["name"] for l in self._links.values()]
        if names != self.devices:
            self.log("bluetooth midi devices: %s" % (", ".join(names) or "none"))
        self.devices = names
        if time.time() < self.search_until:
            self.status = (", ".join(names) + "  -  " if names else "") + "searching..."
        else:
            self.status = ", ".join(names) or self.error or "none connected  -  A to search"

    def _link(self, path, dev, objects):
        """Open the MIDI stream of a connected controller."""
        for p, ifs in objects.items():
            ch = ifs.get("org.bluez.GattCharacteristic1")
            if ch and p.startswith(path + "/") and str(ch["UUID"]).lower() == BLE_CHAR:
                fd, _mtu = self._iface(p, "org.bluez.GattCharacteristic1").AcquireNotify({}, timeout=10)
                fd = fd.take()
                os.set_blocking(fd, False)
                self._links[path] = {"name": str(dev.get("Alias") or dev.get("Name") or "Bluetooth MIDI"), "fd": fd}
                self._trust(path, True)             # while we run, the system reconnects it by itself if it drops
                return

    def _trust(self, path, on):
        try:
            self._iface(path, "org.freedesktop.DBus.Properties").Set("org.bluez.Device1", "Trusted", self.dbus.Boolean(on), timeout=5)
        except Exception as e:
            self.log("bluetooth trust: %s" % str(e)[:120])

    def _look(self):
        """One pass: open the controllers that are connected, connect one that is in range.
        Returns True while a connection is being set up, so the next pass comes soon."""
        objects = self._objects()
        adapter = self._adapter(objects)
        if not adapter:
            self.error = "Bluetooth is off: turn it on in the system menu"
            return False
        self.error = ""
        searching = time.time() < self.search_until
        waiting, found = False, []
        for path, ifs in objects.items():
            dev = ifs.get("org.bluez.Device1")
            if not dev or path in self._links or BLE_SERVICE not in [str(u).lower() for u in dev.get("UUIDs", [])]:
                continue
            if dev.get("Connected") and dev.get("ServicesResolved"):
                try:
                    self._link(path, dev, objects)
                    self.search_until = max(self.search_until, time.time() + 6)    # time to find a second one
                except Exception as e:              # it dropped out again in the meantime: the next pass retries
                    self.log("bluetooth open %s: %s" % (dev.get("Alias"), str(e)[:120]))
                    waiting = True
            elif dev.get("Connected"):
                waiting = True                      # connected, still listing what it offers
            elif searching and "RSSI" in dev:       # RSSI: it was seen in this search
                found.append((path, dev))
        # The radio does one thing at a time: searching while connecting, or connecting two controllers
        # at once, takes many seconds or fails. So: finish one connection, then search on for the next.
        ad = self._iface(adapter, "org.bluez.Adapter1")
        want_scan = searching and not waiting and not found
        if want_scan and not objects[adapter]["org.bluez.Adapter1"].get("Discovering"):
            try:
                ad.SetDiscoveryFilter({"Transport": "le", "UUIDs": [BLE_SERVICE]}, timeout=5)
                ad.StartDiscovery(timeout=5)
                self._scanning = True
            except Exception as e:
                self.log("bluetooth search: %s" % str(e)[:120])
        elif not want_scan and self._scanning:
            self._scanning = False
            try:
                ad.StopDiscovery(timeout=5)
            except Exception:
                pass
        if found and not waiting:
            path, dev = found[0]
            try:
                self._iface(path, "org.bluez.Device1").Connect(timeout=12)
            except Exception as e:
                self.log("bluetooth connect %s: %s" % (dev.get("Alias"), str(e)[:120]))
            return True
        return waiting

    def _run(self):
        import select
        next_look = 0.0
        while self.running:
            now = time.time()
            try:
                if now >= next_look:
                    soon = self._look()
                    next_look = time.time() + (0.5 if soon else 1.0 if time.time() < self.search_until else 5.0)
                    self._set_status()
                fds = {l["fd"]: path for path, l in self._links.items()}
                if not fds:
                    self._wake.wait(0.5)
                    if self._wake.is_set():
                        self._wake.clear()
                        next_look = 0.0
                    continue
                ready = select.select(list(fds), [], [], 0.25)[0]
                for fd in ready:
                    try:
                        data = os.read(fd, 512)
                    except BlockingIOError:
                        continue
                    except OSError:
                        data = b""
                    if not data:                    # the controller went away: look for it again
                        os.close(fd)
                        del self._links[fds[fd]]
                        self.search()
                        next_look = 0.0
                        continue
                    if self.raw_left > 0:           # the first packets go to the log, to learn a new controller
                        self.raw_left -= 1
                        self.log("bluetooth raw %s: %s" % (self._links[fds[fd]]["name"], data.hex(" ")))
                    self.events.extend(ble_parse(data))
                if self._wake.is_set():
                    self._wake.clear()
                    next_look = 0.0
            except Exception as e:                  # Bluetooth must never take the app down
                self.error = "%s: %s" % (type(e).__name__, str(e)[:80])
                self.log("bluetooth midi error: %s" % self.error)
                self._wake.wait(2.0)

    def stop(self):
        self.running = False
        self._wake.set()
        thread = getattr(self, "thread", None)
        if thread is None:
            return
        thread.join(timeout=2.0)
        try:
            # A connection of its own: the thread may still be inside a slow call on the other one.
            bus = self.dbus.SystemBus(private=True)
            objects = self.dbus.Interface(bus.get_object("org.bluez", "/"),
                                          "org.freedesktop.DBus.ObjectManager").GetManagedObjects(timeout=5)
            for path, ifs in objects.items():
                if "org.bluez.Adapter1" in ifs and ifs["org.bluez.Adapter1"].get("Discovering") and self._scanning:
                    try:
                        self.dbus.Interface(bus.get_object("org.bluez", path), "org.bluez.Adapter1").StopDiscovery(timeout=5)
                    except Exception:
                        pass
                dev = ifs.get("org.bluez.Device1")
                if dev and dev.get("Connected") and BLE_SERVICE in [str(u).lower() for u in dev.get("UUIDs", [])]:
                    obj = bus.get_object("org.bluez", path)
                    # Not trusted any more, or the system would connect it again behind our back.
                    self.dbus.Interface(obj, "org.freedesktop.DBus.Properties").Set(
                        "org.bluez.Device1", "Trusted", self.dbus.Boolean(False), timeout=5)
                    self.dbus.Interface(obj, "org.bluez.Device1").Disconnect(timeout=5)   # frees it for other gear
            bus.close()
        except Exception as e:
            self.log("bluetooth stop: %s" % str(e)[:120])
