"""The tablet layout (tablet.py), as an iPad gets it: pads, knobs and the top bar worked by touch.
Runs the whole app without a screen or sound, in a copy, at an 11 inch iPad's screen size.
With a folder as second argument, saves a picture of each page there."""
import os
import shutil
import sys
import tempfile

src = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), ".."))
shots = sys.argv[2] if len(sys.argv) > 2 else None
app = os.path.join(tempfile.mkdtemp(), "app")
shutil.copytree(src, app, ignore=shutil.ignore_patterns("projects", "state.json", "samples", "_vendor", "*.log"))
os.environ.update(SDL_VIDEODRIVER="dummy", PP_NOAUDIO="1", PP_NOMIDI="1", PP_SCRIPT="/dev/null", PP_UI="tablet")
os.environ.setdefault("PP_WINDOW", "1210x834")
sys.path.insert(0, app)
import pygame, main, tablet
a = main.App(); e = a.eng
assert a.tablet and abs(a.u - a.W / 640.0) < 1e-6 and a.LH > 400, (a.u, a.LH)

def at(lx, ly): return a.ox + lx * a.u, a.oy + ly * a.u
def down(lx, ly, f=1): a.touch_down(f, *at(lx, ly))
def move(lx, ly, f=1): a.touch_move(f, *at(lx, ly))
def up(f=1): a.touch_up(f)
def tap(lx, ly, f=1): down(lx, ly, f); up(f)
def shot(name):
    a.toast_until = 0; a.frame = []; a.draw()
    if shots:
        os.makedirs(shots, exist_ok=True)
        pygame.image.save(a.screen, os.path.join(shots, name + ".png"))
def control(kind, **want):
    """The middle of the control on screen that matches."""
    for x, y, w, h, c in a.hot:
        if c["kind"] == kind and all(c.get(k) == v for k, v in want.items()):
            return x + w / 2, y + h / 2
    raise AssertionError("no %s %r on screen" % (kind, want))

a.draw()
assert not a.touch_ui, "no on-screen gamepad on a tablet"
# pads: a touch plays and selects; pad 1 is bottom left, pad 16 top right
for pad in (0, 15, 5):
    before = e.pad_flash[pad]; tap(*control("pad", pad=pad)); e.render(512)
    assert a.sel == pad and e.pad_flash[pad] > before, (pad, a.sel)
p0, p15 = control("pad", pad=0), control("pad", pad=15)
assert p0[0] < p15[0] and p0[1] > p15[1]
# two fingers, two pads
down(*control("pad", pad=0), f=1); down(*control("pad", pad=1), f=2); up(1); up(2)
# a knob: dragging up turns it up, down turns it down, and it stops at its ends
tap(*control("pad", pad=0)); a.draw()
p = e.pads[0]
x, y = control("knob", target="VOLUME")
v0 = p.vol
down(x, y); move(x, y - 40); a.draw()
assert p.vol > v0, (p.vol, v0)
move(x, y - 400); assert p.vol == 1.5
move(x, y + 400); assert p.vol == 0.0
up()
a.tab_last = (None, 0.0)
tap(x, y); tap(x, y)                            # two quick taps: back to the usual value
assert p.vol == 0.8, p.vol
x, y = control("knob", target="PITCH")
a.tab_last = (None, 0.0)
down(x, y); move(x, y - tablet.KNOB_TRAVEL / 4.0); up()
assert p.pitch == 12 and p.buf is not None, p.pitch
down(x, y); move(x, y + tablet.KNOB_TRAVEL / 4.0); up(); assert p.pitch == 0
# start can never pass end
x, y = control("knob", target="START")
a.tab_last = (None, 0.0)
down(x, y); move(x, y - 400); up(); assert p.start < p.end, (p.start, p.end)
down(x, y); move(x, y + 400); up(); assert p.start == 0.0
# the top bar
tap(*control("tempo")); assert True
x, y = control("tempo"); bpm = e.bpm
down(x, y); move(x, y - 25); up(); assert e.bpm == bpm + 10, (e.bpm, bpm)
down(x, y); move(x, y + 25); up(); assert e.bpm == bpm
taps = [c for c in a.hot if c[4]["kind"] == "tap"]
assert len(taps) >= len(main.PAGES) + main.NPAT
tap(288 + 2 * 23 + 10, 19); e.render(512); assert e.cur_pat == 2 or e.pending_pat == 2
tap(288 + 10, 19); e.render(512)
tap(584, 19); e.render(512); assert e.playing
tap(584, 19); e.render(512); assert not e.playing
tap(548, 19); assert e.rec
tap(548, 19); assert not e.rec
# banks, mute mode, note repeat
a.draw()
for x, y, w, h, c in a.hot:
    assert w * a.u >= 40 and h * a.u >= 40, "a control too small for a finger: %r" % ((x, y, w, h),)
shot("play")
names = {}
a.tab_bank(2); assert a.sel // main.BANK == 2
a.tab_bank(0); a.draw()
a.tab_mute_mode(); a.draw()
tap(*control("pad", pad=3)); assert e.pads[3].mute and a.sel == 3
tap(*control("pad", pad=3)); assert not e.pads[3].mute
shot("play_mute")
a.tab_mute_mode()
a.tab_repeat(); assert e.repeat_on
down(*control("pad", pad=0)); assert a.holding, "note repeat holds the pad while the finger is down"
up(); assert not a.holding
a.tab_repeat()
# every page has a layout of its own, with controls a finger can hit
def big_enough():
    for x, y, w, h, c in a.hot:
        assert w * a.u >= 40 and h * a.u >= 40, "a control too small for a finger: %r %r" % (c["kind"], (x, y, w, h))
for i, name in enumerate(main.PAGES):
    tap(6 + i * 46 + 22, 19); assert a.page == i
    assert hasattr(a, "tab_" + name.lower()), name
    a.draw(); big_enough(); shot(name.lower())
# BEAT: tap a step, drag along a row, accents, notes
a.page = main.PAGES.index("BEAT"); a.draw()
pat = e.patterns[e.cur_pat]
gx, gy, cw, rh = a.beat_geom()
def cell(row, step): return gx + (step + 0.5) * cw, gy + (row + 0.5) * rh
row = 11                                        # a track with nothing on it
assert not any(pat["steps"][row])
tap(*cell(row, 3)); assert pat["steps"][row][3] == 1 and a.sel == row and a.cur_step == 3
tap(*cell(row, 3)); assert pat["steps"][row][3] == 0
down(*cell(row, 4)); move(*cell(row, 5)); move(*cell(row, 7)); up()
assert pat["steps"][row][4:8] == [1, 1, 1, 1], pat["steps"][row][:8]      # the steps a quick finger skipped too
down(*cell(row, 4)); move(*cell(row, 6)); up(); assert pat["steps"][row][4:8] == [0, 0, 0, 1]
a.beat_mode = tablet.BEAT_MODES.index("ACCENT")
tap(*cell(row, 7)); assert pat["steps"][row][7] == 2
tap(*cell(row, 7)); assert pat["steps"][row][7] == 1
a.beat_mode = tablet.BEAT_MODES.index("NOTE")
x, y = cell(row, 7)
down(x, y); move(x, y - 22); up(); assert pat["notes"][row][7] == 3, pat["notes"][row][7]
down(x, y); move(x, y + 22); up(); assert pat["notes"][row][7] == 0
a.beat_mode = 0
tap(*cell(row, 7)); assert pat["steps"][row][7] == 0
tap(50, gy + 2.5 * rh); assert a.sel == 2      # a track's name chooses it
n = pat["length"]
a.change_length(1); a.draw(); assert pat["length"] == n + 16
a.tab_bar(1); a.draw(); assert a.cur_step // 16 == 1
tap(*cell(row, 0)); assert pat["steps"][row][16] == 1
tap(*cell(row, 0)); a.tab_bar(0); a.change_length(-1); assert pat["length"] == n
# SONG: build a chain by tapping patterns
a.page = main.PAGES.index("SONG"); e.song = []; e.song_on = False; a.song_cur = 0; a.draw()
a.tab_song_mode(); assert not e.song_on        # nothing to play yet
for k in (0, 1, 1): a.tab_song_set(k)
assert e.song == [0, 1, 1] and a.song_cur == 3
a.song_cur = 1; a.tab_song_set(2); assert e.song == [0, 2, 1]
a.tab_song_edit("repeat"); assert e.song == [0, 2, 2, 1]
a.tab_song_edit("remove"); assert e.song == [0, 2, 1]
a.tab_song_mode(); assert e.song_on
a.tab_length(True, 1); assert pat["length"] == n + 1
a.tab_length(True, -1); assert pat["length"] == n
a.draw(); big_enough(); shot("song_chain")
e.song_on = False
# SOUND: knobs, the markers on the waveform, and the tools
a.page = main.PAGES.index("SOUND"); a.sel = 0; a.draw()
p = e.pads[0]
x, y = control("knob", target="STRETCH")
down(x, y); move(x, y - 20); up(); assert p.stretch > 100 and p.stretch == round(p.stretch), p.stretch
tap(x, y); a.tab_last = (None, 0.0); tap(x, y); tap(x, y); assert p.stretch == 100.0, p.stretch
wx, wy, ww, wh, c = [h for h in a.hot if h[4]["kind"] == "trim"][0]
down(wx + 10, wy + 40); move(wx + ww * 0.25, wy + 40); up(); assert 0.2 < p.start < 0.3, p.start
down(wx + ww - 10, wy + 40); move(wx + ww * 0.75, wy + 40); up(); assert 0.7 < p.end < 0.8, p.end
a.tab_set("START", 0.0); a.tab_set("END", 1.0); a.flush_bake()
tap(wx + ww / 2, wy + 40); assert a.audition is not None       # a tap plays it
tap(wx + ww / 2, wy + 40); assert a.audition is None
a.tab_row("MODE", "right"); assert p.mode == 1
a.tab_row("MODE", "left"); a.tab_row("REVERSE", "right"); assert p.rev
a.tab_row("REVERSE", "right"); a.tab_row("CHORD", "right"); assert p.chord == 1
a.tab_row("CHORD", "left"); a.flush_bake()
a.tab_row("SPREAD"); assert a.confirm; a.confirm = None
# FX: effects that last as long as the finger is down
a.page = main.PAGES.index("FX"); a.draw()
x, y = control("hold", fx="stutter")
down(x, y); assert e.perf["stutter"]
up(); assert not e.perf["stutter"]
x, y, w, h, c = [h for h in a.hot if h[4]["kind"] == "xy"][0]
down(x + w - 14, y + h / 2); a.tick_sticks()
assert a.sticks["lx"] > 0.9 and abs(a.sticks["ly"]) < 0.05 and a.stick_level[a.stick_fx["lx"]] > 0.5
move(x + w / 2, y + 14); assert a.sticks["ly"] < -0.9
up(); a.tick_sticks(); assert a.sticks["lx"] == 0 and a.sticks["ly"] == 0 and not any(a.stick_level.values())
was = a.stick_fx["lx"]; a.tab_stick_fx("lx"); assert a.stick_fx["lx"] != was; a.stick_fx["lx"] = was
x, y = control("knob", target="REVERB SIZE")
down(x, y); move(x, y - 400); up(); assert e.reverb_size == 1.0
tap(x, y); a.tab_last = (None, 0.0); tap(x, y); tap(x, y); assert e.reverb_size == 0.4
# PROJ: the actions are one tap each
a.page = main.PAGES.index("PROJ"); a.draw()
a.tab_row("NAME"); assert a.namer; a.namer = None
a.tab_row("EXPORT AUDIO"); assert a.confirm; a.confirm = None
x, y = control("knob", target="MASTER")
down(x, y); move(x, y - 400); up(); assert e.master == 1.5
a.tab_set("MASTER", 0.8)
# dialogs sit in the middle and take taps
a.page = 0
a.confirm = ("REALLY?", lambda: names.setdefault("yes", True))
shot("confirm")
tap(320, 175 + a.dlg_dy); assert names.get("yes") and not a.confirm
a.confirm = ("REALLY?", lambda: names.setdefault("no", True))
tap(20, 20 + a.dlg_dy + 60); assert not a.confirm and "no" not in names
# the MIDI KNOBS list: tap a row to choose it, tap it again to change what the knob does
a.page = main.PAGES.index("PROJ"); a.draw()
a.tab_row("MIDI KNOBS"); assert a.knobs
shot("knobs")
tap(200, 64 + 2 * 20 + 10 + a.dlg_dy); assert a.knobs["row"] == 2
was = a.knob_fn[2]
tap(200, 64 + 2 * 20 + 10 + a.dlg_dy); assert a.knob_fn[2] != was
tap(200, 316 + a.dlg_dy); assert a.confirm; tap(320, 175 + a.dlg_dy); assert a.knob_fn == list(a.KNOB_DEFAULT)
tap(10, 20 + a.dlg_dy + 60); assert not a.knobs
# live chop: tap to drop points, FINISH keeps them, CANCEL drops them
a.page = 0; a.sel = 0
p0 = e.pads[0]
a.live = {"pad": 0, "path": p0.path, "n": len(p0.raw), "peaks": p0.peaks, "t0": main.time.time(), "marks": []}
shot("live")
tap(300, 170 + a.dlg_dy); assert a.live and len(a.live["marks"]) == 1
tap(100, 280 + a.dlg_dy); assert not a.live and e.pads[0].path == p0.path
# the handheld layout is untouched by any of this
assert main.PAGES == list(a.PAGE_NAMES)
print("ALL TABLET OK")
