"""Touch: the on-screen buttons a phone gets, and taps on pads, tabs, steps, lists and dialogs.
Runs the whole app without a screen or sound, in a copy, at a phone's screen size."""
import os
import shutil
import sys
import tempfile

src = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), ".."))
app = os.path.join(tempfile.mkdtemp(), "app")
shutil.copytree(src, app, ignore=shutil.ignore_patterns("projects", "state.json", "samples", "_vendor", "*.log"))
os.environ.update(SDL_VIDEODRIVER="dummy", PP_NOAUDIO="1", PP_NOMIDI="1", PP_WINDOW="2400x1080", PP_SCRIPT="/dev/null")
sys.path.insert(0, app)
import pygame, main
a = main.App(); e = a.eng
W, H = a.W, a.H
def at(lx, ly): return a.ox + lx * a.u, a.oy + ly * a.u
def tap(lx, ly, f=1): a.touch_down(f, *at(lx, ly)); a.touch_up(f)
assert not a.touch_ui and abs(a.u - H / 360.0) < 1e-6
u0 = a.u
tap(100, 100)                                   # first touch: only brings up the buttons
assert a.touch_ui and a.u < u0 and a.u == min(W / 860.0, H / 360.0), (a.u, u0)
a.draw()
# pads: bottom left is pad 1, top right is pad 16; a touch plays and selects
for (lx, ly), pad in (((40, 300), 0), ((270, 60), 15), ((120, 220), 5)):
    before = e.pad_flash[pad]; tap(lx, ly); e.render(512)
    assert a.sel == pad and e.pad_flash[pad] > before, (pad, a.sel)
# the on-screen buttons are the real buttons
b = a.touch_buttons()
def press(name, f=2):
    x, y, w, h = b[name]; a.touch_down(f, *at(x + w / 2, y + h / 2))
def release(f=2): a.touch_up(f)
press("r1"); assert "r1" in a.held; release(); assert a.page == 1 and "r1" not in a.held
press("l1"); release(); assert a.page == 0
press("start"); release(); e.render(512); assert e.playing; press("start"); release(); e.render(512); assert not e.playing
a.sel = 0; press("right"); release(); assert a.sel == 1
# two fingers: SELECT held with one, up with another, raises the tempo
bpm = e.bpm; press("select", 5); press("up", 6); release(6); release(5); assert e.bpm == bpm + 1, (bpm, e.bpm)
# sliding across the d-pad moves from one direction to the next
press("left", 7); x, y, w, h = b["up"]; a.touch_move(7, *at(x + w / 2, y + h / 2)); assert "up" in a.held and "left" not in a.held; release(7); assert not a.held
# top bar: tabs, pattern letters, play
tap(6 + 46 * 5 + 20, 15); assert main.PAGES[a.page] == "PROJ"
tap(286 + 17 * 2 + 5, 15); assert (e.pending_pat if e.pending_pat is not None else e.cur_pat) == 2
tap(600, 15); e.render(512); assert e.playing; tap(600, 15); e.render(512); assert not e.playing
# PROJ: a tap chooses a row, a second tap on its middle is A
row = a.PROJ_ROWS.index("NEW"); shown = 13
top = max(0, min(a.proj_row - shown // 2, len(a.PROJ_ROWS) - shown)); assert top <= row < top + shown
y = 34 + (row - top) * 22.6 + 10
tap(200, y); assert a.proj_row == row and not a.picker
top = max(0, min(a.proj_row - shown // 2, len(a.PROJ_ROWS) - shown)); y = 34 + (row - top) * 22.6 + 10
tap(200, y); assert a.picker and a.picker["title"].startswith("NEW PROJECT")
a.draw()
tap(300, 66 + 2 * 20 + 8); assert a.picker["idx"] == 2
tap(300, 66 + 2 * 20 + 8); assert a.picker["title"] == "TECHNO: START WITH"
tap(20, 200); assert a.picker["title"].startswith("NEW PROJECT")      # beside the list: back
tap(20, 200); assert not a.picker
# TEMPO: left third down, right third up
row = a.PROJ_ROWS.index("TEMPO"); a.proj_row = row
top = max(0, min(a.proj_row - shown // 2, len(a.PROJ_ROWS) - shown)); y = 34 + (row - top) * 22.6 + 10
bpm = e.bpm; tap(380, y); assert e.bpm > bpm; tap(30, y); tap(30, y); assert e.bpm < bpm, (bpm, e.bpm)
# confirm: on it is yes, beside it is no
done = []; a.confirm = ("SURE?", lambda: done.append(1)); tap(20, 40); assert not a.confirm and not done
a.confirm = ("SURE?", lambda: done.append(1)); tap(320, 170); assert done == [1]
# BEAT: a step toggles, the name column chooses the pad
a.page = main.PAGES.index("BEAT"); pat = e.patterns[e.cur_pat]
for st in range(16): pat["steps"][3][st] = 0
tap(96 + 33.5 * 5 + 10, 46 + 17.8 * 3 + 8); assert a.sel % 16 == 3 and a.cur_step % 16 == 5 and pat["steps"][a.sel][a.cur_step]
tap(96 + 33.5 * 5 + 10, 46 + 17.8 * 3 + 8); assert not pat["steps"][a.sel][a.cur_step]
tap(40, 46 + 17.8 * 9 + 8); assert a.sel % 16 == 9 and a.beat_name
a.draw()
# the name keyboard
a.page = main.PAGES.index("PROJ"); a.start_naming(); n0 = len(a.namer["text"]); tap(125 + 39 * 2 + 18, 144 + 15)
assert len(a.namer["text"]) == n0 + 1 or n0 >= 12, a.namer; a.tap_button("B")
# a physical button takes the on-screen ones away
a.handle_event(pygame.event.Event(pygame.JOYBUTTONDOWN, button=99, instance_id=0, joy=0)); assert not a.touch_ui and a.u == u0
tap(40, 300); assert not a.touch_ui
# a camera cutout: the page keeps clear of it, and touches still land where things are drawn
os.environ["PP_INSETS"] = "120,0,0,30"
a.fit_window(); assert a.insets == (120, 0, 0, 30) and a.ox >= 120 and a.oy + 360 * a.u <= a.H - 30 + 1e-6, (a.insets, a.ox, a.oy)
a.page = 0; tap(270, 60); assert a.sel == 15
del os.environ["PP_INSETS"]; a.fit_window(); assert a.insets == (0, 0, 0, 0) and a.u == u0
print("ALL TOUCH OK")
