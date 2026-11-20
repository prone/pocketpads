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
CAP_READ, CAP_WRITE, CAP_SUBS_READ, CAP_SUBS_WRITE = 1, 2, 32, 64
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
        self.out = deque(maxlen=512)       # (due time, event type, channel, note, velocity)
        self.devices = []                  # names of connected devices
        self.error = ""
        self.out_channel = 0               # 0 = sending off, 1-16 = channel
        self.latency = 0.046
        self.bpm, self.playing = 120.0, False
        self.lib = self.seq = None
        self.port = -1
        self.running = False
        self._linked = set()

    # ----- setup
    def start(self):
        """Open the sequencer. Returns False (with .error set) if MIDI is not available here."""
        try:
            name = ctypes.util.find_library("asound") or "libasound.so.2"
            lib = ctypes.CDLL(name)
            for fn in ("snd_seq_port_info_get_name", "snd_seq_client_info_get_name"):
                getattr(lib, fn).restype = ctypes.c_char_p
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
                    usable = False
                    if caps & CAP_READ and caps & CAP_SUBS_READ:
                        usable = True
                        if (client, port, "in") not in self._linked:
                            lib.snd_seq_connect_from(seq, self.port, client, port)
                        found.add((client, port, "in"))
                    if caps & CAP_WRITE and caps & CAP_SUBS_WRITE:
                        usable = True
                        if (client, port, "out") not in self._linked:
                            lib.snd_seq_connect_to(seq, self.port, client, port)
                        found.add((client, port, "out"))
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
        if self.out_channel and self.running:
            n = int(min(127, max(0, BASE_NOTE + pad + offset)))
            due = time.perf_counter() + self.latency
            self.out.append((due, NOTE_ON, n, int(min(127, max(1, velocity * 100)))))
            self.out.append((due + NOTE_LENGTH, NOTE_OFF, n, 0))

    def _send(self, ev_type, note=0, velocity=0):
        ev = Event()
        ev.type, ev.queue = ev_type, QUEUE_DIRECT
        ev.src_port, ev.dst_client, ev.dst_port = self.port, SUBSCRIBERS, UNKNOWN
        if ev_type in (NOTE_ON, NOTE_OFF):
            ev.data[0], ev.data[1], ev.data[2] = self.out_channel - 1, note, velocity
        self.lib.snd_seq_event_output_direct(self.seq, ctypes.byref(ev))

    # ----- the MIDI thread
    def _run(self):
        lib, seq = self.lib, self.seq
        evp = ctypes.POINTER(Event)()
        next_scan = 0.0
        was_playing, next_tick, pending = False, 0.0, []
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
                if self.out_channel:
                    # Clock: 24 ticks per beat while the sequencer runs, started and stopped with it.
                    if self.playing and not was_playing:
                        pending.append((now + self.latency, START))
                        next_tick = now + self.latency
                    elif was_playing and not self.playing:
                        pending.append((now + self.latency, STOP))
                    was_playing = self.playing
                    while pending and pending[0][0] <= now:
                        self._send(pending.pop(0)[1])
                    if self.playing and now >= next_tick:
                        self._send(CLOCK)
                        next_tick = max(next_tick + 60.0 / self.bpm / 24.0, now - 0.05)
                    late = []
                    while self.out:
                        item = self.out.popleft()
                        if item[0] <= now:
                            self._send(item[1], item[2], item[3])
                        else:
                            late.append(item)
                    self.out.extend(late)
                else:
                    was_playing = False
                    self.out.clear()
            except Exception as e:                 # MIDI must never take the app down
                self.error = "%s: %s" % (type(e).__name__, e)
                self.log("midi error: %s" % self.error)
                time.sleep(1.0)
            time.sleep(0.001)

    def poll(self):
        """Incoming events since the last call."""
        out = []
        while self.events:
            out.append(self.events.popleft())
        return out

    def stop(self):
        self.running = False
        thread = getattr(self, "thread", None)
        if thread is not None:
            thread.join(timeout=0.5)
        if self.seq:
            if self.out_channel:
                try:
                    self._send(STOP)
                except Exception:
                    pass
            self.lib.snd_seq_close(self.seq)
            self.seq = None
