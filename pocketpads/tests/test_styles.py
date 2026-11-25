import sys, os, tempfile
sys.path.insert(0, sys.argv[1])
import numpy as np
import styles
from engine import Engine, KNEE, NPAT, SR

styles.check()
root = tempfile.mkdtemp()
for name in styles.ORDER:
    d = styles.project(name, root)
    e = Engine()
    assert not e.from_dict(d), "%s: a sound did not load" % name
    assert all(p.buf is not None for p in e.pads[:16]) and all(p.buf is None for p in e.pads[16:]), name
    assert e.song_on and e.song and all(0 <= s < NPAT for s in e.song), name
    used = {i for pat in e.patterns for i in range(16) if any(pat["steps"][i][:pat["length"]])}
    assert used == set(range(16)), "%s: pads never played: %s" % (name, sorted(set(range(16)) - used))
    out = e.render_offline()
    level = np.abs(out).max(axis=1)
    secs = len(out) / SR
    hot = float((level > KNEE).sum()) / max(1, (level > 0.02).sum())
    assert 60 < secs < 300 and np.isfinite(out).all(), name
    assert 0.5 < level.max() <= 1.0 and hot < 0.02, "%s: peak %.2f, limiter on %.1f%% of the time" % (name, level.max(), hot * 100)
    # a saved style project loads again from its own files
    f = os.path.join(root, "p.json"); e.save(f)
    e2 = Engine(); assert not e2.load(f) and e2.pads[0].name == e.pads[0].name and e2.song == e.song
    print("%-8s %d:%02d, peak %.2f, %d patterns, song of %d" % (name, secs // 60, secs % 60, level.max(), len(styles.STYLES[name]["patterns"]), len(e.song)))
assert styles.ensure_sounds("TECHNO", root) == styles.ensure_sounds("TECHNO", root)      # second call reuses the files
for bad in ("x.x", "0 . 1"):
    try:
        styles.parse_row(bad, 16); raise SystemExit("a row that does not fit was accepted")
    except ValueError:
        pass
print("ALL STYLES OK")
