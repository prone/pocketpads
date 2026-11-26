"""Controllers: the pad grid's numbering, knob mapping, the SMC-PAD's buttons and pad bank, MIDI decoding.
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
import main, midi
a = main.App(); e = a.eng
def send(*ev):
    a.midi.events.append(ev); a.tick_midi(); e.render(512)
def press(b): a.button(b, True); a.button(b, False)
# pad grid: up from pad 1 is pad 5
assert a.sel == 0; press("up"); assert a.sel == 4, a.sel; press("down"); assert a.sel == 0
# generic controller: first knob turned is knob 1 (volume)
send("cc", 21, 127); assert a.knob_ccs == [21] and e.pads[0].vol == 1.5
send("cc", 27, 127); assert not e.playing and a.knob_ccs == [21, 27]     # not a button without an SMC
send("note", 60, 100); assert a.sel == 8       # (60-36)%16 = 8, bank unchanged
send("note", 36, 100); assert a.sel == 0
send("mmc", 2); assert e.playing; send("mmc", 1); assert not e.playing
send("mmc", 6); assert e.rec; send("mmc", 6); assert not e.rec
# SMC-PAD
a.knob_ccs = []; a.midi.devices = ["SMC-PAD"]; a.midi_seen = 1
send("cc", 27, 127); assert e.playing; send("cc", 27, 0); assert e.playing
send("cc", 28, 127); assert not e.playing
assert a.knob_ccs == list(range(30, 46))
send("cc", 31, 127); assert e.pads[0].pitch == 24
send("cc", 32, 0); assert e.pads[0].filt == -100       # CC 32 is a knob here
pat = e.cur_pat; send("cc", 26, 127); e.render(512); assert (e.pending_pat if e.pending_pat is not None else e.cur_pat) == (pat + 1) % 8
send("cc", 25, 127)
send("note", 37, 100); assert a.sel == 1
send("note", 53, 100); assert a.sel == 17, a.sel        # pad bank B on the controller: our bank 2
send("note", 36, 100); assert a.sel == 0
a.midi_block = 1; send("note", 36, 100); assert a.sel == 0      # back to its first bank is pads 1-16, never the last bank
a.sel = 40; send("note", 37, 100); assert a.sel == 33           # same bank on the controller: the bank on screen stays
send("note", 52, 100); assert a.sel == 16; send("note", 36, 100); assert a.sel == 0
# second knob set
send("cc", 38, 64); assert 120 <= e.bpm <= 126, e.bpm      # knob bank: controller 38 is knob 9, tempo
send("cc", 28, 127); send("cc", 28, 0)
# knob list
a.page = main.PAGES.index("PROJ"); a.proj_row = a.PROJ_ROWS.index("MIDI KNOBS"); press("A"); assert a.knobs
send("cc", 33, 5); assert a.knobs["row"] == 3 and e.pads[0].dsend != round(5/127, 3)
press("right"); assert a.knob_fn[3] == "REVERB"
a.draw()
press("B"); assert not a.knobs
import json
st = json.load(open(os.path.join(app, "state.json"))); assert st["knob_fn"][3] == "REVERB" and st["knob_ccs"] == list(range(30, 46))
send("cc", 33, 127); assert e.pads[0].rsend == 1.0
# note repeat with a controller pad
e.repeat_on = True; send("note", 36, 100); assert a.midi_rep; send("off", 36); assert not a.midi_rep
a.page = 0; a.draw()
# state reload
b = main.App(); assert b.knob_fn[3] == "REVERB"
# decode
d = (midi.ctypes.c_ubyte * 12)(); d[8] = 5; assert midi.decode(midi.PROGRAM, d) == ("pc", 5)
assert midi.mmc(bytes([0xF0, 0x7F, 0x7F, 6, 2, 0xF7])) == ("mmc", 2)
buf = midi.ctypes.create_string_buffer(bytes([0xF0, 0x7F, 0, 6, 1, 0xF7]), 6)
d = (midi.ctypes.c_ubyte * 12)(*((6).to_bytes(4, "little") + midi.ctypes.addressof(buf).to_bytes(8, "little")))
assert midi.decode(midi.SYSEX, d) == ("mmc", 1)
print("midi tests passed")
# Bluetooth MIDI packets: a header byte, a timestamp before each message, running status allowed
P = midi.ble_parse
assert P(bytes([0x80, 0x80, 0x99, 36, 100, 0x81, 0x89, 36, 0])) == [("note", 36, 100), ("off", 36)]
assert P(bytes([0x80, 0x80, 0xB0, 30, 5, 31, 6, 0x82, 32, 7])) == [("cc", 30, 5), ("cc", 31, 6), ("cc", 32, 7)]
assert P(bytes([0x80, 0x80, 0xF0, 0x7F, 0x7F, 6, 2, 0x80, 0xF7])) == [("mmc", 2)]
assert P(bytes([0x80, 0x80, 0xFA, 0x80, 0xFC])) == [("start",), ("stop",)]
assert P(bytes([0x80, 0x80, 0x90, 40, 0, 0x80, 0xF8, 41, 9])) == [("off", 40), ("note", 41, 9)]
assert P(bytes([0x80, 0x80, 0xC0, 3])) == [("pc", 3)] and P(b"") == [] and P(bytes([0x80, 0x80, 0x90, 40])) == []
# the Pocket has pads only: it must not switch on the SMC-PAD's knob and button numbers
a.midi.devices = ["SMC-PAD Pocket"]
assert not a.smc()
print("bluetooth packet tests passed")
# SMC-PAD pad lights: the packets of its maker's protocol, and where a pad's colour is in its settings
H = bytes.fromhex
assert midi.smc_packet(0x11) == H("00591100 0000ff")
assert midi.smc_read(5, 0, 1009) == H("005923080000 05 00000000 f10300 06")
assert midi.smc_colour(0x418, (0, 0, 0)) == H("0059220b0000 05 18040000 030000 000000 db")
cfg = bytearray(midi.SMC_SETTINGS)
for k in range(32):
    cfg[0x414 + 26 * k:0x414 + 26 * k + 8] = bytes([9, 36 + k, 0, 0x7F, 0xF0, k, 0xF0, 0xFF])
pads = midi.smc_pads(bytes(cfg))
assert len(pads) == 32 and pads[0] == (0x418, (0xF0, 0, 0xF0)) and pads[16] == (0x5B8, (0xF0, 16, 0xF0))
cfg[0x414 + 26 * 5] = 0                     # settings laid out some other way: no lights, no wrong writes
assert midi.smc_pads(bytes(cfg)) == [] and midi.smc_pads(b"") == []
assert midi.smc_name("SMC-PAD") and not midi.smc_name("SMC-PAD Pocket") and not midi.smc_name("WIDI Jack")
# a pad of the bank on screen that sounds is flashed on the controller, in the bank its pads are showing
seen = []
a.ble.flash = lambda i, block=None: seen.append((i, block))
a.sel, a.midi_block = 16, None
a.tick_midi(); seen.clear()
a.eng.trigger(16 + 5); a.eng.trigger(3)     # pad 22 is on screen, pad 4 is not
a.tick_midi()
assert seen == [(5, None)], seen
a.midi_block = 1; a.eng.pad_flash[16] += 1; a.tick_midi()
assert seen[-1] == (0, 1)
class L:                                    # flash() reaches the right pad of the right bank
    def __init__(self): self.got = []
    def flash(self, k): self.got.append(k)
b = midi.BleMidi(midi.deque()); lt = L(); b._links = {"x": {"name": "SMC-PAD", "fd": -1, "lights": lt}, "y": {"name": "p", "fd": -1, "lights": None}}
b.flash(5); b.flash(2, 1); b.flash(2, 2)
assert lt.got == [5, 21, 18, 2], lt.got
print("pad light tests passed")
# the beat page follows the playing steps, sixteen at a time, and stays put when the beat stops
a.page = main.PAGES.index("BEAT")
pat = a.eng.patterns[a.eng.cur_pat]; pat["length"] = 64
a.cur_step, a.follow_after = 3, 0.0
a.eng.playing, a.eng.step = True, 37
a.draw_beat(); assert a.cur_step == 35, a.cur_step              # third page, same column
a.eng.step = 2; a.draw_beat(); assert a.cur_step == 3
a.eng.step = 50; a.press_beat("right", False); a.draw_beat()    # working on it: the view holds still
assert a.cur_step == 4
a.follow_after = 0.0; a.draw_beat(); assert a.cur_step == 52
a.eng.playing, a.eng.step = False, -1; a.draw_beat(); assert a.cur_step == 52
pat["length"] = 16; a.cur_step = 0
print("beat follow tests passed")
# plain MIDI bytes (Android, USB): running status, realtime in the middle, sysex split across calls
r = midi.RawMidi()
assert r.feed(bytes([0x99, 36, 100, 37, 0, 0xF8, 38, 90])) == [("note", 36, 100), ("off", 37), ("note", 38, 90)]
assert r.feed(bytes([0xB0, 30])) == [] and r.feed(bytes([5, 0x89, 36, 0])) == [("cc", 30, 5), ("off", 36)]
assert r.feed(bytes([0xF0, 0x7F, 0x7F, 6])) == [] and r.feed(bytes([2, 0xF7, 0xFA, 0xC0, 3])) == [("mmc", 2), ("start",), ("pc", 3)]
assert r.feed(bytes([0xE0, 1, 2, 0xFC, 40])) == [("stop",)]          # pitch bend is read and dropped; a stray byte too
print("raw midi tests passed")
