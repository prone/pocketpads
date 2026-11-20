"""Regression tests for problems found in code review: clicks, damaged projects, missing samples."""
import json
import os
import sys
import tempfile

sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), ".."))
import numpy as np

import engine
import kit
from engine import BLOCK, SR, Engine

tmp = tempfile.mkdtemp()
paths = kit.ensure_kit(os.path.join(tmp, "kit"))


def run(e, seconds):
    return np.concatenate([e.render(BLOCK) for _ in range(int(SR * seconds / BLOCK))])


# A retriggered or choked sound must fade out smoothly even when the fade starts near the end of a block.
dc = os.path.join(tmp, "dc.wav")
engine.write_wav(dc, np.full(SR, 0.3))
e = Engine()
e.pads[0].load(dc)
e.bpm = 120
e.patterns[0] = engine.new_pattern()
e.patterns[0]["steps"][0][:16] = [1] * 16
e.play()
x = run(e, 3.0)[:, 0]
jump = float(np.abs(np.diff(x[2000:])).max())
print("largest sample-to-sample jump while retriggering: %.4f" % jump)
assert jump < 0.02, jump

# A HOLD pad ended by the sequencer fades the same way.
e = Engine()
e.pads[0].load(dc)
e.pads[0].mode = engine.HOLD
e.patterns[0] = engine.new_pattern()
e.patterns[0]["steps"][0][0] = 1
e.play()
x = run(e, 0.5)[:, 0]
assert float(np.abs(np.diff(x[2000:])).max()) < 0.02

# A looping pad in a pattern restarts each time rather than switching itself off every other bar.
e = Engine()
for p, f in zip(e.pads, paths):
    p.load(f)
e.pads[11].mode = engine.LOOP
e.patterns[0] = engine.new_pattern()
e.patterns[0]["steps"][11][0] = 1
e.bpm = 120
e.play()
bars = [float(np.sqrt(np.mean(run(e, 2.0)[SR:] ** 2))) for _ in range(3)]
print("looping pad level in bars 1-3:", [round(b, 3) for b in bars])
assert min(bars) > 0.01

# Damaged project files raise ValueError and never anything else.
for bad in ([], {"bpm": "fast"}, {"bpm": None}, {"patterns": [5]}, {"pads": [{"path": 5}]}, {"pads": "x"}, {"swing": []}):
    f = os.path.join(tmp, "bad.json")
    with open(f, "w") as fh:
        json.dump(bad, fh)
    try:
        Engine().load(f)
    except ValueError:
        pass

# A sample that is missing at load time stays assigned, so saving cannot erase it.
e = Engine()
e.pads[3].load(paths[3])
proj = os.path.join(tmp, "p.json")
e.save(proj)
with open(proj) as fh:
    d = json.load(fh)
d["pads"][3]["path"] = os.path.join(tmp, "gone.wav")
with open(proj, "w") as fh:
    json.dump(d, fh)
e2 = Engine()
missing = e2.load(proj)
e2.save(proj)
with open(proj) as fh:
    assert json.load(fh)["pads"][3]["path"].endswith("gone.wav") and missing

# NaN in a float WAV is cleaned on load.
import struct
bad = np.array([0.5, np.nan, np.inf, -np.inf] * 500, "<f4").tobytes()
f = os.path.join(tmp, "nan.wav")
with open(f, "wb") as fh:
    fh.write(b"RIFF" + struct.pack("<I", 36 + len(bad)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 3, 1, SR, SR * 4, 4, 32)
             + b"data" + struct.pack("<I", len(bad)) + bad)
assert np.isfinite(engine.load_wav(f)).all()

# Pitching a long sample down is capped instead of using gigabytes.
long = os.path.join(tmp, "long.wav")
engine.write_wav(long, np.zeros(SR * 150))
p = engine.Pad(0)
p.load(long)
p.pitch = -24
p.bake()
print("150 s sample at -24 semitones bakes to %.0f s" % p.seconds())
assert p.seconds() <= engine.MAX_BAKE_SECONDS + 1

# The pad filter must not wrap the end of a sound round to its start.
seg = np.zeros((4096, 2), np.float32)
seg[-200:] = 1.0
assert float(np.abs(engine.fft_filter(seg, -0.8)[:200]).max()) < 0.01
print("ALL ROBUSTNESS OK")
