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
print("ALL OK")
