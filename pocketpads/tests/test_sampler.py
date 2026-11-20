import sys, os, tempfile
sys.path.insert(0, sys.argv[1])
import numpy as np, engine, kit
from engine import Engine, SR, BLOCK
d = tempfile.mkdtemp(); paths = kit.ensure_kit(os.path.join(d, "kit"))
def fresh():
    e = Engine()
    for p, x in zip(e.pads, paths): p.load(x)
    e.bpm = 120; return e
run = lambda e, s: np.concatenate([e.render(BLOCK) for _ in range(int(SR*s/BLOCK))])
rms = lambda x: float(np.sqrt(np.mean(x**2)))
# hold: crash stops on note_off, rings on without it
e = fresh(); e.pads[10].mode = engine.HOLD; e.trigger(10); run(e, 0.2); e.note_off(10); x = run(e, 0.3)
e2 = fresh(); e2.trigger(10); run(e2, 0.2); y = run(e2, 0.3)
print("hold: after release %.5f vs one-shot %.4f" % (rms(x[2000:]), rms(y[2000:]))); assert rms(x[2000:]) < 1e-6 < rms(y[2000:])
# hold from the sequencer lasts one step (0.125 s)
e = fresh(); e.patterns[0] = engine.new_pattern(); e.patterns[0]["steps"][10][0] = 1; e.pads[10].mode = engine.HOLD; e.play(); x = run(e, 0.6)
print("sequenced hold: 0-0.1 s %.4f, 0.2-0.5 s %.6f" % (rms(x[:4410]), rms(x[8820:22050]))); assert rms(x[:4410]) > 0.01 and rms(x[8820:22050]) < 1e-6
# loop: blip (0.18 s) keeps sounding, second hit stops it, transport stop stops it
e = fresh(); e.pads[11].mode = engine.LOOP; e.trigger(11); x = run(e, 1.0); print("loop: 0.6-1.0 s %.4f" % rms(x[26460:])); assert rms(x[26460:]) > 0.01
e.trigger(11); x = run(e, 0.4); assert rms(x[4000:]) < 1e-6, rms(x[4000:])
e.trigger(11); run(e, 0.3); e.stop(); x = run(e, 0.3); assert rms(x[4000:]) < 1e-6
# reverse
e = fresh(); p = e.pads[0]; f = p.buf.copy(); p.rev = True; p.bake(); a, b = np.abs(f[:4000]).mean(), np.abs(p.buf[:4000]).mean(); print("reverse: start level %.3f -> %.3f" % (a, b)); assert b < a * 0.5
# fades
p = e.pads[1]; p.fin = 100; p.bake(); assert np.abs(p.buf[:441]).max() < 0.15 * np.abs(p.raw).max()
# fine pitch / fit: 0.5 s bass fitted to 1 beat at 100 bpm (0.6 s)
import math
p = e.pads[12]; L = p.region_seconds(); semis = 12 * math.log2(L / 0.6); p.pitch = int(round(semis)); p.fine = int(round((semis - p.pitch) * 100)); p.bake()
print("fit: %.3f s -> %.3f s (want 0.600), pitch %d fine %d" % (L, p.seconds(), p.pitch, p.fine)); assert abs(p.seconds() - 0.6) < 0.003
# capture + stereo wav
e = fresh(); kit.demo_patterns(e.patterns); e.capture_start(); e.play(); x = run(e, 1.0); c = e.capture_stop(); assert c.shape == x.shape and np.allclose(c, x)
f = os.path.join(d, "st.wav"); engine.write_wav(f, c); r = engine.load_wav(f); assert r.shape == c.shape and np.abs(r - c).max() < 1e-3
# preview + off
e = fresh(); e.preview(3, e.pads[10].raw); x = run(e, 0.2); e.note_off(3); y = run(e, 0.2); assert rms(x) > 0.01 and rms(y[2000:]) < 1e-6
# save/load
e.pads[2].mode = 2; e.pads[2].rev = True; e.pads[2].fine = -20; e.pads[2].fout = 300; f = os.path.join(d, "p.json"); e.save(f); e2 = Engine(); e2.load(f); q = e2.pads[2]; assert (q.mode, q.rev, q.fine, q.fout) == (2, True, -20, 300)
print("ALL SAMPLER OK")
