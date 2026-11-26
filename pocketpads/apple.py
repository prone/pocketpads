"""The parts of PocketPads that use Apple's own libraries, on the iPad and the Mac alike: reading compressed
audio files (AudioToolbox) and MIDI in and out (Core MIDI). Both are driven directly with ctypes, as the
handheld's ALSA is, so no extra package is needed. Nothing here is imported on any other system."""
import ctypes
import os
import platform
import struct

_FW = "/System/Library/Frameworks/%s.framework/%s"
_UTF8 = 0x08000100


def _lib(name):
    return ctypes.CDLL(_FW % (name, name))


def _cf():
    cf = _lib("CoreFoundation")
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    return cf


# ---------------------------------------------------------------- compressed files
class _Format(ctypes.Structure):
    _fields_ = [("rate", ctypes.c_double), ("id", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("bytes_per_packet", ctypes.c_uint32), ("frames_per_packet", ctypes.c_uint32),
                ("bytes_per_frame", ctypes.c_uint32), ("channels", ctypes.c_uint32),
                ("bits", ctypes.c_uint32), ("reserved", ctypes.c_uint32)]


class _Buffer(ctypes.Structure):
    _fields_ = [("channels", ctypes.c_uint32), ("size", ctypes.c_uint32), ("data", ctypes.c_void_p)]


class _BufferList(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("buffer", _Buffer)]


def decode(src, dst, seconds, rate=44100):
    """Write the first `seconds` of an MP3, M4A, AAC, AIFF or FLAC file to `dst` as a 16-bit stereo WAV.
    Returns "" when it worked, or a few words saying why not."""
    cf, at = _cf(), _lib("AudioToolbox")
    at.ExtAudioFileOpenURL.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    at.ExtAudioFileSetProperty.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    at.ExtAudioFileRead.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(_BufferList)]
    at.ExtAudioFileDispose.argtypes = [ctypes.c_void_p]
    raw = os.fsencode(src)
    url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
    ref = ctypes.c_void_p()
    try:
        if not url or at.ExtAudioFileOpenURL(url, ctypes.byref(ref)) != 0 or not ref:
            return "not an audio file this device can read"
        fmt = _Format(float(rate), 0x6C70636D, 0xC, 4, 1, 4, 2, 16, 0)      # 'lpcm', signed and packed
        if at.ExtAudioFileSetProperty(ref, 0x63666D74, ctypes.sizeof(fmt), ctypes.byref(fmt)) != 0:   # 'cfmt'
            return "could not convert this file"
        chunk = 16384
        data = ctypes.create_string_buffer(chunk * 4)
        left, size = int(seconds * rate), 0
        with open(dst, "wb") as f:
            f.write(b"\0" * 44)
            while left > 0:
                frames = ctypes.c_uint32(min(chunk, left))
                bl = _BufferList(1, _Buffer(2, frames.value * 4, ctypes.cast(data, ctypes.c_void_p)))
                if at.ExtAudioFileRead(ref, ctypes.byref(frames), ctypes.byref(bl)) != 0:
                    return "the file could not be read to the end"
                if frames.value == 0:
                    break
                f.write(data.raw[:frames.value * 4])
                size += frames.value * 4
                left -= frames.value
            f.seek(0)
            f.write(b"RIFF" + struct.pack("<I", 36 + size) + b"WAVEfmt ")
            f.write(struct.pack("<IHHIIHH", 16, 1, 2, rate, rate * 4, 4, 16))
            f.write(b"data" + struct.pack("<I", size))
        return "" if size else "the file has no sound in it"
    finally:
        if ref:
            at.ExtAudioFileDispose(ref)
        if url:
            cf.CFRelease(url)


# ---------------------------------------------------------------- MIDI
_READ = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_ALIGNED = platform.machine().lower() not in ("x86_64", "i386")     # packets are padded to 4 bytes on ARM


def packets(address):
    """The data of each packet in a Core MIDI packet list, given its address."""
    count = ctypes.c_uint32.from_address(address).value
    at, out = address + 4, []
    for _ in range(count):
        n = ctypes.c_uint16.from_address(at + 8).value
        out.append(ctypes.string_at(at + 10, n))
        at += 10 + n
        if _ALIGNED:
            at = (at + 3) & ~3
    return out


def packet_list(data):
    """One message as a Core MIDI packet list, to be sent now."""
    return ctypes.create_string_buffer(struct.pack("<IQH", 1, 0, len(data)) + bytes(data) + b"\0" * 4)


class CoreMidi:
    """Every MIDI controller the system shows (USB, and Bluetooth ones the system has connected), in and
    out. MidiPort uses it where there is no ALSA: it calls rescan() every few seconds and send() for notes
    and clock; what the controllers play lands in `events` in the same form as everywhere else."""

    def __init__(self, events, parser, log=lambda msg: None):
        self.events, self.parser, self.log = events, parser, log
        self.sources = {}                           # endpoint -> (name, its own parser)
        cf = self.cf = _cf()
        cm = self.cm = _lib("CoreMIDI")
        u32, ptr = ctypes.c_uint32, ctypes.c_void_p
        cm.MIDIClientCreate.argtypes = [ptr, ptr, ptr, ctypes.POINTER(u32)]
        cm.MIDIInputPortCreate.argtypes = [u32, ptr, _READ, ptr, ctypes.POINTER(u32)]
        cm.MIDIOutputPortCreate.argtypes = [u32, ptr, ctypes.POINTER(u32)]
        for name in ("MIDIGetNumberOfSources", "MIDIGetNumberOfDestinations"):
            getattr(cm, name).restype = ctypes.c_ulong
        for name in ("MIDIGetSource", "MIDIGetDestination"):
            getattr(cm, name).restype = u32
            getattr(cm, name).argtypes = [ctypes.c_ulong]
        cm.MIDIPortConnectSource.argtypes = [u32, u32, ptr]
        cm.MIDIObjectGetStringProperty.argtypes = [u32, ptr, ctypes.POINTER(ptr)]
        cm.MIDISend.argtypes = [u32, u32, ptr]
        cm.MIDIClientDispose.argtypes = [u32]
        self._name_key = ptr.in_dll(cm, "kMIDIPropertyDisplayName")
        label = cf.CFStringCreateWithCString(None, b"PocketPads", _UTF8)
        self.client, self.inp, self.outp = u32(), u32(), u32()
        self._read = _READ(self._on_read)           # kept: Core MIDI calls it for as long as the port lives
        if (cm.MIDIClientCreate(label, None, None, ctypes.byref(self.client)) != 0
                or cm.MIDIInputPortCreate(self.client, label, self._read, None, ctypes.byref(self.inp)) != 0
                or cm.MIDIOutputPortCreate(self.client, label, ctypes.byref(self.outp)) != 0):
            raise OSError("Core MIDI would not start")
        self.rescan()

    def _on_read(self, plist, _refcon, source):
        try:
            got = self.sources.get(source or 0)
            if got:
                for data in packets(plist):
                    self.events.extend(got[1].feed(data))
        except Exception as e:                      # nothing may escape into Core MIDI's own thread
            self.log("core midi read: %s" % e)

    def _name(self, endpoint):
        text = ctypes.c_void_p()
        if self.cm.MIDIObjectGetStringProperty(endpoint, self._name_key, ctypes.byref(text)) != 0 or not text:
            return "MIDI device"
        buf = ctypes.create_string_buffer(256)
        ok = self.cf.CFStringGetCString(text, buf, 256, _UTF8)
        self.cf.CFRelease(text)
        return buf.value.decode("utf-8", "replace") if ok else "MIDI device"

    def rescan(self):
        """Listen to every source present now; returns their names."""
        here = [self.cm.MIDIGetSource(i) for i in range(self.cm.MIDIGetNumberOfSources())]
        for ep in here:
            if ep and ep not in self.sources:
                name = self._name(ep)
                self.sources[ep] = (name, self.parser())
                self.cm.MIDIPortConnectSource(self.inp, ep, ctypes.c_void_p(ep))
                self.log("midi in: %s" % name)
        for ep in [e for e in self.sources if e not in here]:
            self.log("midi gone: %s" % self.sources.pop(ep)[0])
        return [name for name, _ in self.sources.values()]

    def send(self, data):
        pl = packet_list(data)
        for i in range(self.cm.MIDIGetNumberOfDestinations()):
            self.cm.MIDISend(self.outp, self.cm.MIDIGetDestination(i), pl)

    def close(self):
        if self.client:
            self.cm.MIDIClientDispose(self.client)
            self.client = ctypes.c_uint32()


def bluetooth_sheet():
    """iPad only: slide up the system's list of Bluetooth MIDI devices, where one is connected with a tap.
    Once connected it is an ordinary Core MIDI device. Returns "" or why it could not be shown."""
    try:
        from rubicon.objc import ObjCClass
        ctypes.CDLL(_FW % ("CoreAudioKit", "CoreAudioKit"))
        app = ObjCClass("UIApplication").sharedApplication
        root = None
        for scene in app.connectedScenes.allObjects():
            for window in getattr(scene, "windows", None) or []:
                root = root or window.rootViewController
        if root is None:
            return "no window to show it in"
        while root.presentedViewController is not None:
            root = root.presentedViewController
        sheet = ObjCClass("CABTMIDICentralViewController").alloc().init()
        nav = ObjCClass("UINavigationController").alloc().initWithRootViewController_(sheet)
        nav.modalPresentationStyle = 2              # a sheet in the middle; a swipe down or a tap outside closes it
        root.presentViewController_animated_completion_(nav, True, None)
        return ""
    except Exception as e:
        return str(e) or type(e).__name__
