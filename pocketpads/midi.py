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


class EventQueue(deque):
    """The queue of decoded input. Whatever thread adds to it also raises .arrived, so the screen thread can
    act on a pad hit at once instead of at its next frame (up to 33 ms later), and .at says when it came."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.arrived = threading.Event()
        self.at = 0.0

    def append(self, item):
        super().append(item)
        self.at = time.time()
        self.arrived.set()

    def extend(self, items):
        items = list(items)
        if items:
            super().extend(items)
            self.at = time.time()
            self.arrived.set()


class MidiPort:
    def __init__(self, log=lambda msg: None):
        self.log = log
        self.events = EventQueue(maxlen=512)    # decoded input, for the UI thread
        self.out = deque(maxlen=512)       # note-ons waiting to go: (due time, channel, note, velocity)
        self.devices = []                  # names of connected devices
        self.error = ""
        self.out_channel = 0               # 0 = sending off, 1-16 = channel
        self.latency = 0.046
        self.bpm, self.playing = 120.0, False
        self.lib = self.seq = None
        self.java = None                   # on Android: the Java half, which does the sending
        self.port = -1
        self.running = False
        self._linked = set()
        self._wake = threading.Event()     # set on shutdown so the thread stops waiting at once

    # ----- setup
    def start(self):
        """Open the sequencer. Returns False (with .error set) if MIDI is not available here."""
        try:
            try:
                from ctypes.util import find_library    # here, not at the top: on Android this import fails
                name = find_library("asound")
            except ImportError:
                name = None
            name = name or "libasound.so.2"
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
            if not self._start_android():
                self.log("midi unavailable: %s" % (str(e)[:200] or type(e).__name__))
                self.error = "not available on this system"
                return False
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True

    def _start_android(self):
        """On Android there is no sequencer: AndroidMidi reads the controllers, and this port only sends."""
        if "ANDROID_ARGUMENT" not in os.environ:
            return False
        try:
            from jnius import autoclass
            java = autoclass("io.github.prone.pocketpads.PPMidi")
            if java.start(autoclass("org.kivy.android.PythonActivity").mActivity):
                return False
            self.java = java
            return True
        except Exception as e:
            self.log("android midi out: %s" % str(e)[:160])
            return False

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
        if self.java is not None:
            if ev_type in (NOTE_ON, NOTE_OFF):
                msg = bytes(((0x90 if ev_type == NOTE_ON else 0x80) | (channel - 1), note, velocity))
            else:
                msg = bytes(({START: 0xFA, STOP: 0xFC, CLOCK: 0xF8}[ev_type],))
            self.java.send(msg)
            return
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
                if now >= next_scan and lib is not None:
                    self.scan()
                    next_scan = now + 3.0
                while lib is not None and lib.snd_seq_event_input(seq, ctypes.byref(evp)) >= 0 and evp:
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
        if self.java is not None:
            try:
                import jnius
                jnius.detach()                     # a thread that called into Java has to say goodbye to it
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


class RawMidi:
    """Events in a stream of plain MIDI bytes (as a USB device or Android delivers them), in the form decode()
    gives. Keeps the running status and a system exclusive message that is split across calls."""

    def __init__(self):
        self.status, self.data, self.sysex = 0, [], None

    def feed(self, raw):
        out = []
        for b in raw:
            if b >= 0xF8:                           # realtime: may come in the middle of anything
                if b in (0xFA, 0xFB):
                    out.append(("start",))
                elif b == 0xFC:
                    out.append(("stop",))
            elif b == 0xF0:
                self.sysex, self.status = [0xF0], 0
            elif self.sysex is not None:
                if b & 0x80:                        # F7, or a status byte that cuts it short
                    got = mmc(bytes(self.sysex + [0xF7])) if b == 0xF7 else None
                    if got:
                        out.append(got)
                    self.sysex = None
                    if b != 0xF7:
                        self.status, self.data = (b if b < 0xF0 else 0), []
                elif len(self.sysex) < 64:
                    self.sysex.append(b)
            elif b & 0x80:
                self.status, self.data = (b if b < 0xF0 else 0), []
            elif self.status:
                self.data.append(b)
                need = _LENGTH.get(self.status & 0xF0)
                if need is None:
                    self.data = []
                elif len(self.data) == need:
                    a, v = self.data[0], self.data[1] if need == 2 else 0
                    self.data = []
                    kind = self.status & 0xF0
                    if kind == 0x90:
                        out.append(("note", a, v) if v else ("off", a))
                    elif kind == 0x80:
                        out.append(("off", a))
                    elif kind == 0xB0:
                        out.append(("cc", a, v))
                    elif kind == 0xC0:
                        out.append(("pc", a))
        return out


class AndroidMidi:
    """Controllers on Android, USB and Bluetooth, through the system's own MIDI service. Stands in for BleMidi:
    the same start(), search(), stop(), flash(), .devices and .status, and events land in the same queue. The
    Java half is android/java/.../PPMidi.java. Notes and clock out go through MidiPort, which uses it too."""
    SEARCH_SECONDS = 12

    def __init__(self, events, log=lambda msg: None):
        self.events, self.log = events, log
        self.devices, self.status, self.error = [], "off", ""
        self.running = False
        self.search_until = 0.0
        self._want_scan = False
        self._note = ""
        self._lights = {}                           # Bluetooth address -> AndroidPadLights
        self.raw_left = int(os.environ.get("PP_MIDI_LOG", 12 if "ANDROID_ARGUMENT" in os.environ else 0))

    def start(self):
        try:
            from jnius import autoclass
            self.java = autoclass("io.github.prone.pocketpads.PPMidi")
            self.activity = autoclass("org.kivy.android.PythonActivity").mActivity
            why = self.java.start(self.activity)
            if why:
                raise OSError(why)
        except Exception as e:
            self.log("android midi unavailable: %s" % (str(e)[:200] or type(e).__name__))
            self.error = self.status = "not available on this device"
            return False
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.search()
        return True

    def search(self):
        """Look for Bluetooth controllers for a few seconds. Asks for the Bluetooth permission the first time."""
        if not self.running:
            return
        self.search_until = time.time() + self.SEARCH_SECONDS
        try:
            from android.permissions import check_permission, request_permissions
            need = ["android.permission.BLUETOOTH_SCAN", "android.permission.BLUETOOTH_CONNECT",
                    "android.permission.ACCESS_FINE_LOCATION"]
            missing = [p for p in need[:2] if not check_permission(p)]
            if missing:
                # The answer comes on another thread; the scan starts from ours once it is yes.
                request_permissions(need, lambda perms, grants: setattr(self, "_want_scan", True))
                return
        except Exception as e:                      # an older Android without these: just try
            self.log("android midi permissions: %s" % str(e)[:120])
        self._want_scan = True

    def flash(self, i, block=None):
        """Flash pad i (0-15) of every SMC-PAD: in the bank of pads it is showing if we know it, else in both."""
        for lights in list(self._lights.values()):
            for b in ((block % 2,) if block is not None else (0, 1)):
                lights.flash(b * 16 + i)

    def _look_lights(self):
        """Start the lights of an SMC-PAD that has connected, and forget one that has gone."""
        there = dict(line.split("\t", 1) for line in str(self.java.lights()).split("\n") if "\t" in line)
        for addr in [a for a, l in self._lights.items() if a not in there or not l.thread.is_alive()]:
            self._lights.pop(addr).stop()
        for addr, name in there.items():
            if addr not in self._lights and smc_name(name):
                self._lights[addr] = AndroidPadLights(self.java, addr, name, self.log)

    def _run(self):
        parser, last_names = RawMidi(), 0.0
        try:
            while self.running:
                raw = self.java.poll()
                if raw is not None:
                    data = bytes(b & 255 for b in raw)
                    if self.raw_left > 0:           # the first packets go to the log, to learn a new controller
                        self.raw_left -= 1
                        self.log("android midi raw: %s" % data.hex(" "))
                    self.events.extend(parser.feed(data))
                    continue                        # there may be more right behind it
                raw = self.java.pollBle()            # Bluetooth packets, each behind two bytes of length
                if raw is not None:
                    data, i = bytes(b & 255 for b in raw), 0
                    while i + 2 <= len(data):
                        n = data[i] | data[i + 1] << 8
                        packet = data[i + 2:i + 2 + n]
                        i += 2 + n
                        if self.raw_left > 0:
                            self.raw_left -= 1
                            self.log("android midi bluetooth raw: %s" % packet.hex(" "))
                        self.events.extend(ble_parse(packet))
                    continue
                now = time.time()
                if self._want_scan:
                    self._want_scan = False
                    why = self.java.scan(self.SEARCH_SECONDS)
                    self.log("android midi: searching%s" % (" - " + why if why else ""))
                    if why:
                        self.log("android midi search: %s" % why)
                        self.error, self.search_until = why, 0.0
                    else:
                        self.error = ""
                if now - last_names > 0.5:
                    last_names = now
                    note = str(self.java.note())
                    if note != self._note:          # what the Java half has to say, once each
                        self._note = note
                        self.log("android midi: %s" % note[:300])
                    self._look_lights()
                    names = [n for n in str(self.java.devices()).split("\n") if n]
                    if names != self.devices:
                        self.log("android midi devices: %s" % (", ".join(names) or "none"))
                        self.devices = names
                    if now < self.search_until:
                        self.status = (", ".join(names) + "  -  " if names else "") + "searching..."
                    else:
                        self.status = ", ".join(names) or self.error or "none connected  -  A to search"
                time.sleep(0.003)
        except Exception as e:                      # MIDI must never take the app down
            self.error = "%s: %s" % (type(e).__name__, str(e)[:80])
            self.log("android midi error: %s" % self.error)
        finally:
            try:
                import jnius
                jnius.detach()                      # a thread that called into Java has to say goodbye to it
            except Exception:
                pass

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.thread.join(timeout=2.0)
        for lights in list(self._lights.values()):  # first, so no pad is left white
            lights.stop()
        self._lights = {}
        try:
            self.java.stop()
        except Exception as e:
            self.log("android midi stop: %s" % str(e)[:120])


# The SMC-PAD's pad lights do not answer MIDI. Its maker's app sets them over a second Bluetooth channel, and
# so do we. The protocol was worked out by the smc-pad project (github.com/spectalive/smc-pad, Apache-2.0):
# a packet is 00 59 <command> <length, 3 bytes> <data> <checksum>; the pad takes colour writes only after its
# whole settings block has been read once; a read twice a second keeps that session open. Colours are written
# to the pad's working memory only. We never send its "save" command, so its own colours come back at power-on.
SMC_WRITE, SMC_NOTIFY = "0000ae41", "0000ae42"       # start of the two characteristics' UUIDs
SMC_SETTINGS, SMC_CHUNK = 28312, 1009               # size of the settings block, and how much one read asks for
SMC_FIRST, SMC_STEP, SMC_PADS = 0x414, 26, 32       # where the pads' records are: two banks of sixteen


def smc_packet(cmd, data=b""):
    data = bytes(data)
    return bytes([0, 0x59, cmd]) + len(data).to_bytes(3, "little") + data + bytes([~sum(data) & 255])


def smc_read(region, addr, n):
    return smc_packet(0x23, bytes([region]) + addr.to_bytes(4, "little") + n.to_bytes(3, "little"))


def smc_colour(addr, rgb):
    return smc_packet(0x22, bytes([5]) + addr.to_bytes(4, "little") + bytes([3, 0, 0]) + bytes(rgb))


def smc_pads(settings):
    """[(address of the colour, (r, g, b))] for the 32 pads in the order they sit on the pad, bank 1 first;
    [] when the settings do not look the way we know them (other firmware: better no lights than a wrong write)."""
    out = []
    for k in range(SMC_PADS):
        i = SMC_FIRST + k * SMC_STEP
        rec = settings[i:i + 8]
        if len(rec) < 8 or rec[0] != 9 or rec[3] != 0x7F or rec[7] != 0xFF:
            return []
        out.append((i + 4, tuple(rec[4:7])))
    return out


def smc_name(name):
    """Is this the full-size SMC-PAD (the Pocket has no knobs or transport, and its lights are untried)?"""
    return "smc" in name.lower() and "pocket" not in name.lower()


class PadLights:
    """Flashes the pads of one connected SMC-PAD. flash(k) is safe from any thread; all the Bluetooth is in ours."""
    FLASH = 0.12                           # seconds a pad stays white
    WHITE = (255, 255, 255)

    def __init__(self, dbus, path, name, log):
        self.dbus, self.path, self.name, self.log = dbus, path, name, log
        self.todo = deque(maxlen=64)
        self.ready = False
        self.running = True
        self._wake = os.pipe()
        for fd in self._wake:
            os.set_blocking(fd, False)
        self.rfd = self.wfd = self.bus = self.char = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def flash(self, k):
        if self.ready:
            self.todo.append(k)
            self._poke()

    def stop(self):
        self.running = False
        self._poke()
        self.thread.join(timeout=2.0)

    def _poke(self):
        try:
            os.write(self._wake[1], b"x")
        except OSError:
            pass

    def _open(self):
        dbus = self.dbus
        self.bus = dbus.SystemBus(private=True)
        objects = dbus.Interface(self.bus.get_object("org.bluez", "/"),
                                 "org.freedesktop.DBus.ObjectManager").GetManagedObjects(timeout=5)
        chars = {}
        for p, ifs in objects.items():
            ch = ifs.get("org.bluez.GattCharacteristic1")
            if ch and p.startswith(self.path + "/"):
                chars[str(ch["UUID"]).lower()[:8]] = dbus.Interface(self.bus.get_object("org.bluez", p),
                                                                    "org.bluez.GattCharacteristic1")
        if SMC_WRITE not in chars or SMC_NOTIFY not in chars:
            return False
        fd, _mtu = chars[SMC_NOTIFY].AcquireNotify({}, timeout=10)
        self.rfd = fd.take()
        os.set_blocking(self.rfd, False)
        self.char = chars[SMC_WRITE]
        try:                               # a pipe of our own to the pad: no trip through the system per colour
            fd, _mtu = self.char.AcquireWrite({}, timeout=10)
            self.wfd = fd.take()
        except Exception:
            self.wfd = None
        return True

    def _send(self, packet):
        if self.wfd is not None:
            os.write(self.wfd, packet)
        else:
            self.char.WriteValue(self.dbus.Array([self.dbus.Byte(b) for b in packet], signature="y"),
                                 {"type": "command"}, timeout=5)

    def _drain(self):
        """What the pad sent since last time; None when it has gone away."""
        got = b""
        while True:
            try:
                data = os.read(self.rfd, 1024)
            except BlockingIOError:
                return got
            except OSError:
                return None
            if not data:
                return None
            got += data

    def _wait(self, timeout):
        """Sleep until the pad says something, flash() or stop() wakes us, or the time is up."""
        import select
        for fd in select.select([self.rfd, self._wake[0]], [], [], max(0.0, timeout))[0]:
            if fd != self.rfd:
                try:
                    os.read(fd, 64)
                except OSError:
                    pass

    def _close(self):
        # Close our ends before anything else: the system's Bluetooth service has fallen over when a
        # program left with these still open.
        for fd in (self.wfd, self.rfd) + self._wake:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.wfd = self.rfd = None
        try:
            if self.bus is not None:
                self.bus.close()
        except Exception:
            pass

    def _ask(self, packet, want, timeout=3.0):
        self._drain()
        self._send(packet)
        got, end = b"", time.time() + timeout
        while len(got) < want and self.running:
            left = end - time.time()
            if left <= 0:
                raise OSError("no answer from the pad")
            self._wait(left)
            data = self._drain()
            if data is None:
                raise OSError("the pad went away")
            got += data
        return got

    def _settings(self):
        """Read the pad's settings. This is also what makes it accept colours afterwards."""
        self._ask(smc_packet(0x11), 7)
        self._ask(smc_read(4, 0, 12), 27)
        out = b""
        while len(out) < SMC_SETTINGS and self.running:
            n = min(SMC_CHUNK, SMC_SETTINGS - len(out))
            got = self._ask(smc_read(5, len(out), n), 15 + n)      # 6 of header, our 8 back, the bytes, checksum
            if got[:3] != bytes([0, 0x59, 0x23]):
                raise OSError("an answer we do not know")
            out += got[14:14 + n]
        return out

    def _run(self):
        lit = {}                           # pad -> when it goes back to its own colour
        pads = []
        try:
            if not self._open():
                return
            pads = smc_pads(self._settings())
            if not pads:
                self.log("pad lights %s: settings not as expected, lights left alone" % self.name)
                return
            self.log("pad lights %s: ready" % self.name)
            self.ready = True
            poll = time.time() + 0.5
            while self.running:
                now = time.time()
                while self.todo:
                    k = self.todo.popleft()
                    if 0 <= k < len(pads):
                        if k not in lit:
                            self._send(smc_colour(pads[k][0], self.WHITE))
                        lit[k] = now + self.FLASH
                for k in [k for k, t in lit.items() if now >= t]:
                    self._send(smc_colour(*pads[k]))
                    del lit[k]
                if now >= poll:
                    self._send(smc_read(4, 0, 12))
                    poll = now + 0.5
                if not self.todo:
                    self._wait(min([poll] + list(lit.values())) - time.time())
                if self._drain() is None:  # the pad has gone
                    self.running = False
        except Exception as e:             # lights are a nicety: never let them hurt the playing
            self.log("pad lights %s: %s: %s" % (self.name, type(e).__name__, str(e)[:120]))
        finally:
            self.ready = False
            try:
                for k in lit:              # leave no pad white behind
                    self._send(smc_colour(*pads[k]))
            except Exception:
                pass
            self._close()


class AndroidPadLights(PadLights):
    """The same lights, reached through the Java half's connection to the pad instead of the Linux Bluetooth service."""

    def __init__(self, java, addr, name, log):
        self.java, self.addr, self.name, self.log = java, addr, name, log
        self.todo = deque(maxlen=64)
        self.ready = False
        self.running = True
        self._event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _poke(self):
        self._event.set()

    def _open(self):
        return True

    def _send(self, packet):
        if not self.java.smcWrite(self.addr, bytes(packet)):
            raise OSError("the pad went away")

    def _drain(self):
        raw = self.java.smcPoll(self.addr)
        return None if raw is None else bytes(b & 255 for b in raw)

    def _wait(self, timeout):
        self._event.wait(min(max(0.0, timeout), 0.01))     # the pad's answers are collected, not announced
        self._event.clear()

    def _close(self):
        try:
            import jnius
            jnius.detach()
        except Exception:
            pass


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
        self._links = {}                   # device path -> {"name", "fd", "lights"}
        self._errors = 0
        self.raw_left = int(os.environ.get("PP_MIDI_LOG", 0))      # packets still to be written to the log
        self._scanning = False             # we switched the system's search on
        self._wake = threading.Event()

    def _bus(self):
        self.bus = self.dbus.SystemBus(private=True)
        self.om = self.dbus.Interface(self.bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
        self._objects()

    def start(self):
        try:
            import dbus
            self.dbus = dbus
            self._bus()
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
                name = str(dev.get("Alias") or dev.get("Name") or "Bluetooth MIDI")
                # The full-size SMC-PAD's pads can be lit; the Pocket has not been tried.
                lights = PadLights(self.dbus, path, name, self.log) if smc_name(name) else None
                self._links[path] = {"name": name, "fd": fd, "lights": lights}
                self._trust(path, True)             # while we run, the system reconnects it by itself if it drops
                return

    def _drop(self, path):
        link = self._links.pop(path)
        if link["lights"]:
            link["lights"].stop()
        try:
            os.close(link["fd"])
        except OSError:
            pass

    def flash(self, i, block=None):
        """Flash pad i (0-15) of every SMC-PAD: in the bank of pads it is showing if we know it, else in both."""
        for link in list(self._links.values()):
            if link["lights"]:
                for b in ((block % 2,) if block is not None else (0, 1)):
                    link["lights"].flash(b * 16 + i)

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
                        self._drop(fds[fd])
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
                self._errors = 0
            except Exception as e:                  # Bluetooth must never take the app down
                self.error = "%s: %s" % (type(e).__name__, str(e)[:80])
                self._errors += 1
                if self._errors <= 3:               # not a line every two seconds for as long as it lasts
                    self.log("bluetooth midi error: %s" % self.error)
                self._wake.wait(2.0)
                if self._errors >= 2 and self.running:
                    # The system's Bluetooth service was restarted: what we held belongs to the old one.
                    for path in list(self._links):
                        self._drop(path)
                    try:
                        self.bus.close()
                    except Exception:
                        pass
                    try:
                        self._bus()
                        self.search()
                        next_look = 0.0
                    except Exception:
                        pass                        # not back yet: the next round tries again

    def stop(self):
        self.running = False
        self._wake.set()
        thread = getattr(self, "thread", None)
        if thread is None:
            return
        thread.join(timeout=2.0)
        for path in list(self._links):              # lights back to their own colours, and our ends closed
            self._drop(path)
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
