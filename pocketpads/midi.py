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
import threading
import time
from collections import deque

NOTE_ON, NOTE_OFF, CONTROLLER = 6, 7, 10
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
    """Turn a sequencer event into ("note", n, velocity) / ("off", n) / ("cc", number, value) / ("start",) / ("stop",)."""
    if ev_type == NOTE_ON:
        return ("note", data[1], data[2]) if data[2] else ("off", data[1])
    if ev_type == NOTE_OFF:
        return ("off", data[1])
    if ev_type == CONTROLLER:
        return ("cc", int.from_bytes(bytes(data[4:8]), "little"), int.from_bytes(bytes(data[8:12]), "little", signed=True))
    if ev_type in (START, CONTINUE):
        return ("start",)
    if ev_type == STOP:
        return ("stop",)
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
