import sys, time, os, tempfile
sys.path.insert(0, sys.argv[1])
import numpy as np
import engine, kit
from engine import Engine, SR, BLOCK
d = tempfile.mkdtemp(); paths = kit.ensure_kit(os.path.join(d, "kit"))
def fresh():
    e = Engine()
    for p, path in zip(e.pads, paths): p.load(path)
    e.bpm = 120
    return e
def run(e, secs): return np.concatenate([e.render(BLOCK) for _ in range(int(SR*secs/BLOCK))])
def onsets(x, thr=0.02, gap=1500):
    env = np.abs(x).max(axis=1); return [i for i in range(1, len(env)) if env[i] > thr and env[max(0,i-gap):i].max() < thr]
rms = lambda x: float(np.sqrt(np.mean(x**2)))

# delay: rim shot, 1/8 at 120 bpm = 0.25 s between echoes, alternating sides
e = fresh(); e.pads[3].dsend = 1.0; e.delay_div = 1; e.delay_fb = 0.5; e.trigger(3); x = run(e, 1.2)
on = onsets(x); print("delay echoes at", [round(i/SR,3) for i in on[:4]])
assert abs((on[1]-on[0])/SR - 0.25) < 0.004 and abs((on[2]-on[1])/SR - 0.25) < 0.004
s1 = x[on[1]:on[1]+2000]; s2 = x[on[2]:on[2]+2000]
assert rms(s1[:,0]) > 5*rms(s1[:,1]) and rms(s2[:,1]) > 5*rms(s2[:,0]), "not ping-pong"
x = run(e, 12); assert e.delay.tail <= 0 and rms(x[-BLOCK*4:]) == 0, "delay never goes idle"

# reverb: tail decays, stays bounded, goes idle
e = fresh(); e.pads[1].rsend = 1.0; e.reverb_size = 0.6; e.trigger(1); x = run(e, 4.0)
a, b, c = rms(x[int(.4*SR):int(.6*SR)]), rms(x[int(1.4*SR):int(1.6*SR)]), rms(x[int(1.9*SR):int(2.1*SR)])
print("reverb tail rms at 0.5/1.5/2.0 s: %.4f %.4f %.5f, peak %.2f" % (a, b, c, np.abs(x).max()))
assert a > b > c > 0 and np.abs(x).max() < 1.0
e.reverb_size = 1.0; e.trigger(1); x = run(e, 8); assert np.abs(x).max() < 1.0 and np.isfinite(x).all()
x = run(e, 8); assert e.reverb.tail <= 0

# pad filter: low-pass removes highs from the hat; high-pass removes lows from the kick
def hf(x): X = np.abs(np.fft.rfft(x[:,0])); f = np.fft.rfftfreq(len(x), 1/SR); return X[f>5000].sum()/X.sum()
e = fresh(); p = e.pads[4]; base = hf(p.buf); p.filt = -70; p.bake(); lp = hf(p.buf)
k = e.pads[0]; kb = 1-hf(k.buf); k.filt = 80; k.bake()
print("hat highs %.2f -> %.2f with low-pass; kick level %.2f -> %.2f with high-pass" % (base, lp, rms(e.pads[0].raw), rms(k.buf)))
assert lp < base*0.3 and rms(k.buf) < 0.5*rms(k.raw)

# choke: closed hat cuts the open hat
e = fresh(); e.pads[4].choke = e.pads[5].choke = 1; e.trigger(5); run(e, 0.1); e.trigger(4); x = run(e, 0.5)
e2 = fresh(); e2.trigger(5); run(e2, 0.1); e2.trigger(4); y = run(e2, 0.5)
print("open-hat tail after closed hat: choked %.4f vs unchoked %.4f" % (rms(x[int(.2*SR):]), rms(y[int(.2*SR):])))
assert rms(x[int(.2*SR):]) < 0.1*rms(y[int(.2*SR):])

# note repeat: 1/16 at 120 bpm = 0.125 s
e = fresh(); e.repeat_div = 3; e.hold(3); x = run(e, 0.8); e.release(3); y = run(e, 0.5)
on = onsets(x, gap=800); gaps = np.diff(on)/SR; print("repeat gaps", np.round(gaps[:4],4), "after release:", len(onsets(y, gap=800)))
assert np.allclose(gaps, 0.125, atol=0.001) and len(onsets(y[2000:], gap=800)) == 0
# repeat while recording writes steps without double hits
e = fresh(); e.patterns[0] = engine.new_pattern(); e.rec = True; e.play(); run(e, 0.3); e.hold(3); run(e, 0.6); e.release(3); run(e, 2.5)
st = [i for i,v in enumerate(e.patterns[0]["steps"][3]) if v]; print("recorded repeat steps:", st); assert len(st) >= 4

# swing 66% delays odd steps by about a third of a step-pair
e = fresh(); e.patterns[0] = engine.new_pattern(); e.patterns[0]["steps"][3][:4] = [1,1,1,1]; e.swing = 66; e.play(); x = run(e, 0.7)
on = onsets(x, gap=800); print("swing 66 onsets", [round(i/SR,3) for i in on[:4]]); assert abs((on[1]-on[0])/SR - 0.125*1.32) < 0.003 and abs((on[2]-on[0])/SR - 0.25) < 0.002

# performance fx
e = fresh(); kit.demo_patterns(e.patterns); e.play(); run(e, 1.0)
e.perf["stutter"] = True; x = run(e, 0.5); L = int(e.step_frames()); seg = x[L:L*2]; seg2 = x[L*2:L*3]
print("stutter loop repeats: %.5f" % rms(seg-seg2)); assert rms(seg-seg2) < 1e-3 and rms(seg) > 0
e.perf["stutter"] = False; run(e, 0.2)
e.perf["tape"] = True; x = run(e, 1.2); print("tape stop rms start %.3f end %.5f" % (rms(x[:8000]), rms(x[-8000:]))); assert rms(x[-8000:]) < 1e-4
e.perf["tape"] = False; x = run(e, 0.5); assert rms(x) > 0.01
before = run(e, 1.0); e.perf["lp"] = True; run(e, 1.5); x = run(e, 1.0); e.perf["lp"] = False; run(e, 0.4); after = run(e, 1.0)
print("low-pass sweep highs: %.3f -> %.3f -> back %.3f" % (hf(before), hf(x), hf(after))); assert hf(x) < 0.2*hf(before) and hf(after) > 0.6*hf(before)
e.perf["hp"] = True; run(e, 1.5); x = run(e, 1.0); e.perf["hp"] = False; print("high-pass sweep level %.3f -> %.3f" % (rms(before), rms(x))); assert rms(x) < 0.6*rms(before)

# cost with everything on
e = fresh(); e.patterns[0]["steps"] = [[1]*32 for _ in range(16)]
for p in e.pads: p.dsend = p.rsend = 0.3
e.perf["lp"] = True; e.play(); run(e, 0.5); t0 = time.perf_counter(); N = 300
for _ in range(N): e.render_pcm(BLOCK)
print("everything-on render %.3f ms per block (budget %.1f)" % ((time.perf_counter()-t0)/N*1000, BLOCK/SR*1000))
# save/load keeps the new settings
e.pads[2].filt = -40; e.pads[2].choke = 2; e.swing = 58; e.delay_div = 3; f = os.path.join(d, "p.json"); e.save(f)
e2 = Engine(); e2.load(f); assert (e2.pads[2].filt, e2.pads[2].choke, e2.swing, e2.delay_div, round(e2.pads[2].dsend,2)) == (-40, 2, 58, 3, 0.3)
old = e.to_dict(); old["swing"] = 0.4; e2.from_dict(old); assert e2.swing == 60
print("ALL FX OK")
