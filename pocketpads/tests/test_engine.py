import sys, time, os, tempfile
sys.path.insert(0, sys.argv[1])
import numpy as np
import engine, kit
from engine import Engine, SR, BLOCK
d = tempfile.mkdtemp()
paths = kit.ensure_kit(os.path.join(d, "kit"))
e = Engine()
for p, path in zip(e.pads, paths): p.load(path)
print("kit:", [(p.name, round(p.seconds(),2), round(float(np.abs(p.raw).max()),2)) for p in e.pads][:4], "...")
kit.demo_patterns(e.patterns)
e.bpm = 120
# sequencer timing: kick on steps 0,4,8,12 -> onsets every 0.5 s
for p in e.pads[1:]: p.mute = True
e.play()
out = np.concatenate([e.render(BLOCK) for _ in range(int(SR*2.2/BLOCK))])
env = np.abs(out[:,0])
on = [i for i in range(1, len(env)) if env[i] > 0.02 and env[max(0,i-2000):i].max() < 0.02]
print("kick onsets (s):", [round(i/SR,4) for i in on])
assert len(on) >= 4 and all(abs((on[k]-on[0])/SR - 0.5*k) < 0.002 for k in range(4)), "timing off"
# all pads, speed
for p in e.pads: p.mute = False
e.patterns[0]["steps"] = [[1]*32 for _ in range(16)]
t0=time.perf_counter(); N=400
for _ in range(N): e.render_pcm(BLOCK)
dt=(time.perf_counter()-t0)/N*1000
print("worst-case render %.3f ms per block (budget %.1f ms), peak %.2f" % (dt, BLOCK/SR*1000, e.peak))
# save / load round trip
e.pads[3].pitch = 7; e.pads[3].start=0.1; e.pads[3].bake(); e.swing=62.0
f = os.path.join(d, "p.json"); e.save(f)
e2 = Engine(); miss = e2.load(f)
assert not miss and e2.pads[3].pitch == 7 and e2.swing == 62.0 and e2.patterns[0]["steps"][5][9] == 1
assert abs(len(e2.pads[3].buf) - len(e.pads[3].buf)) < 2
# wav formats
import struct
x = (np.sin(np.arange(22050)/10)*0.5).astype("<f4")
with open(os.path.join(d,"f.wav"),"wb") as fh:
    fh.write(b"RIFF"+struct.pack("<I",36+x.nbytes)+b"WAVEfmt "+struct.pack("<IHHIIHH",16,3,1,22050,22050*4,4,32)+b"data"+struct.pack("<I",x.nbytes)+x.tobytes())
a = engine.load_wav(os.path.join(d,"f.wav")); print("float 22k wav ->", a.shape, round(float(np.abs(a).max()),2))
assert a.shape == (44100, 2)
try: engine.load_wav(f); assert False
except ValueError as ex: print("bad file rejected:", ex)
# record quantize
e3 = Engine(); e3.pads[0].load(paths[0]); e3.rec=True; e3.play(); 
for _ in range(20): e3.render(BLOCK)
e3.trigger(0); print("recorded steps:", [i for i,v in enumerate(e3.patterns[0]["steps"][0]) if v], "at step", e3.step)
# --- song chain, long patterns, step notes, project round trip
import numpy as _np, engine as _e, tempfile as _tf, os as _os
_d = _tf.mkdtemp(); _t = _np.arange(_e.SR // 4) / _e.SR
_e.write_wav(_os.path.join(_d, "t.wav"), 0.5 * _np.sin(2 * _np.pi * 220 * _t))
g = _e.Engine(); g.pads[0].load(_os.path.join(_d, "t.wav")); g.bpm = 240
g.patterns[0]["steps"][0][0] = 1
g.patterns[1]["length"] = 40; g.patterns[1]["steps"][0][39] = 1
g.patterns[2]["steps"][0][4] = 1; g.set_note(2, 0, 4, 12)
assert 12 in g.pads[0].variants and abs(len(g.pads[0].variants[12]) * 2 - len(g.pads[0].buf)) < 4
g.song, g.song_on = [0, 2, 1, 0], True
g.play(); order = []
for _ in range(int(_e.SR * 60 / 240 / 4 * (16 + 16 + 40 + 16 + 8) / _e.BLOCK)):
    g.render(_e.BLOCK)
    if not order or order[-1] != g.cur_pat: order.append(g.cur_pat)
print("song order played:", order); assert order == [0, 2, 1, 0]
# an octave-up step really sounds an octave up
g = _e.Engine(); g.pads[0].load(_os.path.join(_d, "t.wav")); g.patterns[0]["steps"][0][0] = 1; g.set_note(0, 0, 0, 12); g.play()
x = _np.concatenate([g.render(_e.BLOCK) for _ in range(12)])[:4096, 0]
hz = _np.argmax(_np.abs(_np.fft.rfft(x * _np.hanning(4096)))) * _e.SR / 4096
print("step note +12 plays at %.0f Hz (pad is 220)" % hz); assert abs(hz - 440) < 12
g.song = [1, 2]; g.patterns[3]["length"] = 128; g.patterns[3]["steps"][5][127] = 2
f = _os.path.join(_d, "p.json"); g.save(f); h = _e.Engine(); h.load(f)
assert h.song == [1, 2] and h.patterns[3]["length"] == 128 and h.patterns[3]["steps"][5][127] == 2
assert h.patterns[0]["notes"][0][0] == 12 and 12 in h.pads[0].variants
h.copy_pattern(0, 6); assert h.patterns[6]["notes"][0][0] == 12; h.clear_pattern(0); assert not h.pads[0].variants or 12 in h.pads[0].variants
# --- export
import kit as _kit
g = _e.Engine()
for _p, _x in zip(g.pads, _kit.ensure_kit(_os.path.join(_d, "kit"))): _p.load(_x)
_kit.demo_patterns(g.patterns); g.bpm = 120
a = g.render_offline(loops=2)
assert abs(len(a) / _e.SR - 6.5) < 0.02 and _np.abs(a[:_e.SR]).max() > 0.1 and _np.abs(a[-200:]).max() < 0.01
g.song, g.song_on = [0, 1, 1], True; g.patterns[1]["length"] = 32
a = g.render_offline(); print("exported song: %.2f s (want 12.5)" % (len(a) / _e.SR))
assert abs(len(a) / _e.SR - 12.5) < 0.02 and not g.playing
print("ALL OK")
