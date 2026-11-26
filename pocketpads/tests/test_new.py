"""Starting a project: NEW asks for a kit, then for its sounds only or one of its songs.
Runs the whole app without a screen or sound, in a copy, so no project or setting of yours is touched."""
import os
import shutil
import sys
import tempfile

src = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), ".."))
app = os.path.join(tempfile.mkdtemp(), "app")
shutil.copytree(src, app, ignore=shutil.ignore_patterns("projects", "state.json", "samples", "_vendor", "*.log"))
os.environ.update(SDL_VIDEODRIVER="dummy", PP_NOAUDIO="1", PP_NOMIDI="1", PP_WINDOW="1280x720", PP_SCRIPT="/dev/null")
sys.path.insert(0, app)
import main, styles
a = main.App(); e = a.eng
def press(b): a.button(b, True); a.button(b, False)
a.page = main.PAGES.index("PROJ"); a.proj_row = a.PROJ_ROWS.index("NEW")
press("A"); assert a.picker and a.picker["title"] == "NEW PROJECT: PICK A KIT"
press("down"); press("down"); press("A")
assert a.picker["title"] == "TECHNO: START WITH" and len(a.picker["items"]) == 5, a.picker
assert all(len(l) <= 56 for st in [""] + styles.ORDER for l, _ in (a.ask_new_start(st), a.picker["items"])[1])
a.ask_new_start("TECHNO")
press("B"); assert a.picker["title"] == "NEW PROJECT: PICK A KIT"          # back, not out
press("B"); assert not a.picker
# sounds only
press("A"); press("down"); press("down"); press("A"); press("A"); assert a.confirm, "no confirm"
press("A"); assert not a.confirm and not a.picker
assert e.pads[0].name == "KICK" and not e.song_on and not any(any(r[:p["length"]]) for p in e.patterns for r in p["steps"])
assert e.bpm == 132 and e.name == "TECHNO", (e.bpm, e.name)
# song 3
press("A"); press("down"); press("down"); press("A"); press("down"); press("down"); press("down"); press("A"); press("A")
assert e.name == "HYPNOTIC" and e.bpm == 128 and e.song_on and len(e.song) == 34, (e.name, e.bpm, len(e.song))
# starter kit, sounds only and demo
press("A"); press("A"); assert len(a.picker["items"]) == 2; press("A"); press("A")
assert not any(any(r[:p["length"]]) for p in e.patterns for r in p["steps"]) and e.pads[0].buf is not None
press("A"); press("A"); press("down"); press("A"); press("A")
assert any(e.patterns[0]["steps"][0][:16])
print("ALL NEW PROJECT OK")
