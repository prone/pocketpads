"""The Apple-only parts (apple.py) and the SDL recorder, checked on a Mac: python3 test_apple.py <app folder>.
Core MIDI is tried through a made-up controller and a made-up synth, so no hardware is needed."""
import ctypes
import os
import subprocess
import sys
import tempfile
import time

APP = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, APP)
if sys.platform != "darwin":
    print("ALL APPLE OK (skipped: not a Mac)")
    sys.exit(0)
import numpy as np
import apple
import midi
from engine import SR, SdlRecorder, load_wav, write_wav

tmp = tempfile.mkdtemp()

# --- compressed files: a tone, squeezed to AAC and to AIFF by the system's converter, read back
t = np.arange(SR * 2) / SR
tone = np.stack([np.sin(t * 2 * np.pi * 440) * 0.5] * 2, axis=1)
wav = os.path.join(tmp, "tone.wav")
write_wav(wav, tone)
for ext, args in ((".m4a", ["-f", "m4af", "-d", "aac"]), (".aiff", ["-f", "AIFF", "-d", "BEI16"])):
    src = os.path.join(tmp, "tone" + ext)
    subprocess.run(["afconvert"] + args + [wav, src], check=True, capture_output=True)
    out = os.path.join(tmp, "out%s.wav" % ext)
    why = apple.decode(src, out, 180, SR)
    assert why == "", why
    got = load_wav(out)
    assert abs(len(got) - SR * 2) < 4000, len(got)
    peak = float(np.abs(got).max())
    assert 0.4 < peak < 0.6, peak
    spectrum = np.abs(np.fft.rfft(got[4096:4096 + SR, 0]))
    assert abs(int(spectrum.argmax()) - 440) <= 2, spectrum.argmax()
assert len(load_wav(out)) and apple.decode(src, out, 0.5, SR) == "" and abs(len(load_wav(out)) - SR // 2) < 200   # the time limit
assert apple.decode(__file__, out, 5, SR) != ""         # not audio: says so, does not crash
assert apple.decode(os.path.join(tmp, "missing.mp3"), out, 5, SR) != ""
print("compressed files ok")

# --- packets
pl = apple.packet_list(b"\x90\x24\x7f")
assert apple.packets(ctypes.addressof(pl)) == [b"\x90\x24\x7f"]

# --- Core MIDI through the port the app uses
port = midi.MidiPort(print)
assert port.start(), port.error
core = port.java
assert isinstance(core, apple.CoreMidi)
cm, cf = core.cm, core.cf
name = cf.CFStringCreateWithCString(None, b"PP Test Controller", 0x08000100)
src = ctypes.c_uint32()
cm.MIDISourceCreate.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
assert cm.MIDISourceCreate(core.client, name, ctypes.byref(src)) == 0
heard = []
sink = apple._READ(lambda plist, a, b: heard.extend(apple.packets(plist)))
dst = ctypes.c_uint32()
cm.MIDIDestinationCreate.argtypes = [ctypes.c_uint32, ctypes.c_void_p, apple._READ, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
assert cm.MIDIDestinationCreate(core.client, cf.CFStringCreateWithCString(None, b"PP Test Synth", 0x08000100), sink, None, ctypes.byref(dst)) == 0
deadline = time.time() + 6
while "PP Test Controller" not in port.devices and time.time() < deadline:     # found by the port's own rescan
    time.sleep(0.1)
assert "PP Test Controller" in port.devices, port.devices
cm.MIDIReceived.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
for msg in (b"\x90\x24\x64", b"\x80\x24\x00", b"\xb0\x07\x40", b"\x99\x30\x7f"):
    cm.MIDIReceived(src, apple.packet_list(msg))
deadline = time.time() + 3
while len(port.events) < 4 and time.time() < deadline:
    time.sleep(0.02)
got = port.poll()
assert got == [("note", 0x24, 0x64), ("off", 0x24), ("cc", 7, 0x40), ("note", 0x30, 0x7f)], got
port.out_channel = 3
port.note(0)
deadline = time.time() + 3
while not any(h[:1] == b"\x92" for h in heard) and time.time() < deadline:
    time.sleep(0.02)
assert any(h[:1] == b"\x92" for h in heard), heard
port.stop()
core.close()
print("core midi ok")

# --- the MIDI that Windows uses (pygame's), driven here through the same made-up controller
os.environ["PP_MIDI"] = "portmidi"
port = midi.MidiPort(print)
real = midi.MidiPort._start_apple
midi.MidiPort._start_apple = lambda self: False
keep = apple.CoreMidi(midi.EventQueue(maxlen=8), midi.RawMidi)        # only to own the made-up controller
src = ctypes.c_uint32()
assert cm.MIDISourceCreate(keep.client, name, ctypes.byref(src)) == 0
time.sleep(0.5)
assert port.start(), port.error
assert isinstance(port.java, midi.PortMidi) and "PP Test Controller" in port.devices, port.devices
for msg in (b"\x90\x24\x64", b"\xb0\x07\x40", b"\xc0\x05"):
    cm.MIDIReceived(src, apple.packet_list(msg))
deadline = time.time() + 3
while len(port.events) < 3 and time.time() < deadline:
    time.sleep(0.02)
got = port.poll()
assert got == [("note", 0x24, 0x64), ("cc", 7, 0x40), ("pc", 5)], got
port.stop(); port.java.close(); keep.close()
midi.MidiPort._start_apple = real
print("pygame midi ok")

# --- the recorder: SDL's stand-in microphone gives silence, which is enough to check the file and the stopping
os.environ["SDL_AUDIODRIVER"] = "dummy"
path = os.path.join(tmp, "rec.wav")
rec = SdlRecorder(path, 10)
time.sleep(0.6)
assert rec.poll() is None, rec.read()
rec.send_signal()
rec.thread.join(3)
assert rec.poll() == 0 and rec.read() == b"", rec.read()
got = load_wav(path)
assert SR * 0.2 < len(got) < SR * 1.5, len(got)
rec = SdlRecorder(os.path.join(tmp, "short.wav"), 0.3)
rec.thread.join(5)
assert rec.poll() == 0 and abs(len(load_wav(os.path.join(tmp, "short.wav"))) - int(SR * 0.3)) < 64
print("recorder ok")
print("ALL APPLE OK")
