"""PocketPads: a sample sequencer for Linux handhelds, 64 pads in four banks of sixteen."""
import gc
import glob
import hashlib
import json
import math
import os
import re
import signal
import struct
import subprocess
import sys
import time
import traceback

import numpy as np
import pygame

import kit
import midi
import styles
from engine import (APP_DIR, BANK, BLOCK, CHORD_MAX_SECONDS, CHORDS, EXPORT_MAX_SECONDS, CHOKE_GROUPS, CRUSH_BITS, CRUSH_RATES, DELAY_TIMES, FITS, HOLD, MAX_STEPS, MIN_STEPS, MODES, NAME_CHARS, NAME_MAX, NBANKS, NPADS, NPAT,
                    PAT_NAMES, RATES, SONG_SLOTS, SR, STRETCH_MAX,
                    STRETCH_MIN, STUTTERS, VARIANT_MAX_SECONDS, Engine, clean_name,
                    find_chops, load_cached, load_wav, prune_cache, write_wav)

SAMPLES = os.path.join(APP_DIR, "samples")
PROJECTS = os.path.join(APP_DIR, "projects")
STATE = os.path.join(APP_DIR, "state.json")
PAGES = ["PLAY", "BEAT", "SONG", "SOUND", "FX", "PROJ"]
VERSION = "0.4"
CONVERT_EXTS = (".mp3", ".flac", ".ogg", ".m4a", ".aac", ".aif", ".aiff", ".opus")   # read through ffmpeg
CONVERT_SECONDS = 180
REC_SECONDS = 10
# Recording goes straight to an ALSA device. hw:0,0 at 48 kHz mono is the TrimUI Smart Pro's built-in microphone;
# set PP_REC_DEVICE (for example "default" or "plughw:1,0") on other hardware.
REC_DEVICE = os.environ.get("PP_REC_DEVICE", "hw:0,0")
SPREAD_MAX_SECONDS = 20   # SPREAD makes 16 pitched copies, so long samples would exhaust memory
STRETCH_MAX_SECONDS = 30  # stretching takes about 0.16 s per second of audio on the handheld
LONG_SAMPLE_SECONDS = 4   # above this, re-rendering a pad waits until the value stops changing
OPEN_GRAINBRICK = 42  # exit code the launcher script turns into "run GrainBrick, then come back"


def find_grainbrick():
    """GrainBrick's install folder, if it sits next to PocketPads in ports/."""
    override = os.environ.get("PP_GB_DIR")
    if override:
        return override if os.path.isdir(override) else None
    found = [d for d in glob.glob(os.path.join(os.path.dirname(APP_DIR), "grainbrick*"))
             if os.path.isfile(os.path.join(d, "main.py"))]
    return max(found, key=os.path.getmtime) if found else None


GB_DIR = find_grainbrick()
SLOTS = 16
KEY_CHORDS_MAX_SECONDS = 4           # KEY CHORDS bakes sixteen chords at once, so it takes shorter sounds than one CHORD

BG, PANEL, LINE = (17, 18, 23), (30, 32, 40), (72, 76, 90)
TEXT, DIM = (228, 229, 235), (128, 132, 148)
TEAL, ORANGE, RED, DARK = (62, 205, 182), (255, 152, 62), (236, 84, 84), (10, 11, 14)

DIRS = ("up", "down", "left", "right")
# Drum mode: these eight buttons play pads while L2 (the bank's first eight pads) or R2 (the other eight) is held.
DRUM_KEYS = ["left", "up", "right", "down", "Y", "X", "A", "B"]
DRUM_GLYPH = {"left": "<", "up": "^", "right": ">", "down": "v"}

KEYS = {pygame.K_UP: "up", pygame.K_DOWN: "down", pygame.K_LEFT: "left", pygame.K_RIGHT: "right",
        pygame.K_z: "A", pygame.K_x: "B", pygame.K_s: "X", pygame.K_a: "Y",
        pygame.K_q: "l1", pygame.K_w: "r1", pygame.K_1: "l2", pygame.K_2: "r2",
        pygame.K_RETURN: "start", pygame.K_TAB: "select"}
# SDL names buttons by position (its "a" is the bottom one). Handheld labels follow the
# Nintendo layout, where the right-hand button is A, so map position -> printed label.
PAD_BUTTONS = {"a": "B", "b": "A", "x": "Y", "y": "X", "leftshoulder": "l1", "rightshoulder": "r1",
               "start": "start", "back": "select", "dpup": "up", "dpdown": "down",
               "dpleft": "left", "dpright": "right"}


_MIXED = {}


def mix(a, b, t):
    """Blend two colours. Remembered, because the pages ask for the same few blends every frame."""
    key = (a, b, t)
    c = _MIXED.get(key)
    if c is None:
        if len(_MIXED) > 2000:
            _MIXED.clear()
        c = _MIXED[key] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return c


class App:
    def __init__(self):
        self.log = open(os.path.join(APP_DIR, "pocketpads.log"), "w", buffering=1)
        pygame.display.init()
        pygame.font.init()
        pygame.joystick.init()
        win = os.environ.get("PP_WINDOW")
        if win:
            w, h = (int(v) for v in win.split("x"))
            self.display = pygame.display.set_mode((w, h))
        else:
            self.display = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        # Everything is drawn on `screen`, a picture kept in memory, and copied to the display when it
        # changes. That lets a frame redraw only the areas that changed (see draw), which matters:
        # redrawing it all takes most of a frame on a handheld and starves the audio thread.
        self.screen = pygame.Surface(self.display.get_size())
        self.ops = None                 # while a frame is being described: its list of drawing steps
        self.frame = []                 # the last frame shown, as [(area, steps), ...]
        self.geom = {}                  # box geometry already worked out
        self.waves = {}                 # waveform pictures already drawn (see _wave)
        self.hint_rows = {}             # hint lines already laid out
        pygame.display.set_caption("PocketPads")
        pygame.mouse.set_visible(False)
        self.W, self.H = self.screen.get_size()
        self.u = min(self.W / 640.0, self.H / 360.0)
        self.ox = (self.W - 640 * self.u) / 2
        self.oy = (self.H - 360 * self.u) / 2
        self.fonts = {k: pygame.font.Font(None, max(8, int(s * self.u)))
                      for k, s in (("s", 13), ("h", 16), ("m", 17), ("l", 26), ("xl", 40))}
        self.cache = {}
        self.logline("screen %dx%d scale %.2f" % (self.W, self.H, self.u))
        self.splash("LOADING")
        splash_until = time.time() + (0 if os.environ.get("PP_SCRIPT") and not os.environ.get("PP_SPLASH") else 1.5)
        self.init_controllers()

        self.eng = Engine()
        self.page, self.sel, self.cur_step = 0, 0, 0
        self.sound_row, self.proj_row, self.fx_row = 0, 0, 0
        self.song_row, self.song_cur, self.copy_to = 0, 0, 1
        self.paint = None      # BEAT page: value being painted while A is held
        self.previewing = None # file being auditioned in the import list
        self.preview_end = 0.0 # when that audition runs out
        self.holding = {}      # button -> pad it is repeating
        self.recording = None
        self.gated = {}        # button -> HOLD pad it is sounding
        self.live = None       # live chop in progress
        self.resampling = None
        self.chop_mode, self.fit_idx = 0, 2
        self.key_idx = 0       # KEY CHORDS: major or minor
        self.fit_keep = False  # FIT TO TEMPO: True stretches (pitch kept), False repitches
        self.select_alone = False
        self.audition = None   # (pad, start time) while the SOUND page is playing a pad
        self.wheel, self.wheel_acc, self.wheel_seen = 0.0, 0.0, False
        self.in_beat = False           # sample picker: the highlighted file stands in for the pad, so the beat plays it
        self.trial = None              # while it does: (pad index, the pad as it was, file now on it)
        self.beat_name = False         # BEAT page: the cursor is on the row's name, left of step 1
        self.beat_acc = [0.0, 0.0]     # BEAT page: part-steps owed to the tempo (left stick) and volume (right stick)
        self.sticks = {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0}      # both sticks, -1 to 1 (up is negative)
        self.sticks_seen = set()
        self.stick_level = {}
        self.stick_fx = dict(self.STICK_DEFAULT)    # the effect chosen for each stick direction (FX page)
        self.perf_held = {}    # button -> performance effect it is holding
        self.slot = 1          # slot the PROJECT row points at
        self.loaded = 1        # slot the project in memory belongs to (autosave goes here)
        self.pending_bake = None
        self.is_repeat = False
        self.held = set()
        self.repeat = {}
        self.toast, self.toast_until = "", 0
        self.browser = None
        self.picker = None
        self.confirm = None
        self.namer = None      # the name being typed: {"text", "x", "y"} (PROJ page, NAME row)
        self.slot_names = {}   # slot -> (file time, name), so stepping through the slots does not reread files
        self.running = True
        self.exit_code = 0
        self.script = self.load_script()
        self.script_wait = 0

        os.makedirs(PROJECTS, exist_ok=True)
        os.makedirs(SAMPLES, exist_ok=True)
        self.kit_paths = kit.ensure_kit(os.path.join(SAMPLES, "kit"))
        self.midi = midi.MidiPort(self.logline)
        self.knob_ccs = []              # controller number of each knob: knob 1 first (see tick_midi)
        self.knob_fn = list(self.KNOB_DEFAULT)      # what each knob changes, by name
        self.knobs = None               # the MIDI KNOBS list while it is open: {"row": n}
        self.midi_last = ""             # the last message a controller sent, shown in that list
        self.midi_block = None          # which sixteen notes the controller's pads last sent (its own pad bank)
        self.midi_logged = set()        # messages already written to the log (each kind once)
        self.midi_rep = {}              # note -> pad, for pads repeating while a controller pad is held
        self.midi_seen = 0              # how many MIDI devices were connected last time we looked
        self.midi_held = {}             # note -> pad, for HOLD pads played from a controller
        try:
            with open(STATE) as f:
                state = json.load(f)
            self.slot = min(SLOTS, max(1, int(state.get("slot", 1))))
            self.midi.out_channel = int(state.get("midi_out", 0)) % 17
            self.in_beat = bool(state.get("in_beat", False))
            self.knob_ccs = [int(c) % 128 for c in state.get("knob_ccs", [])][:len(self.KNOB_DEFAULT)]
            for k, name in enumerate(list(state.get("knob_fn", []))[:len(self.knob_fn)]):
                if name in self.TARGETS:
                    self.knob_fn[k] = name
            for axis, name in dict(state.get("sticks") or {}).items():
                if axis in self.stick_fx and name in self.STICK_FX:
                    self.stick_fx[axis] = name
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self.ble = midi.BleMidi(self.midi.events, self.logline)     # Bluetooth controllers feed the same queue
        if not os.environ.get("PP_NOMIDI"):
            self.midi.start()
            self.ble.start()
        self.eng.on_note = self.midi.note
        self.loaded = self.slot
        if not (os.path.exists(self.slot_path()) and self.load_project(quiet=True)):
            self.new_project(quiet=True)
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))   # save on a system kill too
        while time.time() < splash_until:      # keep the title up long enough to read
            pygame.event.pump()
            time.sleep(0.05)
        # Keep the audio thread fed: hand over the interpreter quickly and avoid garbage-collection pauses.
        sys.setswitchinterval(0.001)
        gc.collect()
        gc.freeze()
        gc.disable()
        self.eng.start_audio(enable=not os.environ.get("PP_NOAUDIO"))
        if self.eng.audio_error:
            self.logline("audio error: " + self.eng.audio_error)
            self.say("NO AUDIO: " + self.eng.audio_error, 6)

    def logline(self, msg):
        self.log.write("%.2f %s\n" % (time.time() % 10000, msg))

    def splash(self, status):
        """Title screen shown while the kit and project load."""
        self.screen.fill(BG)
        try:
            art = pygame.image.load(os.path.join(APP_DIR, "art", "pocketpads.png"))
            h = int(210 * self.u)
            w = int(art.get_width() * h / art.get_height())
            art = pygame.transform.scale(art, (w, h))          # nearest-neighbour keeps the pixels crisp
            self.screen.blit(art, (int(self.ox + 320 * self.u - w / 2), int(self.oy + 28 * self.u)))
            pygame.draw.rect(self.screen, LINE, (int(self.ox + 320 * self.u - w / 2) - 2, int(self.oy + 28 * self.u) - 2,
                                                 w + 4, h + 4), max(2, self.S(1)), self.S(4))
        except (pygame.error, OSError) as e:
            self.logline("splash art missing: %s" % e)
        self.text("PocketPads", 320, 272, "xl", TEXT, "c")
        self.text("VERSION %s" % VERSION, 320, 302, "m", ORANGE, "c")
        self.text(status, 320, 332, "s", DIM, "c")
        self.present()

    # ------------------------------------------------------------ input
    def init_controllers(self):
        self.controllers, self.joysticks = [], []
        try:
            from pygame._sdl2 import controller
            controller.init()
            for i in range(pygame.joystick.get_count()):
                if controller.is_controller(i):
                    c = controller.Controller(i)
                    self.controllers.append(c)
                    self.logline("controller %d: %s" % (i, c.name))
        except Exception as e:
            self.logline("controller api unavailable: %r" % e)
        if not self.controllers:
            for i in range(pygame.joystick.get_count()):
                j = pygame.joystick.Joystick(i)
                j.init()
                self.joysticks.append(j)
                self.logline("joystick %d: %s buttons=%d hats=%d" % (i, j.get_name(), j.get_numbuttons(), j.get_numhats()))
        self.btn_names = {}
        for name, label in PAD_BUTTONS.items():
            const = getattr(pygame, "CONTROLLER_BUTTON_" + name.upper().replace("DP", "DPAD_"), None)
            if const is not None:
                self.btn_names[const] = label
        self.trig_state = {}
        self.hat_state = set()

    def handle_event(self, e):
        if e.type == pygame.QUIT:
            self.running = False
        elif e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.running = False
            elif e.key in KEYS:
                self.button(KEYS[e.key], True)
        elif e.type == pygame.KEYUP and e.key in KEYS:
            self.button(KEYS[e.key], False)
        elif e.type in (pygame.CONTROLLERBUTTONDOWN, pygame.CONTROLLERBUTTONUP):
            down = e.type == pygame.CONTROLLERBUTTONDOWN
            name = self.btn_names.get(e.button)
            if down:
                self.logline("cbutton %s -> %s" % (e.button, name))
            if name:
                self.button(name, down)
        elif e.type == pygame.CONTROLLERAXISMOTION:
            stick = {pygame.CONTROLLER_AXIS_LEFTX: "lx", pygame.CONTROLLER_AXIS_LEFTY: "ly",
                     pygame.CONTROLLER_AXIS_RIGHTX: "rx", pygame.CONTROLLER_AXIS_RIGHTY: "ry"}.get(e.axis)
            if stick:
                self.sticks[stick] = e.value / 32768.0
                if stick not in self.sticks_seen and abs(e.value) > 16000:
                    self.sticks_seen.add(stick)
                    self.logline("stick axis seen: %s" % stick)
            if e.axis == pygame.CONTROLLER_AXIS_RIGHTX:
                self.wheel = e.value / 32768.0
                if not self.wheel_seen and abs(self.wheel) > 0.5:
                    self.wheel_seen = True
                    self.logline("right stick seen: %.2f" % self.wheel)
            name = {pygame.CONTROLLER_AXIS_TRIGGERLEFT: "l2", pygame.CONTROLLER_AXIS_TRIGGERRIGHT: "r2"}.get(e.axis)
            if name:
                down = e.value > 12000
                if self.trig_state.get(name, False) != down:
                    self.trig_state[name] = down
                    self.button(name, down)
        elif not self.controllers and e.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
            down = e.type == pygame.JOYBUTTONDOWN
            if down:
                self.logline("jbutton %s" % e.button)
            name = self.joy_map().get(str(e.button))
            if name:
                self.button(name, down)
        elif not self.controllers and e.type == pygame.JOYHATMOTION:
            now = set()
            if e.value[0] < 0: now.add("left")
            if e.value[0] > 0: now.add("right")
            if e.value[1] > 0: now.add("up")
            if e.value[1] < 0: now.add("down")
            for d in now - self.hat_state:
                self.button(d, True)
            for d in self.hat_state - now:
                self.button(d, False)
            self.hat_state = now

    def joy_map(self):
        if not hasattr(self, "_joy_map"):
            try:
                with open(os.path.join(APP_DIR, "buttons.json")) as f:
                    self._joy_map = json.load(f)
            except (OSError, ValueError):
                self._joy_map = {}
        return self._joy_map

    def button(self, name, down):
        if down:
            if name in self.held:
                return
            self.held.add(name)
            self.select_alone = name == "select"       # any other press while SELECT is down makes it a combo
            if name in DIRS:
                self.repeat[name] = time.time() + 0.32
            self.press(name)
        else:
            self.held.discard(name)
            self.repeat.pop(name, None)
            if name == "A":
                self.paint = None
            if name == "select" and self.select_alone and PAGES[self.page] == "SOUND" and not self.modal():
                self.toggle_audition()                 # a tap of SELECT on its own: play / stop
            pad = self.holding.pop(name, None)
            if pad is not None:
                self.eng.release(pad)
            pad = self.gated.pop(name, None)
            if pad is not None:
                self.eng.note_off(pad)
            fx = self.perf_held.pop(name, None)
            if fx:
                self.eng.perf[fx] = False

    def modal(self):
        """True while a dialog, list or recording owns the buttons."""
        return bool(self.confirm or self.browser or self.picker or self.recording or self.live or self.namer or self.knobs)

    def tick_repeat(self):
        now = time.time()
        for name, t in list(self.repeat.items()):
            if now >= t:
                self.repeat[name] = now + 0.07
                self.press(name, repeat=True)

    def tick_sticks(self):
        """On the FX page each stick direction plays the effect chosen for it: the further the push,
        the more effect, and letting go ends it."""
        e = self.eng
        v = dict.fromkeys(("FILTER", "TEMPO", "PAN", "FADE OUT", "ECHO", "REVERB", "STUTTER", "CRUSH", "TAPE STOP"), 0.0)
        if PAGES[self.page] == "FX" and not self.modal():
            for axis, name in self.stick_fx.items():
                x = self.sticks[axis] * (-1.0 if axis[1] == "y" else 1.0)      # up counts as positive
                x = 0.0 if abs(x) < 0.25 else math.copysign(min(1.0, (abs(x) - 0.25) / 0.7), x)    # slack at the centre
                if len(self.STICK_FX[name]) == 2:
                    v[name] = x if abs(x) > abs(v[name]) else v[name]
                elif name in v:
                    v[name] = max(v[name], abs(x))
        e.fx_filt, e.fx_tempo, e.fx_pan = v["FILTER"], v["TEMPO"], v["PAN"]
        e.fx_fade, e.fx_echo, e.fx_verb = v["FADE OUT"], v["ECHO"], v["REVERB"] * 0.8
        e.fx_stut, e.fx_crush, e.fx_tape = v["STUTTER"], v["CRUSH"], v["TAPE STOP"] > 0.5
        self.stick_level = v

    def tick_beat_sticks(self, dt):
        """On the BEAT page the left stick changes the tempo and the right stick the selected pad's volume.
        Push right or up for more, left or down for less; the further the push, the faster it moves."""
        if PAGES[self.page] != "BEAT" or self.modal():
            self.beat_acc = [0.0, 0.0]
            return
        e, k = self.eng, self.sticks
        for i, (x, y, slow, fast) in enumerate(((k["lx"], k["ly"], 1.0, 20.0), (k["rx"], k["ry"], 3.0, 40.0))):
            v = x if abs(x) >= abs(y) else -y           # up counts as more
            if abs(v) < 0.3:
                self.beat_acc[i] = 0.0
                continue
            push = min(1.0, (abs(v) - 0.3) / 0.65)
            self.beat_acc[i] += dt * (slow + (fast - slow) * push * push)
            n = int(self.beat_acc[i])
            if not n:
                continue
            self.beat_acc[i] -= n
            self.select_alone = False
            d = n if v > 0 else -n
            if i == 0:
                e.bpm = float(min(300, max(40, round(e.bpm) + d)))
                self.say("TEMPO %d BPM" % e.bpm, 1.0)
            else:
                p = e.pads[self.sel]
                p.vol = round(min(1.5, max(0.0, p.vol + 0.01 * d)), 2)
                self.say("PAD %d %s VOLUME %d" % (self.sel + 1, p.name[:16], round(p.vol * 100)), 1.0)

    def tick_wheel(self, dt):
        """The right stick is a data wheel: push it sideways to change the selected value."""
        v = self.wheel
        page = PAGES[self.page]
        idle = page not in ("SOUND", "PROJ") and not (page == "SONG" and self.SONG_ROWS[self.song_row] == "LENGTH")
        if abs(v) < 0.25 or self.modal() or idle:
            self.wheel_acc = 0.0
            return
        push = (abs(v) - 0.25) / 0.75
        self.wheel_acc += dt * (2.0 + 38.0 * push * push)      # 2 to 40 changes a second
        self.select_alone = False               # using the wheel with SELECT held is not a SELECT tap
        while self.wheel_acc >= 1.0:
            self.wheel_acc -= 1.0
            self.press("right" if v > 0 else "left", repeat=True)

    # ------------------------------------------------------------ actions
    def say(self, msg, secs=1.6):
        self.toast, self.toast_until = msg, time.time() + secs

    def slot_path(self, slot=None):
        return os.path.join(PROJECTS, "P%02d.json" % (slot or self.slot))

    def slot_name(self, slot):
        """The name of the project in a slot ("" if it has none). Only the start of the file is read."""
        if slot == self.loaded:
            return self.eng.name
        path = self.slot_path(slot)
        try:
            stamp = os.path.getmtime(path)
            hit = self.slot_names.get(slot)
            if hit is None or hit[0] != stamp:
                with open(path) as f:
                    m = re.match(r'\{"version": \d+, "name": "([^"\\]*)"', f.read(160))
                hit = self.slot_names[slot] = (stamp, clean_name(m.group(1)) if m else "")
            return hit[1]
        except OSError:
            return ""

    def slot_label(self, slot):
        """A slot as shown to the user: "P03 NIGHT DRIVE", or "P03" for a project without a name."""
        name = self.slot_name(slot)
        return "P%02d %s" % (slot, name) if name else "P%02d" % slot

    # ------------------------------------------------------------ naming a project
    NAME_ROWS = [NAME_CHARS[i:i + 10] for i in range(0, len(NAME_CHARS), 10)]

    def start_naming(self):
        self.namer = {"text": self.eng.name, "x": 0, "y": 0}

    def press_namer(self, b):
        nm = self.namer
        if b in DIRS:
            if b in ("left", "right"):
                nm["x"] = (nm["x"] + (1 if b == "right" else -1)) % 10
            else:
                nm["y"] = (nm["y"] + (1 if b == "down" else -1)) % len(self.NAME_ROWS)
        elif b == "A":
            if len(nm["text"]) < NAME_MAX:
                nm["text"] += self.NAME_ROWS[nm["y"]][nm["x"]]
        elif b == "X":
            nm["text"] = nm["text"][:-1]
        elif b == "B":
            self.namer = None
        elif b == "start":
            self.namer = None
            self.eng.name = clean_name(nm["text"])
            self.save_project(quiet=True, autosave=True)      # so the name is on the card, and in the slot list, at once
            self.say("P%02d IS NOW %s" % (self.loaded, self.eng.name) if self.eng.name else "P%02d HAS NO NAME NOW" % self.loaded, 3)

    def save_project(self, quiet=False, autosave=False):
        """Save to the slot the PROJECT row shows; autosave always goes to the slot the project came from."""
        self.trial_end()                # a sound only being tried in the picker is not part of the project
        slot = self.loaded if autosave else self.slot
        try:
            self.flush_bake()
            self.eng.save(self.slot_path(slot))
            self.loaded = slot
            self.save_state(slot)
            if not quiet:
                self.say("SAVED %s" % self.slot_label(slot))
        except OSError as e:
            self.say("SAVE FAILED: %s" % e, 4)

    def save_state(self, slot=None):
        """Settings that belong to the handheld, not to one project."""
        with open(STATE, "w") as f:
            json.dump({"slot": self.loaded if slot is None else slot, "midi_out": self.midi.out_channel,
                       "sticks": self.stick_fx, "in_beat": self.in_beat,
                       "knob_ccs": self.knob_ccs, "knob_fn": self.knob_fn}, f)

    def load_project(self, quiet=False):
        """Load the slot the PROJECT row shows. Returns True on success."""
        path = self.slot_path()
        if not os.path.exists(path):
            self.say("P%02d IS EMPTY" % self.slot)
            return False
        try:
            self.eng.stop()
            self.pending_bake = None
            missing = self.eng.load(path)
        except (OSError, ValueError) as e:
            # Keep the unreadable file aside so autosave cannot destroy what might be recoverable.
            self.logline("load failed: %s: %s" % (path, e))
            try:
                os.replace(path, path + ".damaged")
            except OSError:
                pass
            self.new_project(quiet=True)
            self.say("P%02d WAS DAMAGED - KEPT AS .damaged" % self.slot, 5)
            return False
        self.loaded = self.slot
        self.sel = self.cur_step = 0
        self.midi_held.clear()
        if missing:
            self.say("%d SAMPLE(S) MISSING" % len(missing), 4)
        elif not quiet:
            self.say("LOADED %s" % self.slot_label(self.slot))
        return True

    def free_slot(self):
        """The first slot with no project in it, or None."""
        for n in range(1, SLOTS + 1):
            if n != self.loaded and not os.path.exists(self.slot_path(n)):
                return n
        return None

    def save_copy(self):
        """SAVE A COPY: save the open project as it was last saved, then carry on in a copy in the first empty slot."""
        new, old = self.free_slot(), self.loaded
        if new is None:
            self.say("ALL %d SLOTS ARE USED - NO ROOM FOR A COPY" % SLOTS, 4)
            return

        def go():
            self.slot = new
            self.save_project(quiet=True)
            if self.loaded != new:          # the save failed (it has said so): stay where we were
                self.slot = old
            elif os.path.exists(self.slot_path(old)):
                self.say("COPY SAVED AS P%02d. YOU ARE NOW IN P%02d (P%02d IS KEPT)" % (new, new, old), 4.5)
            else:                           # the old slot had never been saved, so there is nothing kept in it
                self.say("SAVED AS P%02d. YOU ARE NOW IN P%02d" % (new, new), 4)
        self.confirm = ("SAVE A COPY AS P%02d AND SWITCH TO IT?" % new, go)

    def ask_open(self):
        """OPEN: a list of the saved projects."""
        items = [("%s%s" % (self.slot_label(n), "   (open now)" if n == self.loaded else ""), n)
                 for n in range(1, SLOTS + 1) if n == self.loaded or os.path.exists(self.slot_path(n))]
        if len(items) < 2:
            self.say("NO OTHER SAVED PROJECTS YET", 3)
            return

        def chosen(n):
            if n == self.loaded:
                self.say("%s IS ALREADY OPEN" % self.slot_label(n), 3)
                return

            def go():
                # Check the file reads before letting go of the open project: a damaged one is set aside
                # and nothing else changes.
                path = self.slot_path(n)
                try:
                    with open(path) as f:
                        if not isinstance(json.load(f), dict):
                            raise ValueError("not a project")
                except (OSError, ValueError) as err:
                    self.logline("open failed: %s: %s" % (path, err))
                    try:
                        os.replace(path, path + ".damaged")
                    except OSError:
                        pass
                    self.say("P%02d IS DAMAGED - KEPT AS .damaged. STILL IN P%02d" % (n, self.loaded), 5)
                    return
                self.slot = n
                self.load_project()
            self.confirm = ("OPEN %s?\nUNSAVED CHANGES TO P%02d ARE LOST" % (self.slot_label(n), self.loaded), go)
        self.picker = {"title": "OPEN A PROJECT", "items": items, "idx": 0, "fn": chosen,
                       "hint": "A open    B cancel    save first if you want to keep your changes"}

    def ask_new_project(self):
        """NEW: pick the starter kit or a style, then start it in the first empty slot."""
        items = [("STARTER KIT   drums and bass with a demo beat", "")]
        items += [("%s   %s" % (name, styles.ABOUT[name]), name) for name in styles.ORDER]
        self.picker = {"title": "NEW PROJECT: PICK A KIT AND SONG", "items": items, "idx": 0, "fn": self.ask_new_slot,
                       "hint": "A choose    B cancel    each style is a kit, patterns and a full song"}

    def ask_new_slot(self, style):
        """NEW keeps the open project and starts the new one in the first empty slot."""
        what = "%s PROJECT" % style if style else "NEW PROJECT"
        empty = [n for n in range(1, SLOTS + 1) if n != self.loaded and not os.path.exists(self.slot_path(n))]
        if not empty:
            self.confirm = ("ALL %d SLOTS ARE USED. REPLACE P%02d WITH A %s?" % (SLOTS, self.slot, what),
                            lambda: self.new_project(style=style))
            return

        def go():
            self.save_project(quiet=True, autosave=True)       # keep what is open
            self.slot = empty[0]
            self.new_project(quiet=True, style=style)
            self.save_project(quiet=True)
            self.say("%s IN P%02d (P%02d SAVED)%s" % (what, empty[0], keep, ". START PLAYS THE SONG" if style else ""), 4.5)
        keep = self.loaded
        self.confirm = ("START A %s IN P%02d?" % (what, empty[0]), go)

    def new_project(self, quiet=False, style=None):
        self.eng.stop()
        self.loaded, self.pending_bake = self.slot, None
        self.sel = self.cur_step = 0
        self.midi_held.clear()
        if style:
            # A style is a ready-made project: its kit (made on first use, which takes a moment), patterns and song.
            self.say("MAKING THE %s KIT ..." % style, 2)
            self.draw()
            self.eng.from_dict(styles.project(style, os.path.join(SAMPLES, "styles")))
            if not quiet:
                self.say("%s PROJECT: START PLAYS THE SONG" % style, 4)
            return
        self.eng.from_dict({"bpm": 112})
        pads = self.eng.pads
        for p, path in zip(pads, self.kit_paths):
            p.load(path)
        pads[4].choke = pads[5].choke = 1          # closed hat cuts the open hat
        pads[1].rsend, pads[2].rsend = 0.15, 0.25  # a little room on snare and clap
        pads[11].dsend, pads[14].dsend, pads[14].rsend = 0.25, 0.3, 0.15
        kit.demo_patterns(self.eng.patterns)
        if not quiet:
            self.say("NEW PROJECT (STARTER KIT)")

    def press(self, b, repeat=False):
        e = self.eng
        sel = "select" in self.held
        self.is_repeat = repeat
        if self.recording:
            if b in ("A", "B", "start"):
                self.stop_record()
            return
        if self.live:
            if b in ("A", "Y", "l2", "r2"):
                self.live_mark()
            elif b in ("B", "start"):
                self.live_finish()
            elif b == "X":
                self.live_finish(cancel=True)
            return
        if self.resampling and b == "start" and not sel:
            self.finish_resample()
            return
        if self.confirm:
            if b == "A":
                msg, fn = self.confirm
                self.confirm = None
                fn()
            elif b == "B":
                self.confirm = None
            return
        if self.namer:
            return self.press_namer(b)
        if self.browser:
            return self.press_browser(b)
        if self.picker:
            return self.press_picker(b)
        if self.knobs:
            return self.press_knobs(b)
        # global controls
        if b == "start":
            if sel:
                self.running = False
            else:
                e.toggle_play()
            return
        if b in ("l1", "r1"):
            d = -1 if b == "l1" else 1
            if sel and e.playing and e.song_on and e.song:
                self.say("SONG MODE IS ON: PATTERNS FOLLOW THE CHAIN", 2.5)
            elif sel:
                e.select_pattern(((e.pending_pat if e.pending_pat is not None else e.cur_pat) + d) % NPAT)
            else:
                self.page = (self.page + d) % len(PAGES)
            return
        if sel and b == "Y":
            self.flush_bake()
            self.sel = (self.sel + BANK) % NPADS
            self.say("BANK %d: PADS %d-%d" % (self.sel // BANK + 1, self.base() + 1, self.base() + BANK), 1.5)
            return
        if sel and b in ("up", "down"):
            e.bpm = float(min(300, max(40, round(e.bpm) + (1 if b == "up" else -1))))
            return
        getattr(self, "press_" + PAGES[self.page].lower())(b, sel)

    def base(self):
        """Index of the first pad in the bank on screen (the bank the selected pad is in)."""
        return self.sel // BANK * BANK

    def bank_pads(self):
        b = self.base()
        return self.eng.pads[b:b + BANK]

    def step_pad(self, d):
        """Select the previous or next pad, staying inside the bank."""
        b = self.base()
        self.flush_bake()                   # finish the pad being left before its turn is lost
        self.sel = b + (self.sel - b + d) % BANK

    def press_play(self, b, sel):
        e = self.eng
        for mod, base in (("l2", 0), ("r2", 8)):
            if mod in self.held and b in DRUM_KEYS:
                if self.is_repeat:          # a held d-pad auto-repeats for menus; a drum hit must not
                    return
                i = self.base() + base + DRUM_KEYS.index(b)
                self.sel = i
                self.hit(i, b)
                return
        if sel and b == "X":
            e.repeat_on = not e.repeat_on
            self.say("NOTE REPEAT %s: HOLD A PAD" % RATES[e.repeat_div][0] if e.repeat_on else "NOTE REPEAT OFF")
        elif sel and b in ("left", "right"):
            e.repeat_div = (e.repeat_div + (1 if b == "right" else -1)) % len(RATES)
            self.say("REPEAT RATE %s" % RATES[e.repeat_div][0], 1.0)
        elif b in DIRS:
            r, c = divmod(self.sel % BANK, 4)
            if b == "up": r = (r + 1) % 4       # pad 1 is bottom left, so up the screen is up the numbers
            if b == "down": r = (r - 1) % 4
            if b == "left": c = (c - 1) % 4
            if b == "right": c = (c + 1) % 4
            self.sel = self.base() + r * 4 + c
        elif b == "A":
            self.hit(self.sel, b)
        elif b == "B":
            p = e.pads[self.sel]
            p.mute = not p.mute
        elif b == "X":
            e.rec = not e.rec
            self.say("RECORD ON: PLAY PADS WHILE RUNNING" if e.rec else "RECORD OFF")
        elif b == "Y":
            self.page = PAGES.index("SOUND")

    def hit(self, pad, button):
        """Play a pad; with note repeat on it keeps firing until the button is released."""
        if self.eng.repeat_on:
            self.eng.hold(pad)
            self.holding[button] = pad
        else:
            self.eng.trigger(pad)
            if self.eng.pads[pad].mode == HOLD:
                self.gated[button] = pad

    def press_beat(self, b, sel):
        e = self.eng
        pat = e.patterns[e.cur_pat]
        row, notes = pat["steps"][self.sel], pat["notes"][self.sel]
        length = pat["length"]
        self.cur_step = min(self.cur_step, length - 1)
        if self.beat_name and b in ("A", "Y", "left", "right", "l2", "r2") and not (sel and b == "A"):
            # On the row's name, left of step 1: A picks a new sound for the pad, and any sideways move goes back to the steps.
            if b == "A":
                self.open_browser()
            elif b != "Y":
                self.beat_name = False
                self.cur_step = {"right": 0, "r2": 0, "left": length - 1, "l2": length - 4}[b]
            return
        if sel and b in ("left", "right", "l2", "r2"):
            # Step notes: SELECT + left/right moves this step a semitone, SELECT + L2/R2 an octave.
            d = {"left": -1, "right": 1, "l2": -12, "r2": 12}[b]
            if e.pads[self.sel].raw is None:
                self.say("THIS PAD HAS NO SOUND")
                return
            if e.pads[self.sel].seconds() > VARIANT_MAX_SECONDS:
                self.say("THIS SOUND IS TOO LONG FOR STEP NOTES (%d s MAX)" % VARIANT_MAX_SECONDS, 3)
                return
            if not row[self.cur_step]:
                row[self.cur_step] = 1
            e.set_note(e.cur_pat, self.sel, self.cur_step, notes[self.cur_step] + d)
            if not e.playing:
                e.preview(self.sel, e.pads[self.sel].variants.get(notes[self.cur_step], e.pads[self.sel].buf))
        elif b == "up":
            self.step_pad(-1)
        elif b == "down":
            self.step_pad(1)
        elif b in ("left", "right", "l2", "r2"):
            d = {"left": -1, "right": 1, "l2": -4, "r2": 4}[b]
            if self.paint is not None and "A" in self.held:
                # Holding A while moving paints (or wipes) every step the cursor passes over.
                for _ in range(abs(d)):
                    self.cur_step = (self.cur_step + (1 if d > 0 else -1)) % length
                    if not self.paint:
                        row[self.cur_step] = notes[self.cur_step] = 0
                    elif not row[self.cur_step]:         # accents already there are left alone
                        row[self.cur_step] = self.paint
                e.refresh_variants(self.sel)
            elif b == "left" and self.cur_step == 0:
                self.beat_name = True               # one more to the left of step 1 is the row's name
            else:
                self.cur_step = (self.cur_step + d) % length
        elif b == "A" and sel:
            def clear_track():
                row[:] = [0] * len(row)
                notes[:] = [0] * len(notes)
                e.refresh_variants(self.sel)
            self.confirm = ("CLEAR TRACK %d?" % (self.sel + 1), clear_track)
        elif b == "X" and sel:
            self.ask_clear_pattern()
        elif b == "A":
            row[self.cur_step] = 0 if row[self.cur_step] else 1
            self.paint = row[self.cur_step]
            if not row[self.cur_step]:
                notes[self.cur_step] = 0
            e.refresh_variants(self.sel)
            if row[self.cur_step] and not e.playing:
                e.trigger(self.sel)
        elif b == "Y":
            row[self.cur_step] = 1 if row[self.cur_step] == 2 else 2
            e.refresh_variants(self.sel)
        elif b == "B":
            p = e.pads[self.sel]
            p.mute = not p.mute
        elif b == "X":
            p = e.pads[self.sel]
            p.solo = not p.solo

    # ------------------------------------------------------------ song page
    SONG_ROWS = ["SONG MODE", "CHAIN", "COPY PATTERN", "CLEAR PATTERN", "LENGTH"]

    def press_song(self, b, sel):
        e = self.eng
        row = self.SONG_ROWS[self.song_row]
        song = list(e.song)             # edited as a copy and swapped in whole (the audio thread reads it)
        slots = min(len(song) + 1, SONG_SLOTS)
        self.song_cur = min(self.song_cur, slots - 1)
        if b == "up":
            self.song_row = (self.song_row - 1) % len(self.SONG_ROWS)
        elif b == "down":
            self.song_row = (self.song_row + 1) % len(self.SONG_ROWS)
        elif row == "SONG MODE" and b in ("left", "right", "A"):
            if not song:
                self.say("THE CHAIN IS EMPTY - ADD PATTERNS BELOW FIRST", 3)
            else:
                e.song_on = not e.song_on
                e.song_pos = 0
                self.say("SONG MODE ON: PLAYS THE CHAIN IN ORDER" if e.song_on else "SONG MODE OFF: LOOPS ONE PATTERN", 2.5)
        elif row == "CHAIN":
            if b == "left":
                self.song_cur = (self.song_cur - 1) % slots
            elif b == "right":
                self.song_cur = (self.song_cur + 1) % slots
            elif b in ("A", "Y"):
                d = 1 if b == "A" else -1
                if self.song_cur >= len(song):          # the empty slot at the end: add the current pattern
                    song.append(e.cur_pat if e.pending_pat is None else e.pending_pat)
                    self.song_cur = min(len(song), SONG_SLOTS - 1)
                    if len(song) == SONG_SLOTS:
                        self.say("THE CHAIN IS FULL (%d)" % SONG_SLOTS)
                else:
                    song[self.song_cur] = (song[self.song_cur] + d) % NPAT
            elif b == "X" and self.song_cur < len(song):
                if len(song) >= SONG_SLOTS:
                    self.say("THE CHAIN IS FULL (%d)" % SONG_SLOTS)
                else:
                    song.insert(self.song_cur, song[self.song_cur])     # repeat this slot
            elif b == "B" and song:
                del song[min(self.song_cur, len(song) - 1)]
                if not song:
                    e.song_on = False
            e.song = song
        elif row == "COPY PATTERN":
            if b in ("left", "right"):
                self.copy_to = (self.copy_to + (1 if b == "right" else -1)) % NPAT
            elif b == "A":
                src, dst = e.cur_pat, self.copy_to
                if src == dst:
                    self.say("PICK A DIFFERENT PATTERN TO COPY TO (LEFT / RIGHT)", 2.5)
                    return

                def go():
                    e.copy_pattern(src, dst)
                    e.select_pattern(dst)
                    self.say("PATTERN %s COPIED TO %s" % (PAT_NAMES[src], PAT_NAMES[dst]), 2.5)
                used = any(any(r) for r in e.patterns[dst]["steps"])
                if used:
                    self.confirm = ("COPY %s OVER %s? %s HAS STEPS" % (PAT_NAMES[src], PAT_NAMES[dst], PAT_NAMES[dst]), go)
                else:
                    go()
        elif row == "CLEAR PATTERN" and b == "A":
            self.ask_clear_pattern()
        elif row == "LENGTH" and b in ("left", "right"):
            self.change_length(1 if b == "right" else -1)

    def ask_clear_pattern(self):
        index = self.eng.cur_pat        # fixed now: the playing pattern can change while the question is up
        self.confirm = ("CLEAR PATTERN %s?" % PAT_NAMES[index], lambda: self.eng.clear_pattern(index))

    def change_length(self, d):
        """Pattern length: a bar (16 steps) at a time, or one step at a time with Y held."""
        pat = self.eng.patterns[self.eng.cur_pat]
        if "Y" in self.held:
            n = pat["length"] + d
        else:
            n = (pat["length"] // 16 + d) * 16 if d > 0 else ((pat["length"] - 1) // 16) * 16
        pat["length"] = int(min(MAX_STEPS, max(MIN_STEPS, n)))

    def draw_song(self):
        e = self.eng
        song = e.song
        self.song_cur = min(self.song_cur, min(len(song) + 1, SONG_SLOTS) - 1)
        pat = e.patterns[e.cur_pat]
        steps = e.song_steps()
        secs = steps * 60.0 / e.bpm / 4.0
        vals = {"SONG MODE": ("ON  -  plays the chain in order" if e.song_on else "OFF  -  loops pattern %s" % PAT_NAMES[e.cur_pat]),
                "CHAIN": ("%d pattern%s, %d:%02d long" % (len(song), "" if len(song) == 1 else "s", secs // 60, secs % 60)) if song else "empty  -  press A to add pattern %s" % PAT_NAMES[e.cur_pat],
                "COPY PATTERN": "%s  to  %s   -  A to copy" % (PAT_NAMES[e.cur_pat], PAT_NAMES[self.copy_to]),
                "CLEAR PATTERN": "empty pattern %s   -  A" % PAT_NAMES[e.cur_pat],
                "LENGTH": "%d steps  (pattern %s, %d bar%s%s)" % (pat["length"], PAT_NAMES[e.cur_pat], pat["length"] // 16,
                                                                "" if pat["length"] // 16 == 1 else "s",
                                                                " + %d step%s" % (pat["length"] % 16, "" if pat["length"] % 16 == 1 else "s") if pat["length"] % 16 else "")}
        ys = {"SONG MODE": 36, "CHAIN": 62, "COPY PATTERN": 232, "CLEAR PATTERN": 254, "LENGTH": 276}
        for i, name in enumerate(self.SONG_ROWS):
            y, on = ys[name], i == self.song_row
            self.box(8, y, 624, 22, PANEL if on else None, ORANGE if on else None, 4, 2)
            self.text(name, 20, y + 11, "m", ORANGE if on else DIM)
            self.text(vals[name], 170, y + 11, "m", TEXT)
        # the chain: 16 slots to a line, the slot being edited outlined, the one playing lit
        chain_on = self.SONG_ROWS[self.song_row] == "CHAIN"
        for i in range(min(SONG_SLOTS, len(song) + 1)):
            r, c = divmod(i, 16)
            x, y = 20 + c * 37.6, 92 + r * 35
            if i < len(song):
                playing = e.playing and e.song_on and e.song_slot == i
                self.box(x, y, 34, 30, TEAL if playing else PANEL, LINE, 4)
                self.text(PAT_NAMES[song[i]], x + 17, y + 13, "l", DARK if playing else TEXT, "c")
                self.text(str(i + 1), x + 17, y + 25, "s", DARK if playing else DIM, "c")
            else:
                self.box(x, y, 34, 30, None, LINE, 4)
                self.text("+", x + 17, y + 15, "l", DIM, "c")
            if chain_on and i == self.song_cur:
                self.box(x - 1.5, y - 1.5, 37, 33, None, ORANGE, 5, 2)
        if chain_on:
            self.hints("left right move   A next pattern / add   Y previous   X repeat slot   B remove   SELECT+L1 R1 pattern")
        elif self.SONG_ROWS[self.song_row] == "LENGTH":
            self.hints("left right one bar   hold Y for one step   SELECT+L1 R1 pattern")
        else:
            self.hints("up down choose   left right change   A confirm   SELECT+L1 R1 pattern")

    SOUND_ROWS = ["WAVEFORM", "SAMPLE", "VOLUME", "PITCH", "FINE TUNE", "CHORD", "STRETCH", "FILTER", "BIT CRUSH", "LO-FI RATE", "START", "END", "MODE", "REVERSE", "FADE IN",
                  "FADE OUT", "DELAY", "REVERB", "CHOKE", "CHOP", "FIT STYLE", "FIT TO TEMPO", "SPREAD", "KEY CHORDS"]
    # KEY CHORDS: the chords of a key, one per pad. Each entry is (name, semitones above the key note, chord name).
    KEYS = [("MAJOR KEY", [("I", 0, "MAJOR"), ("II m", 2, "MINOR"), ("III m", 4, "MINOR"), ("IV", 5, "MAJOR"),
                           ("V", 7, "MAJOR"), ("VI m", 9, "MINOR"), ("VII dim", 11, "DIM"), ("I high", 12, "MAJOR"),
                           ("I maj7", 0, "MAJOR 7"), ("II m7", 2, "MINOR 7"), ("III m7", 4, "MINOR 7"), ("IV maj7", 5, "MAJOR 7"),
                           ("V 7", 7, "7TH"), ("VI m7", 9, "MINOR 7"), ("V sus4", 7, "SUS 4"), ("I sus2", 0, "SUS 2")]),
            ("MINOR KEY", [("I m", 0, "MINOR"), ("II dim", 2, "DIM"), ("III", 3, "MAJOR"), ("IV m", 5, "MINOR"),
                           ("V m", 7, "MINOR"), ("VI", 8, "MAJOR"), ("VII", 10, "MAJOR"), ("I m high", 12, "MINOR"),
                           ("I m7", 0, "MINOR 7"), ("III maj7", 3, "MAJOR 7"), ("IV m7", 5, "MINOR 7"), ("V m7", 7, "MINOR 7"),
                           ("VI maj7", 8, "MAJOR 7"), ("VII 7", 10, "7TH"), ("V 7", 7, "7TH"), ("I sus4", 0, "SUS 4")])]
    CHOPS = [("AT HITS", 0), ("4 EQUAL", 4), ("8 EQUAL", 8), ("16 EQUAL", 16), ("LIVE: TAP AS IT PLAYS", -1)]

    def press_sound(self, b, sel):
        e = self.eng
        p = e.pads[self.sel]
        if sel and b in ("left", "right"):
            self.step_pad(1 if b == "right" else -1)
        elif b in ("l2", "r2"):
            self.step_pad(1 if b == "r2" else -1)
        elif b == "up":
            self.sound_row = (self.sound_row - 1) % len(self.SOUND_ROWS)
        elif b == "down":
            self.sound_row = (self.sound_row + 1) % len(self.SOUND_ROWS)
        elif b in ("left", "right"):
            d = (1 if b == "right" else -1) * (5 if "Y" in self.held else 1)
            row = self.SOUND_ROWS[self.sound_row]
            if row == "WAVEFORM":          # on the waveform, left and right nudge the start point
                step = self.trim_step(p)
                p.start = round(max(0.0, min(p.end - step, p.start + step * d)), 6)
                self.rebake(p)
            elif row == "VOLUME":
                p.vol = round(min(1.5, max(0.0, p.vol + 0.05 * d)), 2)
            elif row == "PITCH":
                p.pitch = int(min(24, max(-24, p.pitch + d)))
                self.rebake(p)
            elif row == "FILTER":
                p.filt = int(min(100, max(-100, p.filt + 2 * d)))
                self.rebake(p)
            elif row == "FINE TUNE":
                p.fine = int(min(50, max(-50, p.fine + d)))
                self.rebake(p)
            elif row == "CHORD":
                p.chord = (p.chord + (1 if d > 0 else -1)) % len(CHORDS)
                if p.chord and p.raw is not None and not p.chord_fits():
                    self.say("TOO LONG FOR A CHORD (%d s MAX) - TRIM OR CHOP IT" % CHORD_MAX_SECONDS, 3.5)
                self.rebake(p)
            elif row == "KEY CHORDS":
                self.key_idx = (self.key_idx + (1 if d > 0 else -1)) % len(self.KEYS)
            elif row == "MODE":
                p.mode = (p.mode + (1 if d > 0 else -1)) % len(MODES)
            elif row == "REVERSE":
                p.rev = not p.rev
                self.rebake(p)
            elif row in ("FADE IN", "FADE OUT"):
                k = "fin" if row == "FADE IN" else "fout"
                setattr(p, k, int(min(2000, max(0, getattr(p, k) + 10 * d))))
                self.rebake(p)
            elif row == "CHOP":
                self.chop_mode = (self.chop_mode + (1 if d > 0 else -1)) % len(self.CHOPS)
            elif row == "STRETCH" and p.region_seconds() > STRETCH_MAX_SECONDS:
                self.say("TOO LONG TO STRETCH (%d s MAX) - TRIM OR CHOP IT" % STRETCH_MAX_SECONDS, 3.5)
            elif row == "STRETCH":
                p.stretch = float(min(STRETCH_MAX, max(STRETCH_MIN, round(p.stretch) + d)))
                self.rebake(p)
            elif row == "BIT CRUSH":
                p.crush = int(min(len(CRUSH_BITS) - 1, max(0, p.crush + (1 if d > 0 else -1))))
                self.rebake(p)
            elif row == "LO-FI RATE":
                p.down = int(min(len(CRUSH_RATES) - 1, max(0, p.down + (1 if d > 0 else -1))))
                self.rebake(p)
            elif row == "FIT STYLE":
                self.fit_keep = not self.fit_keep
            elif row == "FIT TO TEMPO":
                self.fit_idx = (self.fit_idx + (1 if d > 0 else -1)) % len(FITS)
            elif row == "DELAY":
                p.dsend = round(min(1.0, max(0.0, p.dsend + 0.05 * d)), 2)
            elif row == "REVERB":
                p.rsend = round(min(1.0, max(0.0, p.rsend + 0.05 * d)), 2)
            elif row == "CHOKE":
                p.choke = (p.choke + (1 if d > 0 else -1)) % (CHOKE_GROUPS + 1)
            elif row == "START":
                step = self.trim_step(p)
                p.start = round(max(0.0, min(p.end - step, p.start + step * d)), 6)
                self.rebake(p)
            elif row == "END":
                step = self.trim_step(p)
                p.end = round(min(1.0, max(p.start + step, p.end + step * d)), 6)
                self.rebake(p)
            elif row == "SAMPLE":
                self.open_browser()
        elif b == "A":
            row = self.SOUND_ROWS[self.sound_row]
            if row == "SAMPLE":
                self.open_browser()
            elif row in ("CHOP", "FIT TO TEMPO", "SPREAD", "KEY CHORDS") and p.raw is None:
                self.say("THIS PAD HAS NO SAMPLE")
            elif row == "CHOP":
                self.start_chop()
            elif row == "FIT TO TEMPO":
                self.fit_to_tempo()
            elif row == "SPREAD" and p.region_seconds() > SPREAD_MAX_SECONDS:
                self.say("TOO LONG TO SPREAD (%d s MAX) - TRIM OR CHOP IT" % SPREAD_MAX_SECONDS, 3.5)
            elif row == "SPREAD":
                self.confirm = ("SPREAD PAD %d ACROSS PADS %d-%d?" % (self.sel + 1, self.base() + 1, self.base() + BANK), self.spread)
            elif row == "KEY CHORDS" and abs(p.stretch - 100) > 0.05:
                self.say("SET STRETCH TO OFF FIRST - 16 STRETCHED CHORDS TAKE TOO LONG", 4)
            elif row == "KEY CHORDS" and p.baked_seconds(self.key_root(p)) > KEY_CHORDS_MAX_SECONDS:
                self.say("TOO LONG FOR 16 CHORDS (%d s MAX) - TRIM OR CHOP IT" % KEY_CHORDS_MAX_SECONDS, 3.5)
            elif row == "KEY CHORDS":
                self.confirm = ("%s CHORDS ON PADS %d-%d?" % (self.KEYS[self.key_idx][0], self.base() + 1, self.base() + BANK),
                                self.key_chords)
            else:
                self.toggle_audition()
        elif b == "X":
            self.open_browser()
        elif b == "B":
            self.page = PAGES.index("PLAY")

    # ------------------------------------------------------------ sampler tools
    def start_chop(self):
        p = self.eng.pads[self.sel]
        label, count = self.CHOPS[self.chop_mode]
        if count < 0:
            self.eng.stop()
            self.eng.preview(self.sel, p.raw)
            self.live = {"pad": self.sel, "path": p.path, "n": len(p.raw), "peaks": p.peaks, "t0": time.time(), "marks": []}
            return
        a, z = p.start, p.end
        if count:
            edges = [a + (z - a) * i / count for i in range(count + 1)]
            slices = list(zip(edges, edges[1:]))
        else:
            n = len(p.raw)
            seg = p.raw[int(a * n):max(int(a * n) + 16, int(z * n))]
            slices = [(a + (z - a) * s, a + (z - a) * t) for s, t in find_chops(seg, BANK)]
        if len(slices) < 2:
            self.say("ONLY ONE HIT FOUND - TRY EQUAL OR LIVE", 3)
            return
        path = p.path
        self.confirm = ("CHOP INTO %d? REPLACES PADS %d-%d" % (len(slices), self.base() + 1, self.base() + len(slices)),
                        lambda: self.put_slices(path, slices) and self.say("%d CHOPS ON THE PADS" % len(slices), 2.5))

    def live_pos(self):
        lv = self.live
        return (time.time() - lv["t0"] - 0.06) * SR / lv["n"]      # 0.06 s: output delay

    def live_mark(self):
        lv = self.live
        pos = min(0.999, max(0.0, self.live_pos()))
        if len(lv["marks"]) < BANK and (not lv["marks"] or pos - lv["marks"][-1] > 0.03 * SR / lv["n"]):
            lv["marks"].append(pos)

    def live_finish(self, cancel=False):
        lv, self.live = self.live, None
        self.eng.note_off(lv["pad"])
        marks = lv["marks"]
        if cancel or not marks:
            self.say("CHOP CANCELLED" if cancel else "NO SLICE POINTS WERE TAPPED", 2)
            return
        if self.put_slices(lv["path"], list(zip(marks, marks[1:] + [1.0]))):
            self.say("%d CHOPS ON THE PADS" % len(marks), 2.5)

    def tick_live(self):
        if self.live and self.live_pos() >= 1.0:
            self.live_finish()

    def fit_to_tempo(self):
        """Make the pad last an exact number of beats at the project tempo, by stretching or repitching."""
        p = self.eng.pads[self.sel]
        label, beats = FITS[self.fit_idx]
        want = beats * 60.0 / self.eng.bpm
        if self.pending_bake and self.pending_bake[0] is not p:
            self.flush_bake()
        self.pending_bake = None
        if self.fit_keep and p.region_seconds() > STRETCH_MAX_SECONDS:
            self.say("TOO LONG TO STRETCH (%d s MAX) - TRIM OR CHOP IT" % STRETCH_MAX_SECONDS, 3.5)
            return
        if self.fit_keep:               # change the length only, at whatever pitch the pad is set to
            pct = 100.0 * want * 2.0 ** ((p.pitch + p.fine / 100.0) / 12.0) / p.region_seconds()
            if not STRETCH_MIN <= pct <= STRETCH_MAX:
                self.say("TOO FAR: THAT NEEDS A %d%% STRETCH (%d-%d)" % (round(pct), STRETCH_MIN, STRETCH_MAX), 3.5)
                return
            p.stretch = pct
            p.bake()
            self.say("FITTED TO %s: STRETCH %.1f%%, PITCH KEPT" % (label, pct), 3)
            return
        semis = 12.0 * math.log2(p.region_seconds() * p.stretch / 100.0 / want)
        if abs(semis) > 24:
            self.say("TOO FAR: THAT NEEDS %+d SEMITONES" % round(semis), 3)
            return
        p.pitch = int(round(semis))
        p.fine = int(round((semis - p.pitch) * 100))
        p.bake()
        self.say("FITTED TO %s: PITCH %+d, FINE %+d" % (label, p.pitch, p.fine), 3)

    def spread(self):
        """Chromatic mode: this pad's sound on every pad of its bank, one semitone apart, this pad as the root."""
        if self.eng.pads[self.sel].raw is None:
            self.say("THIS PAD HAS NO SAMPLE")
            return
        src = self.eng.pads[self.sel]
        base = src.name.split(" ")[0][:8]
        root = self.sel - self.base()
        for i, q in enumerate(self.bank_pads()):
            if q is src:
                continue
            q.clear()
            q.raw, q.peaks, q.path = src.raw, src.peaks, src.path
            q.copy_settings(src)
            q.pitch = int(min(24, max(-24, src.pitch + i - root)))
            q.name = "%s %+d" % (base, i - root)
            q.bake()
        prune_cache(self.eng.pads)
        self.page = PAGES.index("PLAY")
        self.say("PAD %d SPREAD ACROSS PADS %d-%d" % (self.sel + 1, self.base() + 1, self.base() + BANK), 2.5)

    @staticmethod
    def key_root(pad):
        """The key note for KEY CHORDS: the pad's pitch, an octave or two down if the top chord would be out of range."""
        root = pad.pitch
        while root + 12 > 24:
            root -= 12
        return root

    def key_chords(self):
        """This pad's sound as the sixteen chords of a key, one per pad of its bank. The pad's own pitch is the key note."""
        src = self.eng.pads[self.sel]
        if src.raw is None:
            self.say("THIS PAD HAS NO SAMPLE")
            return
        self.flush_bake()
        key, chords = self.KEYS[self.key_idx]
        names = [c[0] for c in CHORDS]
        raw, peaks, path, root = src.raw, src.peaks, src.path, self.key_root(src)
        self.say("MAKING 16 CHORDS ...", 2)
        self.draw()                         # sixteen bakes take a moment on the handheld: say so first
        keep = type(src)(0)                 # the source pad is one of the sixteen, so copy its settings first
        keep.copy_settings(src)
        for q, (label, semis, chord) in zip(self.bank_pads(), chords):
            self.eng.note_off(q.index)
            q.clear()
            q.raw, q.peaks, q.path = raw, peaks, path
            q.copy_settings(keep)
            q.pitch, q.chord, q.name = int(root + semis), names.index(chord), label
            q.bake()
            self.breathe()
        prune_cache(self.eng.pads)
        self.sel, self.page = self.base(), PAGES.index("PLAY")
        self.say("CHORDS OF A %s ON PADS %d-%d" % (key, self.base() + 1, self.base() + BANK), 3)

    def export_audio(self):
        """Render the song (or four loops of the current pattern) to samples/exports/ as a WAV file."""
        e = self.eng
        e.stop()
        self.flush_bake()
        self.save_project(quiet=True, autosave=True)       # a long export is the riskiest moment to lose work

        def progress(frac):
            pygame.event.pump()
            self.box(170, 150, 300, 60, PANEL, ORANGE, 6, 2)
            self.text("EXPORTING  %d%%" % round(frac * 100), 320, 170, "m", TEXT, "c")
            self.box(190, 188, 260, 8, DARK, None, 3)
            self.box(190, 188, 260 * frac, 8, TEAL, None, 3)
            self.present()
        try:
            progress(0.0)
            audio = e.render_offline(progress=progress)
            folder = os.path.join(SAMPLES, "exports")
            os.makedirs(folder, exist_ok=True)
            kind = "SONG" if (e.song_on and e.song) else "PATTERN %s" % PAT_NAMES[e.cur_pat]
            path = self.unused_path(folder, "P%02d %s %s" % (self.loaded, kind, time.strftime("%Y-%m-%d %H-%M")))
            write_wav(path, audio)
        except (OSError, ValueError, MemoryError) as err:
            self.say("EXPORT FAILED: %s" % err, 4)
            return
        finally:
            # Presses made while it was busy must not run afterwards; releases still count.
            for ev in pygame.event.get():
                if ev.type in (pygame.KEYUP, pygame.JOYBUTTONUP, pygame.CONTROLLERBUTTONUP, pygame.QUIT):
                    self.handle_event(ev)
        self.logline("export: %s, %.1f s" % (path, len(audio) / SR))
        self.say("SAVED %d:%02d TO samples/exports/" % (len(audio) / SR // 60, len(audio) / SR % 60), 4)

    def start_resample(self):
        self.eng.capture_start()
        if not self.eng.playing:
            self.eng.play()
        self.resampling = (time.time(), self.sel)      # remember the target: the selection may move while playing
        self.page = PAGES.index("FX")
        self.say("RESAMPLING: PLAY, THEN PRESS START TO FINISH", 4)

    def finish_resample(self):
        target, self.resampling = self.resampling[1], None
        self.eng.stop()
        audio = self.eng.capture_stop()
        loud = np.nonzero(np.abs(audio).max(axis=1) > 0.003)[0] if len(audio) else []
        if len(loud) < 2000:
            self.say("NOTHING WAS PLAYING", 3)
            return
        audio = audio[max(0, loud[0] - 64):loud[-1] + 2205]
        folder = os.path.join(APP_DIR, "samples", "resampled")
        os.makedirs(folder, exist_ok=True)
        path = self.unused_path(folder, time.strftime("RS %Y-%m-%d %H-%M-%S"))
        try:
            write_wav(path, audio / max(1.0, float(np.abs(audio).max())))
            p = self.eng.pads[target]
            p.clear()
            p.load(path)
            p.name = "RESAMPLE"
            self.sel = target
        except (OSError, ValueError) as e:
            self.say("RESAMPLE FAILED: %s" % e, 4)
            return
        prune_cache(self.eng.pads)
        self.page = PAGES.index("SOUND")
        self.say("%.1f s RESAMPLED TO PAD %d" % (len(audio) / SR, self.sel + 1), 3)

    @staticmethod
    def trim_step(p):
        """One nudge of a start or end point: 10 ms, or 0.2% of the file for very short samples."""
        n = len(p.raw) if p.raw is not None else SR
        return min(0.002, 0.01 * SR / n)

    def rebake(self, p):
        """Re-render a pad after a setting change. Long samples wait until the value stops moving,
        because rendering them takes long enough to make the controls stutter."""
        if self.pending_bake and self.pending_bake[0] is not p:
            self.flush_bake()               # another pad is still waiting: do it now, or it would be forgotten
        slow = abs(p.stretch - 100) > 0.05 or p.notes_wanted or p.chord    # stretching, chords and step-note copies take time
        if p.region_seconds() > LONG_SAMPLE_SECONDS or slow:
            self.pending_bake = (p, time.time() + 0.2)
        else:
            p.bake()

    def flush_bake(self, force=True):
        if self.pending_bake and (force or time.time() >= self.pending_bake[1]):
            p, self.pending_bake = self.pending_bake[0], None
            p.bake()

    def toggle_audition(self):
        """Play the selected pad, or stop it if it is already sounding."""
        self.flush_bake()
        if self.auditioning() is not None:
            self.eng.note_off(self.sel)
            self.audition = None
        elif self.eng.pads[self.sel].buf is not None:
            self.eng.trigger(self.sel)
            self.audition = (self.sel, time.time())

    def auditioning(self):
        """How far through the selected pad's sound the SOUND-page preview is (0..1), or None if stopped."""
        if not self.audition or self.audition[0] != self.sel:
            return None
        p = self.eng.pads[self.sel]
        secs = p.seconds()
        if secs <= 0:
            return None
        t = (time.time() - self.audition[1] - 0.06) / secs
        if t >= 1.0:
            if p.mode != 2:
                self.audition = None
                return None
            t %= 1.0
        return max(0.0, t)

    FX_ROWS = ["COMPRESSOR", "DELAY TIME", "DELAY FEEDBACK", "REVERB SIZE", "STUTTER LENGTH", "REPEAT RATE",
               "L STICK SIDEWAYS", "L STICK UP DOWN", "R STICK SIDEWAYS", "R STICK UP DOWN"]
    STICK_ROWS = {"L STICK SIDEWAYS": "lx", "L STICK UP DOWN": "ly", "R STICK SIDEWAYS": "rx", "R STICK UP DOWN": "ry"}
    # What a stick direction can do. Two labels: one effect each way (left or down first).
    # One label: the same effect whichever way the stick is pushed.
    STICK_FX = {"OFF": (), "FILTER": ("LOW", "HIGH"), "TEMPO": ("SLOW", "FAST"), "PAN": ("LEFT", "RIGHT"),
                "FADE OUT": ("FADE",), "ECHO": ("ECHO",), "REVERB": ("REVERB",), "STUTTER": ("STUTTER",),
                "CRUSH": ("CRUSH",), "TAPE STOP": ("TAPE",)}
    STICK_DEFAULT = {"lx": "FILTER", "ly": "FADE OUT", "rx": "TEMPO", "ry": "ECHO"}
    PERFORM = {"A": ("stutter", "STUTTER"), "B": ("tape", "TAPE STOP"), "X": ("lp", "LOW-PASS"), "Y": ("hp", "HIGH-PASS")}

    def press_fx(self, b, sel):
        e = self.eng
        if b in self.PERFORM:
            fx = self.PERFORM[b][0]
            e.perf[fx] = True
            self.perf_held[b] = fx
        elif b == "up":
            self.fx_row = (self.fx_row - 1) % len(self.FX_ROWS)
        elif b == "down":
            self.fx_row = (self.fx_row + 1) % len(self.FX_ROWS)
        elif b in ("left", "right"):
            d = 1 if b == "right" else -1
            row = self.FX_ROWS[self.fx_row]
            if row == "COMPRESSOR":
                e.comp = round(min(1.0, max(0.0, e.comp + 0.05 * d)), 2)
            elif row == "DELAY TIME":
                e.delay_div = (e.delay_div + d) % len(DELAY_TIMES)
            elif row == "DELAY FEEDBACK":
                e.delay_fb = round(min(0.9, max(0.0, e.delay_fb + 0.05 * d)), 2)
            elif row == "REVERB SIZE":
                e.reverb_size = round(min(1.0, max(0.0, e.reverb_size + 0.05 * d)), 2)
            elif row == "STUTTER LENGTH":
                e.stutter_div = (e.stutter_div + d) % len(STUTTERS)
            elif row == "REPEAT RATE":
                e.repeat_div = (e.repeat_div + d) % len(RATES)
            elif row in self.STICK_ROWS:
                names = list(self.STICK_FX)
                axis = self.STICK_ROWS[row]
                self.stick_fx[axis] = names[(names.index(self.stick_fx[axis]) + d) % len(names)]

    PROJ_ROWS = (["RECORD MIC", "RESAMPLE", "EXPORT AUDIO", "SAVE", "SAVE A COPY", "NAME", "OPEN", "NEW", "TEMPO", "SWING", "MASTER", "MIDI OUT", "MIDI DEVICES", "BLUETOOTH MIDI", "MIDI KNOBS"]
                 + (["IMPORT CHOPS", "GRAINBRICK"] if GB_DIR else []) + ["QUIT"])

    def press_proj(self, b, sel):
        e = self.eng
        row = self.PROJ_ROWS[self.proj_row]
        if b == "up":
            self.proj_row = (self.proj_row - 1) % len(self.PROJ_ROWS)
        elif b == "down":
            self.proj_row = (self.proj_row + 1) % len(self.PROJ_ROWS)
        elif b in ("left", "right"):
            d = 1 if b == "right" else -1
            big = 5 if "Y" in self.held else 1
            if row == "TEMPO":
                e.bpm = float(min(300, max(40, round(e.bpm) + d * big)))
            elif row == "SWING":
                e.swing = float(min(75, max(50, round(e.swing) + d * big)))
            elif row == "MIDI OUT":
                self.midi.out_channel = (self.midi.out_channel + d) % 17
            elif row == "MASTER":
                e.master = round(min(1.5, max(0.0, e.master + 0.05 * d)), 2)
        elif b == "A":
            if row == "SAVE":                   # always the open project, into its own slot
                self.save_project(autosave=True)
            elif row == "SAVE A COPY":
                self.save_copy()
            elif row == "OPEN":
                self.ask_open()
            elif row == "NAME":
                self.start_naming()
            elif row == "NEW":
                self.ask_new_project()
            elif row == "MIDI KNOBS":
                self.knobs = {"row": 0}
            elif row == "BLUETOOTH MIDI":
                if self.ble.running or self.ble.start():
                    self.ble.search()
                    self.say("SEARCHING: SWITCH THE CONTROLLER'S BLUETOOTH ON", 3)
                else:
                    self.say("BLUETOOTH IS OFF IN THE SYSTEM MENU", 3)
            elif row == "RECORD MIC":
                self.confirm = ("RECORD %d SECONDS? REPLACES PADS %d-%d" % (REC_SECONDS, self.base() + 1, self.base() + BANK),
                                self.start_record)
            elif row == "EXPORT AUDIO":
                what = "THE SONG" if (e.song_on and e.song) else "PATTERN %s x 4" % PAT_NAMES[e.cur_pat]
                if e.offline_seconds() > EXPORT_MAX_SECONDS:
                    self.confirm = ("%s IS %d MIN LONG. EXPORT THE FIRST %d MIN?" % (
                        what, round(e.offline_seconds() / 60), EXPORT_MAX_SECONDS // 60), self.export_audio)
                else:
                    self.confirm = ("EXPORT %s AS A WAV FILE?" % what, self.export_audio)
            elif row == "RESAMPLE":
                self.confirm = ("RESAMPLE THE OUTPUT TO PAD %d?" % (self.sel + 1), self.start_resample)
            elif row == "IMPORT CHOPS":
                items = self.gb_projects()
                if items:
                    self.picker = {"title": "IMPORT CHOPS FROM A GRAINBRICK PROJECT", "items": items, "idx": 0,
                                   "preview": self.preview_gb,
                                   "fn": self.import_chops}
                else:
                    self.say("NO GRAINBRICK PROJECTS WITH SLICES FOUND", 3)
            elif row == "GRAINBRICK":
                def go():
                    self.exit_code = OPEN_GRAINBRICK
                    self.running = False
                self.confirm = ("OPEN GRAINBRICK? QUIT IT TO COME BACK HERE", go)
            elif row == "QUIT":
                self.running = False

    # ------------------------------------------------------------ GrainBrick link
    def gb_projects(self):
        """GrainBrick projects that have a sample and slice points, newest first."""
        out = []
        files = glob.glob(os.path.join(GB_DIR, "projects", "*.json"))
        for f in sorted(files, key=os.path.getmtime, reverse=True):
            try:
                with open(f) as fh:
                    d = json.load(fh)
                cuts = (d.get("rhythm") or {}).get("boundaries") or []
                if (d.get("sample") or {}).get("path") and len(cuts) >= 3:
                    name = d.get("name") or os.path.splitext(os.path.basename(f))[0]
                    out.append(("%s   (%d slices)" % (name, len(cuts) - 1), f))
            except (OSError, ValueError, AttributeError, TypeError):
                continue
        return out

    def preview_gb(self, project_file):
        """Play the sample a GrainBrick project uses, so it can be heard before importing."""
        try:
            with open(project_file) as fh:
                sp = json.load(fh)["sample"]["path"]
            raw = load_cached(sp if os.path.isabs(sp) else os.path.join(GB_DIR, sp))[0]
        except (OSError, ValueError, KeyError, TypeError) as e:
            self.say("CAN'T PLAY IT: %s" % e, 3)
            return False
        self.eng.preview(self.sel, raw)
        self.preview_end = time.time() + len(raw) / SR
        return True

    def import_chops(self, project_file):
        """Put a GrainBrick project's slices on the pads of the bank on screen, one slice per pad."""
        try:
            with open(project_file) as fh:
                d = json.load(fh)
            sp = d["sample"]["path"]
            path = sp if os.path.isabs(sp) else os.path.join(GB_DIR, sp)
            cuts = sorted(min(1.0, max(0.0, float(x))) for x in d["rhythm"]["boundaries"])
            slices = [(a, z) for a, z in zip(cuts, cuts[1:]) if z - a > 0.0005]
            load_cached(path)
        except (OSError, ValueError, KeyError, TypeError) as e:
            self.say("IMPORT FAILED: %s" % e, 4)
            return
        total = len(slices)
        if not self.put_slices(path, slices[:BANK]):
            return
        extra = "  (FIRST 16 OF %d)" % total if total > BANK else ""
        self.say("IMPORTED %d CHOPS%s" % (min(total, BANK), extra), 3)

    def put_slices(self, path, slices):
        """One slice per pad, from the first pad of the bank on screen. Returns False (and changes nothing) if the file is gone."""
        try:
            load_cached(path)
        except (OSError, ValueError) as e:
            self.say("CAN'T READ THE SAMPLE: %s" % e, 4)
            return False
        self.eng.stop()
        self.pending_bake = None
        for i, (p, (a, z)) in enumerate(zip(self.bank_pads(), slices)):
            p.clear()
            p.load(path)
            p.start, p.end = a, z
            p.bake()
            p.name = "CHOP %d" % (i + 1)
        prune_cache(self.eng.pads)
        self.sel, self.page = self.base(), PAGES.index("PLAY")
        return True

    @staticmethod
    def unused_path(folder, stem):
        """A WAV path that does not exist yet (the device clock can reset, so timestamps may repeat)."""
        path, n = os.path.join(folder, stem + ".wav"), 1
        while os.path.exists(path):
            n += 1
            path = os.path.join(folder, "%s (%d).wav" % (stem, n))
        return path

    # ------------------------------------------------------------ record and chop
    def start_record(self):
        """Record from the built-in microphone; the take is chopped onto the pads when it ends."""
        self.eng.stop()
        folder = os.path.join(APP_DIR, "samples", "recorded")
        os.makedirs(folder, exist_ok=True)
        path = self.unused_path(folder, time.strftime("REC %Y-%m-%d %H-%M-%S"))
        fake = os.environ.get("PP_REC_FAKE")
        if fake:
            cmd = ["cp", fake, path]
        else:
            # This sound chip needs the first mic boost on and these gains, or capture fails.
            for ctl, val in (("ADCL Input MIC1 Boost", "on"), ("MIC1 gain volume", "31"), ("ADC volume", "160,160")):
                try:
                    subprocess.run(["amixer", "-c", "0", "sset", ctl, val], capture_output=True, timeout=3)
                except (OSError, subprocess.SubprocessError):
                    pass
            cmd = ["arecord", "-q", "-D", REC_DEVICE, "-f", "S16_LE", "-r", "48000", "-c", "1",
                   "-d", str(REC_SECONDS), path]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError as e:
            self.say("CANNOT RECORD: %s" % e, 4)
            return
        self.recording = {"proc": proc, "path": path, "t0": time.time(), "level": 0.0}
        self.logline("record start: %s" % " ".join(cmd))

    def tick_record(self):
        r = self.recording
        if not r:
            return
        try:                                    # live level from the tail of the growing file
            with open(r["path"], "rb") as f:
                f.seek(max(44, os.path.getsize(r["path"]) - 4800) & ~1)
                tail = f.read()
            vals = struct.unpack("<%dh" % (len(tail) // 2), tail[:len(tail) // 2 * 2])
            if vals:
                r["level"] = max(r["level"] * 0.7, max(map(abs, vals)) / 32768.0)
        except (OSError, struct.error):
            pass
        if r["proc"].poll() is None:
            if time.time() - r["t0"] > REC_SECONDS + 3:
                r["proc"].kill()
            return
        self.recording = None
        err = (r["proc"].stderr.read() or b"").decode("utf-8", "replace").strip()
        self.finish_record(r["path"], err)

    def stop_record(self):
        if self.recording and self.recording["proc"].poll() is None:
            self.recording["proc"].send_signal(signal.SIGINT)   # arecord closes the file properly

    def finish_record(self, path, err):
        try:
            raw = load_wav(path)
            mono = raw.mean(axis=1).astype(np.float64)
            mono -= mono.mean()
            peak = float(np.abs(mono).max()) if len(mono) else 0.0
            if len(mono) < 4410 or peak < 0.002:
                raise ValueError("no sound was captured")
            write_wav(path, mono / peak * 0.9)
            raw, _ = load_cached(path)
            slices = find_chops(raw, BANK)
        except (OSError, ValueError) as e:
            self.logline("record failed: %s | %s" % (e, err))
            busy = "busy" in err.lower()
            self.say("MIC IS BUSY - CLOSE OTHER AUDIO APPS OR RESTART" if busy else ("RECORDING FAILED: %s" % (err or e))[:60], 5)
            try:
                os.remove(path)
            except OSError:
                pass
            return
        if not self.put_slices(path, slices):
            return
        for p in self.bank_pads()[len(slices):]:
            p.clear()
        self.logline("record ok: %.1f s peak %.3f, %d chops" % (len(mono) / 44100.0, peak, len(slices)))
        self.say("RECORDED %d CHOPS ONTO THE PADS" % len(slices), 3)

    def press_picker(self, b):
        pk = self.picker
        if b in ("A", "B", "up", "down") and self.previewing:
            self.eng.note_off(self.sel)
            self.previewing = None
        if b == "X" and pk.get("preview"):
            item = pk["items"][pk["idx"]][1]
            if self.previewing == item:
                self.eng.note_off(self.sel)
                self.previewing = None
            else:
                self.eng.note_off(self.sel)
                self.previewing = item if pk["preview"](item) else None
        elif b == "B":
            self.picker = None
            prune_cache(self.eng.pads)
        elif b == "up":
            pk["idx"] = (pk["idx"] - 1) % len(pk["items"])
        elif b == "down":
            pk["idx"] = (pk["idx"] + 1) % len(pk["items"])
        elif b == "A":
            self.picker = None
            pk["fn"](pk["items"][pk["idx"]][1])

    # ------------------------------------------------------------ sample browser
    def roots(self):
        r = [("MY SAMPLES", SAMPLES)]
        if GB_DIR:
            for label, sub in (("GRAINBRICK RECORDINGS", "recordings"), ("GRAINBRICK SAMPLES", "samples")):
                d = os.path.join(GB_DIR, sub)
                if os.path.isdir(d):
                    r.append((label, d))
        return r

    def root_of(self, path):
        ap = os.path.abspath(path)
        for label, d in self.roots():
            rd = os.path.abspath(d)
            if ap == rd or ap.startswith(rd + os.sep):
                return label, rd
        return None

    def open_browser(self):
        p = self.eng.pads[self.sel]
        d = os.path.dirname(p.path) if p.path else SAMPLES
        if not os.path.isdir(d) or not self.root_of(d):
            d = SAMPLES
        self.browser = {"dir": d, "items": [], "idx": 0, "moved": time.time()}
        self.browser_list(os.path.basename(p.path) if p.path else None)

    # "In the beat": while the picker is open the highlighted file takes the pad's place, with the pad's own
    # settings, so the running pattern plays it next to the rest of the kit. Nothing is kept unless A is pressed.
    def trial_end(self, keep=False):
        """Stop trying a file: put the pad back as it was, or with keep=True leave the new sound on it."""
        t, self.trial = self.trial, None
        if t and not keep:
            self.eng.note_off(t[0])
            self.eng.pads[t[0]].__dict__.update(t[1])

    def tick_trial(self):
        br = self.browser
        if not br or self.confirm:
            return
        kind, name = br["items"][br["idx"]] if br["items"] else ("", "")
        path = os.path.join(br["dir"], name) if kind == "wav" and br["dir"] else None
        want = path if self.in_beat else None
        if (self.trial[2] if self.trial else None) == want or time.time() - br.get("moved", 0) < 0.25:
            return                          # nothing to change, or the cursor is still moving
        self.trial_end()
        if not want:
            return
        pad = self.eng.pads[self.sel]
        before = dict(pad.__dict__)
        try:
            src = self.convert(want) if want.lower().endswith(CONVERT_EXTS) else want
            self.pending_bake = None
            self.eng.note_off(self.sel)
            pad.start, pad.end = 0.0, 1.0
            pad.load(src)
            pad.name = self.sound_name(want)
            self.trial = (self.sel, before, want)
        except (OSError, ValueError) as e:
            pad.__dict__.update(before)
            self.trial = (self.sel, before, want)       # remembered, so a bad file is not tried again every frame
            self.trial_end()
            br["moved"] = time.time() + 3600            # until the cursor moves again
            self.say("CAN'T LOAD: %s" % e, 3)

    def browser_list(self, focus=None):
        br = self.browser
        if br["dir"] is None:
            items = [("root", label) for label, _ in self.roots()]
        else:
            try:
                names = sorted(os.listdir(br["dir"]), key=str.lower)
            except OSError:
                names = []
            dirs = [n for n in names if not n.startswith(".") and os.path.isdir(os.path.join(br["dir"], n))]
            wavs = [n for n in names if not n.startswith(".") and n.lower().endswith((".wav",) + CONVERT_EXTS)]
            items = [("dir", n) for n in dirs] + [("wav", n) for n in wavs]
            at_root = self.root_of(br["dir"])[1] == os.path.abspath(br["dir"])
            if not at_root or len(self.roots()) > 1:
                items.insert(0, ("up", ".."))
        br["items"], br["idx"] = items, 0
        for i, (_, n) in enumerate(items):
            if n == focus:
                br["idx"] = i

    def press_browser(self, b):
        br = self.browser
        items = br["items"]
        br["moved"] = time.time()
        if b == "B":
            self.eng.note_off(self.sel)
            self.trial_end()
            self.browser = None
            prune_cache(self.eng.pads)
            return
        if b == "start":                    # the beat can be started and stopped without leaving the picker
            self.eng.toggle_play()
            return
        if b == "r1":
            self.in_beat = not self.in_beat
            self.say("IN THE BEAT: ON - THE PATTERN PLAYS THE FILE YOU ARE ON" if self.in_beat else "IN THE BEAT: OFF", 3)
            if self.in_beat and not self.eng.playing:
                self.eng.play()
            return
        if br["dir"] is not None and not self.root_of(br["dir"]):
            br["dir"] = None                # the folder went away (card removed, GrainBrick moved)
            self.browser_list()
            return
        if not items:
            return
        kind, name = items[br["idx"]]
        if b == "up":
            br["idx"] = (br["idx"] - 1) % len(items)
        elif b == "down":
            br["idx"] = (br["idx"] + 1) % len(items)
        elif b in ("l2", "r2"):
            br["idx"] = min(len(items) - 1, max(0, br["idx"] + (-8 if b == "l2" else 8)))
        elif b == "A" and kind == "root":
            br["dir"] = dict(self.roots())[name]
            self.browser_list()
        elif b == "A" and kind == "up":
            label, rd = self.root_of(br["dir"])
            if os.path.abspath(br["dir"]) == rd:
                br["dir"] = None
                self.browser_list(label)
            else:
                prev = os.path.basename(br["dir"])
                br["dir"] = os.path.dirname(br["dir"])
                self.browser_list(prev)
        elif b == "A" and kind == "dir":
            br["dir"] = os.path.join(br["dir"], name)
            self.browser_list()
        elif b == "Y" and kind in ("dir", "wav"):
            # A whole folder at once: the highlighted folder, or the one these files are in.
            folder = os.path.join(br["dir"], name) if kind == "dir" else br["dir"]
            count = len(self.folder_sounds(folder))
            if not count:
                self.say("NO SOUNDS IN THAT FOLDER")
            else:
                first = self.base() + 1
                self.confirm = ("LOAD %d SOUNDS ONTO PADS %d-%d?" % (count, first, first + count - 1),
                                lambda: self.load_folder(folder))
        elif b in ("A", "X") and kind == "wav":
            pad = self.eng.pads[self.sel]
            try:
                path = os.path.join(br["dir"], name)
                if name.lower().endswith(CONVERT_EXTS):
                    path = self.convert(path)
                if b == "X":                # preview: hear the file without touching the pad
                    self.eng.note_off(self.sel)
                    self.eng.preview(self.sel, load_cached(path)[0])
                    return
                self.eng.note_off(self.sel)
                self.trial_end(keep=True)
                self.pending_bake = None
                pad.load(path)
                pad.name = self.sound_name(name)
                pad.start, pad.end = 0.0, 1.0
                pad.bake()
                if not (self.in_beat and self.eng.playing):     # in the beat it is already being heard
                    self.eng.trigger(self.sel)
                self.browser = None
                prune_cache(self.eng.pads)
                self.say("PAD %d: %s" % (self.sel + 1, pad.name))
            except (OSError, ValueError) as e:
                self.say("CAN'T LOAD: %s" % e, 3)

    @staticmethod
    def sound_name(path):
        """A pad's name for a file: the file's own name, not that of the converted copy it may be played from."""
        name = os.path.splitext(os.path.basename(path))[0].upper().replace("_", " ")
        return (re.sub(r"^\d{1,2}[ -]+", "", name) or name)[:24]

    @staticmethod
    def folder_sounds(folder):
        """The first sixteen sound files in a folder, in name order."""
        try:
            names = sorted(os.listdir(folder), key=str.lower)
        except OSError:
            return []
        return [os.path.join(folder, n) for n in names
                if not n.startswith(".") and n.lower().endswith((".wav",) + CONVERT_EXTS)][:BANK]

    def load_folder(self, folder):
        """Put a folder's sounds on the pads of the bank on screen, one each, from the first pad."""
        self.trial_end()
        self.pending_bake = None
        first, done, failed = self.base(), 0, 0
        for i, src in enumerate(self.folder_sounds(folder)):
            pad = self.eng.pads[first + i]
            try:
                path = self.convert(src) if src.lower().endswith(CONVERT_EXTS) else src
                self.eng.note_off(first + i)
                pad.load(path)
                pad.name = self.sound_name(src)
                pad.start, pad.end = 0.0, 1.0
                pad.bake()
                done += 1
            except (OSError, ValueError):
                failed += 1
        self.browser = None
        prune_cache(self.eng.pads)
        self.eng.refresh_variants()
        self.say("%d SOUNDS FROM %s ON PADS %d-%d%s" % (done, os.path.basename(folder).upper()[:16], first + 1, first + max(1, done),
                                                       ", %d WOULD NOT LOAD" % failed if failed else ""), 4)

    def convert(self, src):
        """Turn an MP3 (or other compressed file) into a WAV in samples/converted, once, and return its path."""
        folder = os.path.join(SAMPLES, "converted")
        os.makedirs(folder, exist_ok=True)
        # The source folder goes into the name so two files called "loop" in different places stay separate.
        tag = hashlib.md5(os.path.abspath(src).encode("utf-8", "replace")).hexdigest()[:6]
        out = os.path.join(folder, "%s-%s.wav" % (os.path.splitext(os.path.basename(src))[0], tag))
        if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(src) and os.path.getsize(out) > 1000:
            return out
        self.say("CONVERTING %s ..." % os.path.basename(src)[:36], 60)
        self.draw()
        tmp = out + ".part"
        try:
            r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file", "-i", src, "-t", str(CONVERT_SECONDS), "-vn", "-ac", "2",
                                "-ar", str(SR), "-c:a", "pcm_s16le", "-f", "wav", tmp],
                               capture_output=True, timeout=120)
        except FileNotFoundError:
            raise ValueError("ffmpeg is not installed")
        except subprocess.TimeoutExpired:
            raise ValueError("converting took too long")
        finally:
            self.toast_until = 0
        if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 1000:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise ValueError((r.stderr.decode("utf-8", "replace").strip().splitlines() or ["not an audio file"])[-1][-40:])
        os.replace(tmp, out)
        return out

    # ------------------------------------------------------------ drawing
    def S(self, v):
        return int(round(v * self.u))

    def R(self, x, y, w, h):
        return pygame.Rect(int(self.ox + x * self.u), int(self.oy + y * self.u), self.S(w), self.S(h))

    def present(self, rects=None):
        """Copy the picture, or just the given parts of it, to the display."""
        if rects:
            for r in rects:
                self.display.blit(self.screen, r, r)
        else:
            self.display.blit(self.screen, (0, 0))
        pygame.display.flip()
        self.frame = []                 # whatever was drawn, the next frame starts from scratch

    def breathe(self):
        """Stand aside while the audio thread renders a block (see Engine.gate). Called between drawing steps."""
        gate = self.eng.gate
        if gate.locked():
            gate.acquire()
            gate.release()

    def area(self, x, y, w, h):
        """Start a new area of the frame. An area is redrawn only when its drawing steps change."""
        self.breathe()
        self.ops = []
        self.areas.append(((x, y, w, h), self.ops))

    def box(self, x, y, w, h, fill=None, border=None, radius=3, bw=1):
        if self.ops is not None:        # describing a frame: note the step, draw later if needed
            self.ops.append((self._box, x, y, w, h, fill, border, radius, bw))
        else:
            self._box(x, y, w, h, fill, border, radius, bw)

    def _box(self, x, y, w, h, fill, border, radius, bw):
        key = (x, y, w, h, radius, bw)
        g = self.geom.get(key)
        if g is None:
            if len(self.geom) > 4000:
                self.geom.clear()
            g = self.geom[key] = (self.R(x, y, w, h), self.S(radius), max(1, self.S(bw)))
        if fill is not None:
            pygame.draw.rect(self.screen, fill, g[0], 0, g[1])
        if border is not None:
            pygame.draw.rect(self.screen, border, g[0], g[2], g[1])

    def text(self, s, x, y, font="m", color=TEXT, align="l"):
        if self.ops is not None:
            self.ops.append((self._text, s, x, y, font, color, align))
        else:
            self._text(s, x, y, font, color, align)

    def _text(self, s, x, y, font, color, align):
        key = (s, font, color)
        surf = self.cache.get(key)
        if surf is None:
            if len(self.cache) > 600:
                self.cache.clear()
            surf = self.cache[key] = self.fonts[font].render(s, True, color)
        px, py = self.ox + x * self.u, self.oy + y * self.u
        w, h = surf.get_size()
        if align == "c":
            px -= w / 2
        elif align == "r":
            px -= w
        self.screen.blit(surf, (int(px), int(py - h / 2)))

    def custom(self, fn, *key):
        """A drawing step done by a function. `key` must change whenever what it draws changes."""
        if self.ops is not None:
            self.ops.append((fn,) + key)
        else:
            fn(*key)

    def wave(self, pad, x, y, w, h, color, trim=False):
        self.custom(self._wave, pad, x, y, w, h, color, trim, id(pad.raw), pad.start, pad.end)

    def _wave(self, pad, x, y, w, h, color, trim, *_):
        if pad.peaks is None:
            self.box(x, y, w, h, DARK, LINE)
            self.text("NO SAMPLE", x + w / 2, y + h / 2, "m", DIM, "c")
            return
        # Drawing a waveform takes tens of milliseconds on a handheld, so each picture is kept: stepping
        # from pad to pad, as when playing, then costs one copy. The entry holds on to the peaks it was
        # drawn from, which is how a pad that has since been given another sample is told apart.
        key = (id(pad.peaks), w, h, color, trim, pad.start, pad.end)     # not where it is drawn: pads often share a sound
        rect = self.R(x, y, w, h)
        hit = self.waves.get(key)
        if hit is not None and hit[0] is pad.peaks:
            self.screen.blit(hit[1], rect)
            return
        self.box(x, y, w, h, DARK, LINE)
        self._draw_wave(pad, x, y, w, h, color, trim)
        if len(self.waves) >= 32:
            self.waves.pop(next(iter(self.waves)))
        self.waves[key] = (pad.peaks, self.screen.subsurface(rect).copy())

    def _draw_wave(self, pad, x, y, w, h, color, trim):
        cols = len(pad.peaks)
        mid = self.oy + (y + h / 2) * self.u
        x0 = self.ox + (x + 2) * self.u
        ww = (w - 4) * self.u
        hh = (h / 2 - 3) * self.u
        wd = max(1, int(ww / cols) + 1)
        fill = self.screen.fill                 # filled bars: several times quicker than wide lines
        for i in range(cols):
            if not i & 31:
                self.breathe()
            f = i / cols
            inside = (not trim) or (pad.start <= f < pad.end)
            a = max(1, int(pad.peaks[i] * hh))
            fill(color if inside else LINE, (int(x0 + f * ww) - wd // 2, int(mid - a), wd, 2 * a + 1))
        if trim:
            for f, c in ((pad.start, TEAL), (pad.end, ORANGE)):
                px = int(x0 + f * ww)
                pygame.draw.line(self.screen, c, (px, int(self.oy + (y + 1) * self.u)),
                                 (px, int(self.oy + (y + h - 1) * self.u)), max(2, self.S(1)))

    def draw(self):
        """Describe the frame as areas of drawing steps, then redraw only the areas that changed."""
        self.areas = []
        try:
            self.describe()
        finally:
            self.ops = None
        old, new = self.frame, self.areas
        over = new[-2][1]                               # dialogs and lists lie on top of everything
        same_layout = len(old) == len(new) and all(a[0] == b[0] for a, b in zip(old, new))
        changed = [i for i in range(len(new))] if not same_layout else [i for i in range(len(new)) if old[i][1] != new[i][1]]
        if not changed:
            return
        rects = []
        last = len(new) - 1                             # the message strip, drawn after everything else
        if not same_layout or over or old[-2][1] or last in changed:
            changed = range(len(new))                   # a dialog, or a message coming or going: redraw the lot
            self.screen.fill(BG)
            rects = None
        elif new[last][1]:
            # A message is showing and stays as it is. Whatever is redrawn underneath would paint over it,
            # so it is drawn again on top, instead of redrawing the whole screen for every pad that lights up.
            mx, my, mw, mh = new[last][0]
            if any(x < mx + mw and mx < x + w and y < my + mh and my < y + h for (x, y, w, h), _ in (new[i] for i in changed)):
                changed.append(last)
        for i in changed:
            (x, y, w, h), ops = new[i]
            if w:
                clip = self.R(x, y, w, h)
                self.screen.set_clip(clip)
                if i != last:                           # the message strip lies over the page: never wipe under it
                    self.screen.fill(BG, clip)
                if rects is not None:
                    rects.append(clip)
            for op in ops:
                self.breathe()
                op[0](*op[1:])
            self.screen.set_clip(None)
        self.present(rects)
        self.frame = new

    def describe(self):
        e = self.eng
        self.area(0, 0, 572, 29)
        # top bar
        x = 6
        for i, name in enumerate(PAGES):
            on = i == self.page
            self.box(x, 5, 44, 20, TEAL if on else PANEL, None if on else LINE)
            self.text(name, x + 22, 15, "m", DARK if on else TEXT, "c")
            x += 46
        x += 4
        blink = int(time.time() * 4) % 2
        for i in range(NPAT):
            on = i == e.cur_pat
            pending = e.pending_pat == i
            fill = ORANGE if on else (mix(PANEL, ORANGE, 0.5) if pending and blink else PANEL)
            self.box(x, 5, 15, 20, fill, None if on else LINE)
            self.text(PAT_NAMES[i], x + 7.5, 15, "m", DARK if on else TEXT, "c")
            x += 17
        self.text("P%02d" % self.loaded, 424, 15, "m", DIM)
        self.text("%d" % round(e.bpm), 487, 15, "l", TEXT, "r")
        self.text("BPM", 490, 16, "s", DIM)
        if self.resampling:
            self.text("RESAMP", 512, 16, "s", RED if blink else ORANGE)
        elif e.rec:
            self.custom(self._dot, RED if (not e.playing or blink) else mix(RED, BG, 0.6))
            self.text("REC", 528, 16, "s", RED)
        elif e.song_on:
            self.text("SONG", 514, 16, "s", TEAL)
        if e.playing:
            self.custom(self._play_mark)
        else:
            self.box(554, 9, 12, 12, DIM, None, 1)
        # output meter, in an area of its own: it moves all the time and the rest of the bar does not
        self.area(572, 0, 68, 29)
        self.box(578, 9, 56, 12, DARK, LINE, 2)
        lv = round(min(1.0, e.peak) * 26) / 26.0        # in steps: a meter that twitches redraws the bar
        if lv > 0.01:
            self.box(580, 11, 52 * lv, 8, RED if lv > 0.95 else TEAL, None, 1)

        self.area(0, 29, 640, 301)
        getattr(self, "draw_" + PAGES[self.page].lower())()
        self.area(0, 0, 0, 0)                           # on top: lists, dialogs, messages (not clipped)
        if self.browser:
            self.draw_browser()
        if self.picker:
            pk = self.picker
            self.box(60, 34, 520, 296, PANEL, ORANGE, 6, 2)
            self.text(pk["title"], 74, 50, "m", ORANGE)
            top = max(0, min(pk["idx"] - 6, len(pk["items"]) - 12))
            for n, (label, _) in enumerate(pk["items"][top:top + 12]):
                y = 66 + n * 20
                if top + n == pk["idx"]:
                    self.box(68, y, 504, 19, mix(PANEL, TEAL, 0.35), None, 3)
                self.text(label[:56], 78, y + 10, "m", TEXT)
            for n, (_, item) in enumerate(pk["items"][top:top + 12]):
                if item == self.previewing:
                    self.text("PLAYING", 564, 76 + n * 20, "s", ORANGE, "r")
            self.text(pk.get("hint") or "A import to pads %d-%d    X play / stop    B cancel" % (self.base() + 1, self.base() + BANK),
                      320, 317, "h", mix(DIM, TEXT, 0.6), "c")
        if self.knobs:
            self.draw_knobs()
        if self.live:
            lv = self.live
            pos = min(1.0, max(0.0, self.live_pos()))
            self.box(20, 60, 600, 250, PANEL, ORANGE, 6, 2)
            self.text("LIVE CHOP", 36, 80, "l", ORANGE)
            self.text("%d of %d slice points" % (len(lv["marks"]), BANK), 604, 81, "m", TEXT, "r")
            self.box(36, 100, 568, 150, DARK, LINE, 4)
            self.custom(self._live_wave, lv["peaks"], pos)
            for k, m in enumerate(lv["marks"]):
                self.box(38 + m * 564, 100, 1.5, 150, ORANGE, None, 0)
                self.text(str(k + 1), 41 + m * 564, 108, "s", ORANGE)
            self.box(38 + pos * 564, 100, 1.5, 150, TEXT, None, 0)
            self.text("A  drop a slice point        B  finish        X  cancel", 320, 272, "m", TEXT, "c")
            self.text("Each point starts a new pad. It finishes by itself at the end.", 320, 292, "s", DIM, "c")
        if self.recording:
            r = self.recording
            left = max(0.0, REC_SECONDS - (time.time() - r["t0"]))
            self.box(120, 110, 400, 130, PANEL, ORANGE, 6, 2)
            self.text("RECORDING", 320, 134, "l", ORANGE, "c")
            self.text("%.1f s left" % left, 320, 162, "m", TEXT, "c")
            self.box(150, 180, 340, 8, DARK, LINE, 3)
            self.box(150, 180, max(2, 340 * (1 - left / REC_SECONDS)), 8, ORANGE, None, 3)
            self.box(150, 196, 340, 8, DARK, LINE, 3)
            self.box(150, 196, max(2, 340 * min(1.0, r["level"] ** 0.5)), 8, TEAL, None, 3)
            self.text("A stop early and use it", 320, 222, "s", DIM, "c")
        if self.namer:
            nm = self.namer
            self.box(100, 62, 440, 246, PANEL, ORANGE, 6, 2)
            self.text("NAME FOR P%02d" % self.loaded, 320, 82, "m", ORANGE, "c")
            self.box(170, 98, 300, 34, DARK, LINE, 4)
            shown = nm["text"] + ("_" if len(nm["text"]) < NAME_MAX and int(time.time() * 2) % 2 else "")
            self.text(shown, 320, 115, "l", TEXT, "c")
            for r, row in enumerate(self.NAME_ROWS):
                for c, ch in enumerate(row):
                    x, y = 125 + c * 39, 144 + r * 33
                    on = (c, r) == (nm["x"], nm["y"])
                    self.box(x, y, 36, 30, TEAL if on else DARK, None if on else LINE, 4)
                    self.text("SPACE" if ch == " " else ch, x + 18, y + 15, "s" if ch == " " else "l", DARK if on else TEXT, "c")
            self.text("A add letter    X delete    START done    B cancel", 320, 292, "h", mix(DIM, TEXT, 0.6), "c")
        if self.confirm:
            lines = self.confirm[0].split("\n")
            if len(lines) == 1 and len(lines[0]) > 44 and "? " in lines[0]:      # too wide for the box: break after the question
                lines = [lines[0][:lines[0].index("? ") + 1], lines[0][lines[0].index("? ") + 2:]]
            if len(lines) == 1 and len(lines[0]) > 44 and ". " in lines[0]:
                lines = [lines[0][:lines[0].index(". ") + 1], lines[0][lines[0].index(". ") + 2:]]
            self.box(150, 130, 340, 90, PANEL, ORANGE, 5, 2)
            for i, line in enumerate(lines[:2]):
                self.text(line, 320, 162 + (i - (len(lines[:2]) - 1) / 2) * 20, "m", TEXT, "c")
            self.text("A = YES     B = NO", 320, 198 if len(lines) > 1 else 192, "m", ORANGE, "c")
        self.area(120, 300, 400, 24)                    # last: the message strip (see draw)
        if time.time() < self.toast_until:
            self.box(120, 300, 400, 24, ORANGE, None, 4)
            self.text(self.toast[:60], 320, 312, "m", DARK, "c")

    def _dot(self, col):
        pygame.draw.circle(self.screen, col, self.R(520, 15, 0, 0).topleft, self.S(5))

    def _play_mark(self):
        cx, cy = self.R(560, 15, 0, 0).topleft
        s = self.S(6)
        pygame.draw.polygon(self.screen, TEAL, [(cx - s, cy - s), (cx - s, cy + s), (cx + s, cy)])

    def _live_wave(self, peaks, pos):
        cols = len(peaks)
        for i in range(cols):
            f = i / cols
            px = int(self.ox + (38 + f * 564) * self.u)
            a = max(1, int(peaks[i] * 70 * self.u))
            mid = int(self.oy + 175 * self.u)
            pygame.draw.line(self.screen, TEAL if f <= pos else LINE, (px, mid - a), (px, mid + a), max(1, self.S(1.2)))

    @staticmethod
    def clip(s, n):
        """Shorten text to n characters, showing that something was cut."""
        return s if len(s) <= n else s[:n - 2].rstrip(", ") + " .."

    def hints(self, s):
        """Button help along the bottom, in readable type; wraps onto a second line when it has to."""
        rows = self.hint_rows.get(s)
        if rows is None:
            parts = [p.strip() for p in s.split("   ") if p.strip()]
            if self.fonts["h"].size("    ".join(parts))[0] <= 624 * self.u or len(parts) < 2:
                rows = [("    ".join(parts), 346)]
            else:
                best = min(range(1, len(parts)), key=lambda k: abs(len("  ".join(parts[:k])) - len("  ".join(parts[k:]))))
                rows = [("    ".join(parts[:best]), 338), ("    ".join(parts[best:]), 353)]
            self.hint_rows[s] = rows
        if self.ops is not None:
            self.area(0, 330, 640, 30)
        for line, y in rows:
            self.text(line, 320, y, "h", mix(DIM, TEXT, 0.6), "c")

    def bank_marker(self, x, y, small=False):
        """Four squares showing which bank of pads is on screen."""
        cur = self.sel // BANK
        w = 12 if small else 17
        for n in range(NBANKS):
            on = n == cur
            self.box(x + n * (w + 2), y, w, 14 if small else 18, ORANGE if on else DARK, None if on else LINE, 3)
            self.text(str(n + 1), x + n * (w + 2) + w / 2, y + (7 if small else 9), "s", DARK if on else DIM, "c")

    def draw_play(self):
        e = self.eng
        now = time.time()
        drum = 0 if "l2" in self.held else (8 if "r2" in self.held else None)
        first = self.base()
        for i, p in enumerate(self.bank_pads(), first):
            r, c = divmod(i - first, 4)
            x, y = 8 + c * 75, 34 + (3 - r) * 75        # pad 1 bottom left, pad 16 top right, as on pad controllers
            self.area(x, y, 71, 71)                     # an area per pad: a hit redraws one pad, not sixteen
            glow = round(min(1.0, max(0.0, 1.0 - (now - e.pad_flash[i]) / 0.18)) * 3) / 3.0   # four steps; safe if the clock jumps
            base = PANEL if p.buf is not None else DARK
            fill = mix(base, TEAL, glow)
            self.box(x, y, 71, 71, fill, ORANGE if i == self.sel else LINE, 6, 2 if i == self.sel else 1)
            col = DIM if (p.mute or p.buf is None) else TEXT
            self.text(str(i + 1), x + 8, y + 16, "l", col)
            self.text(p.name[:9], x + 36, y + 56, "s", col, "c")
            if p.mute:
                self.text("MUTE", x + 64, y + 13, "s", RED, "r")
            if drum is not None and drum <= i - first < drum + 8:
                k = DRUM_KEYS[i - first - drum]
                self.box(x + 22, y + 22, 28, 22, ORANGE, None, 4)
                self.text(DRUM_GLYPH.get(k, k), x + 36, y + 33, "m", DARK, "c")
        p = e.pads[self.sel]
        self.area(314, 29, 326, 163)                    # the selected pad: changes when the selection moves
        self.box(318, 34, 314, 296, PANEL, LINE, 6)
        self.text("PAD %d" % (self.sel + 1), 330, 52, "l", ORANGE)
        self.text(p.name[:12], 540, 53, "m", TEXT, "r")
        self.bank_marker(552, 44)
        self.wave(p, 330, 70, 290, 76, TEAL, trim=p.start > 0 or p.end < 1)
        self.text("VOL %d" % round(p.vol * 100), 330, 162, "m", TEXT)
        self.text("PITCH %+d" % p.pitch, 420, 162, "m", TEXT)
        self.text("%.2fs" % p.seconds(), 620, 162, "m", DIM, "r")
        if e.repeat_on:
            self.box(330, 174, 118, 17, ORANGE, None, 3)
            self.text("NOTE REPEAT %s" % RATES[e.repeat_div][0], 389, 183, "s", DARK, "c")
        if p.choke:
            self.text("CHOKE %d" % p.choke, 620, 183, "s", DIM, "r")
        self.area(314, 192, 326, 138)                   # the help text: never changes
        self.box(318, 34, 314, 296, PANEL, LINE, 6)
        lines = ["A  play pad          B  mute pad",
                 "X  record on/off     Y  sound + samples",
                 "Hold L2 or R2: buttons play 8 pads each",
                 "START  play/stop     L1 R1  page",
                 "SELECT + L1 R1  pattern A-H",
                 "SELECT + up down  tempo",
                 "SELECT + X  note repeat on/off",
                 "SELECT + left right  repeat rate", "SELECT + Y  next bank of pads"]
        for i, s in enumerate(lines):
            self.text(s, 330, 200 + i * 15, "h", mix(DIM, TEXT, 0.6))
        self.hints("SELECT + START  quit (saves the project)")

    def draw_beat(self):
        e = self.eng
        pat = e.patterns[e.cur_pat]
        length = pat["length"]
        self.cur_step = min(self.cur_step, length - 1)
        view = (self.cur_step // 16) * 16
        gx, gy, cw, rh = 96, 46, 33.5, 17.8
        any_solo = any(p.solo for p in e.pads)
        self.text(PAT_NAMES[e.cur_pat], 8, 37, "s", DIM)
        cur_note = pat["notes"][self.sel][self.cur_step] if pat["steps"][self.sel][self.cur_step] else 0
        self.text("%s%d-%d of %d" % ("NOTE %+d    " % cur_note if cur_note else "", view + 1, min(length, view + 16), length),
                  632, 37, "s", ORANGE if cur_note else DIM, "r")
        for s in range(16):
            st = view + s
            if st % 4 == 0 and st < length:
                self.text(str(st + 1), gx + s * cw + cw / 2, 37, "s", DIM, "c")
        if e.playing and view <= e.step < view + 16:
            self.box(gx + (e.step - view) * cw, gy - 1, cw - 2, rh * 16 + 1, mix(BG, TEAL, 0.22), None, 2)
        first = self.base()
        self.bank_marker(96 - 74, 30, small=True)
        for i, p in enumerate(self.bank_pads(), first):
            y = gy + (i - first) * rh
            silent = p.mute or (any_solo and not p.solo) or p.buf is None
            if i == self.sel:
                self.box(4, y, 88, rh - 2, PANEL, None, 2)
            self.text("%02d %s" % (i + 1, p.name[:8]), 8, y + rh / 2 - 1, "s",
                      ORANGE if i == self.sel else (DIM if silent else TEXT))
            if p.solo:
                self.text("S", 90, y + rh / 2 - 1, "s", TEAL, "r")
            elif p.mute:
                self.text("M", 90, y + rh / 2 - 1, "s", RED, "r")
            for s in range(16):
                st = view + s
                x = gx + s * cw
                if st >= length:                  # past the end of the pattern
                    continue
                v = pat["steps"][i][st]
                if v:
                    col = ORANGE if v == 2 else TEXT
                    self.box(x + 2, y + 1, cw - 6, rh - 5, mix(col, BG, 0.6) if silent else col, None, 2)
                    note = pat["notes"][i][st]
                    if note:
                        self.text("%+d" % note, x + cw / 2 - 1, y + rh / 2 - 1.5, "s", DARK, "c")
                else:
                    self.box(x + 2, y + 1, cw - 6, rh - 5, PANEL if (st // 4) % 2 == 0 else mix(PANEL, BG, 0.5),
                             None, 2)
        if self.beat_name:
            self.box(3, gy + (self.sel - first) * rh - 1, 90, rh, None, ORANGE, 3, 2)
            # The pad's sound, shown over the steps: under the row in the top half of the grid, above it in the bottom half.
            p, r = e.pads[self.sel], self.sel - first
            py = gy + (r + 1) * rh + 3 if r < 8 else gy + r * rh - 85
            self.box(gx - 2, py, 538, 80, PANEL, ORANGE, 5, 2)
            self.text("PAD %d  %s" % (self.sel + 1, p.name[:20]), gx + 8, py + 13, "m", ORANGE)
            if p.raw is None:
                self.text("EMPTY  -  press A to pick a sound", gx + 268, py + 48, "m", DIM, "c")
            else:
                self.text("VOL %d    PITCH %+d    %.2fs" % (round(p.vol * 100), p.pitch, p.seconds()), gx + 526, py + 13, "m", TEXT, "r")
                self.wave(p, gx + 4, py + 24, 526, 52, TEAL, trim=p.start > 0 or p.end < 1)
            self.hints("A pick a new sound for this pad   up down pad   SELECT+Y next 16 pads   right back to the steps   "
                       "B mute   X solo   L stick tempo   R stick volume   SELECT+A clear track")
            return
        self.box(gx + (self.cur_step - view) * cw, gy + (self.sel - first) * rh - 1, cw - 2, rh - 1, None, ORANGE, 3, 2)
        self.hints("up down pad   SELECT+Y next 16 pads   A step (hold: paint)   Y accent   B mute   X solo   "
                   "L2 R2 jump   left of step 1: sound   sticks: tempo, volume   SELECT+left right note   SELECT+A / X clear")

    def draw_sound(self):
        e = self.eng
        p = e.pads[self.sel]
        self.text("PAD %d" % (self.sel + 1), 10, 46, "l", ORANGE)
        self.text(p.name[:30], 90, 47, "m", TEXT)
        self.text("%.2fs" % p.seconds(), 630, 47, "m", DIM, "r")
        self.wave(p, 8, 60, 624, 80, TEAL, trim=True)
        if self.sound_row == 0:
            self.box(7, 59, 626, 82, None, ORANGE, 4, 2)
        t = self.auditioning()
        if t is not None:
            f = p.end - (p.end - p.start) * t if p.rev else p.start + (p.end - p.start) * t
            self.box(10 + f * 620 - 0.75, 62, 1.5, 76, TEXT, None, 0)
            self.text("PLAYING", 560, 47, "s", ORANGE, "r")
        filt = "OFF" if not p.filt else ("LOW-PASS %d" % -p.filt if p.filt < 0 else "HIGH-PASS %d" % p.filt)
        vals = {"VOLUME": "%d" % round(p.vol * 100), "PITCH": "%+d semitones" % p.pitch, "FILTER": filt,
                "START": "%.1f%%" % (p.start * 100), "END": "%.1f%%" % (p.end * 100),
                "DELAY": "send %d" % round(p.dsend * 100), "REVERB": "send %d" % round(p.rsend * 100),
                "CHOKE": "group %d  (pads in a group cut each other)" % p.choke if p.choke else "OFF",
                "FINE TUNE": "%+d cents" % p.fine,
                "CHORD": CHORDS[p.chord][0] if not p.chord else "%s  (%s)" % (
                    CHORDS[p.chord][0], "one hit plays the chord" if p.raw is None or p.chord_fits() else "too long - not applied"),
                "KEY CHORDS": "%s  -  A puts its chords on the 16 pads" % self.KEYS[self.key_idx][0],
                "BIT CRUSH": "%d bit" % CRUSH_BITS[p.crush] if p.crush else "OFF",
                "LO-FI RATE": "%.1f kHz" % (SR / 1000.0 / CRUSH_RATES[p.down]) if p.down else "OFF",
                "STRETCH": "OFF" if abs(p.stretch - 100) < 0.05 else "%.4g%%, same pitch" % round(p.stretch, 1),
                "FIT STYLE": "KEEP PITCH (stretch)" if self.fit_keep else "CHANGE PITCH (classic)",
                "MODE": MODES[p.mode] + ("", "  (plays while held)", "  (hit to start, hit to stop)")[p.mode],
                "REVERSE": "ON" if p.rev else "OFF",
                "FADE IN": "%d ms" % p.fin if p.fin else "OFF", "FADE OUT": "%d ms" % p.fout if p.fout else "OFF",
                "CHOP": "%s   -  A to chop onto pads" % self.CHOPS[self.chop_mode][0],
                "FIT TO TEMPO": "%s at %d BPM   -  A to fit" % (FITS[self.fit_idx][0], round(e.bpm)),
                "SPREAD": "across this bank's 16 pads, a semitone apart  -  A",
                "SAMPLE": "%s   -  A to browse files" % (os.path.basename(p.path)[:34] or "(none)")}
        fracs = {"VOLUME": p.vol / 1.5, "PITCH": (p.pitch + 24) / 48.0, "FILTER": (p.filt + 100) / 200.0,
                 "FINE TUNE": (p.fine + 50) / 100.0, "BIT CRUSH": p.crush / (len(CRUSH_BITS) - 1.0),
                 "LO-FI RATE": p.down / (len(CRUSH_RATES) - 1.0),
                 "STRETCH": (p.stretch - STRETCH_MIN) / (STRETCH_MAX - STRETCH_MIN), "START": p.start, "END": p.end, "DELAY": p.dsend,
                 "REVERB": p.rsend, "FADE IN": p.fin / 2000.0, "FADE OUT": p.fout / 2000.0}
        rows, shown = self.SOUND_ROWS[1:], 9
        top = max(0, min(self.sound_row - 1 - 4, len(rows) - shown))
        for n, name in enumerate(rows[top:top + shown]):
            i = top + n + 1
            y = 146 + n * 20.5
            on = i == self.sound_row
            self.box(8, y, 618, 19, PANEL if on else None, ORANGE if on else None, 4, 2)
            self.text(name, 20, y + 10, "m", ORANGE if on else DIM)
            self.text(vals[name][:52 if name not in fracs else 24], 130, y + 10, "m", TEXT)
            if name in fracs:
                self.box(350, y + 7, 264, 6, DARK, LINE, 3)
                if name in ("FILTER", "FINE TUNE", "PITCH"):   # centre-zero bars
                    lo, hi = sorted((0.5, fracs[name]))
                    self.box(350 + 264 * lo, y + 7, max(2, 264 * (hi - lo)), 6, TEAL, None, 3)
                else:
                    self.box(350, y + 7, max(2, 264 * fracs[name]), 6, TEAL, None, 3)
        self.box(630, 146, 3, shown * 20.5 - 2, DARK, None, 1)       # scroll position
        self.box(630, 146 + (shown * 20.5 - 2) * top / len(rows), 3, (shown * 20.5 - 2) * shown / len(rows), DIM, None, 1)
        if self.sound_row == 0:
            self.hints("A or SELECT play / stop   left right move the start   down settings   X samples   L2 R2 pad   B back")
        else:
            self.hints("up down choose   left right or stick change   Y faster   SELECT play / stop   X samples   L2 R2 pad   B back")

    def draw_fx(self):
        e = self.eng
        vals = {"COMPRESSOR": "%d%%" % round(e.comp * 100) if e.comp else "OFF", "DELAY TIME": DELAY_TIMES[e.delay_div][0], "DELAY FEEDBACK": "%d%%" % round(e.delay_fb * 100),
                "REVERB SIZE": "%d%%" % round(e.reverb_size * 100), "STUTTER LENGTH": STUTTERS[e.stutter_div][0],
                "REPEAT RATE": RATES[e.repeat_div][0]}
        for row, axis in self.STICK_ROWS.items():
            vals[row] = self.stick_fx[axis]
        for i, name in enumerate(self.FX_ROWS):
            y = 36 + i * 26 + (8 if name in self.STICK_ROWS else 0)
            on = i == self.fx_row
            self.box(8, y, 318, 24, PANEL if on else None, ORANGE if on else None, 4, 2)
            self.text(name, 20, y + 12, "m", ORANGE if on else DIM)
            self.text(vals[name], 316, y + 12, "m", TEXT, "r")
        self.text("Sticks: pick the effect each direction plays.", 20, 316, "s", mix(DIM, TEXT, 0.6))
        self.box(338, 34, 294, 296, PANEL, LINE, 6)
        self.text("PERFORM", 350, 52, "l", ORANGE)
        self.text("hold a button or push a stick", 622, 53, "s", DIM, "r")
        spots = {"X": (348, 68), "Y": (488, 68), "A": (348, 102), "B": (488, 102)}
        for btn, (x, y) in spots.items():
            fx, label = self.PERFORM[btn]
            on = e.perf[fx]
            self.box(x, y, 134, 28, ORANGE if on else DARK, None if on else LINE, 6)
            self.text(btn, x + 16, y + 14, "m", DARK if on else ORANGE, "c")
            self.text(label, x + 78, y + 14, "s", DARK if on else TEXT, "c")
        # the two sticks: a dot shows the push, the effect it is reaching for lights up
        live = PAGES[self.page] == "FX" and not self.modal()
        lv = self.stick_level
        for title, cx, ax, ay in (("LEFT STICK", 412, "lx", "ly"), ("RIGHT STICK", 556, "rx", "ry")):
            cy, r = 234, 30
            self.text(title, cx, 152, "s", DIM, "c")
            self.box(cx - r, cy - r, r * 2, r * 2, DARK, LINE, r)
            self.box(cx - r, cy - 0.5, r * 2, 1, LINE, None, 0)
            self.box(cx - 0.5, cy - r, 1, r * 2, LINE, None, 0)
            x, y = (self.sticks[ax], self.sticks[ay]) if live else (0.0, 0.0)
            busy = False
            # each direction's label, lit while that effect is sounding
            for axis, lo_at, hi_at in ((ax, (cx - r - 5, cy, "r"), (cx + r + 5, cy, "l")),
                                       (ay, (cx, cy + r + 11, "c"), (cx, cy - r - 10, "c"))):
                name = self.stick_fx[axis]
                labels = self.STICK_FX[name]
                level = lv.get(name, 0.0)
                busy = busy or bool(level)
                if len(labels) == 2:
                    self.text(labels[0], lo_at[0], lo_at[1], "s", ORANGE if level < 0 else TEXT, lo_at[2])
                    self.text(labels[1], hi_at[0], hi_at[1], "s", ORANGE if level > 0 else TEXT, hi_at[2])
                elif labels:
                    self.text(labels[0], hi_at[0], hi_at[1], "s", ORANGE if level else TEXT, hi_at[2])
                    self.text(labels[0], lo_at[0], lo_at[1], "s", ORANGE if level else mix(DIM, TEXT, 0.4), lo_at[2])
            self.box(cx + x * (r - 6) - 6, cy + y * (r - 6) - 6, 12, 12, ORANGE if busy else DIM, None, 6)
        self.text("Push further for more. Let go and it snaps back.", 485, 312, "s", mix(DIM, TEXT, 0.6), "c")
        self.hints("up down choose   left right change   hold A B X Y or push the sticks to perform")

    def draw_proj(self):
        e = self.eng
        free = self.free_slot()
        saved = sum(os.path.exists(self.slot_path(n)) for n in range(1, SLOTS + 1) if n != self.loaded)
        vals = {"SAVE": "%s  -  A saves it" % self.slot_label(self.loaded),
                "SAVE A COPY": "as a new project in P%02d" % free if free else "no empty slot left",
                "OPEN": "another project  (%d saved)" % saved if saved else "no other projects yet",
                "NAME": ("%s  -  A to rename P%02d" % (e.name, self.loaded)) if e.name else "none  -  A to name P%02d" % self.loaded,
                "NEW": "starter kit, or a style with a full song", "TEMPO": "%d BPM" % round(e.bpm),
                "SWING": "%d%%  (50 straight, 66 triplet)" % round(e.swing),
                "MIDI OUT": ("channel %d  -  pads send notes, plus clock" % self.midi.out_channel) if self.midi.out_channel
                            else "OFF",
                "MIDI DEVICES": (self.clip(", ".join(self.midi_names()), 40) or "none found  -  plug one in") if self.midi.running
                                else self.midi.error or "off",
                "MASTER": "%d" % round(e.master * 100), "QUIT": "save and exit",
                "MIDI KNOBS": "A to choose what each knob changes",
                "BLUETOOTH MIDI": self.clip(self.ble.status, 40) if self.ble.running else "A to search for controllers",
                "RECORD MIC": "%d s from the mic, chopped onto pads %d-%d" % (REC_SECONDS, self.base() + 1, self.base() + BANK),
                "RESAMPLE": "record what you hear onto pad %d" % (self.sel + 1),
                "EXPORT AUDIO": "save %s as a WAV file" % ("the song" if (e.song_on and e.song)
                                                           else "pattern %s x 4" % PAT_NAMES[e.cur_pat]),
                "IMPORT CHOPS": "slices from a GrainBrick project",
                "GRAINBRICK": "open it to sample, quit to return"}
        shown = 13
        top = max(0, min(self.proj_row - shown // 2, len(self.PROJ_ROWS) - shown))
        if len(self.PROJ_ROWS) > shown:
            self.box(411, 36, 3, shown * 22.6 - 4, DARK, None, 1)
            self.box(411, 36 + (shown * 22.6 - 4) * top / len(self.PROJ_ROWS), 3,
                     (shown * 22.6 - 4) * shown / len(self.PROJ_ROWS), DIM, None, 1)
        for i, name in enumerate(self.PROJ_ROWS):
            if not top <= i < top + shown:
                continue
            y = 34 + (i - top) * 22.6
            on = i == self.proj_row
            self.box(8, y, 398, 21, PANEL if on else None, ORANGE if on else None, 4, 2)
            self.text(name, 20, y + 10.5, "m", ORANGE if on else DIM)
            self.text(vals[name], 150, y + 10.5, "m", TEXT)
        self.box(418, 38, 214, 275, PANEL, LINE, 6)
        self.text("PocketPads %s" % VERSION, 622, 326, "s", DIM, "r")
        info = ["GETTING SOUNDS IN", "", "RECORD MIC is at the top of", "this list.", "",
                "To load a file: SOUND page,", "SAMPLE row, press A.", "Put WAV or MP3 files in", "ports/pocketpads/samples/", "",
                "audio: %s" % ("off" if e.dev is None else "ok"),
                "dropouts: %d" % e.underruns, "peak load: %.1f ms" % e.max_render_ms]
        for i, s in enumerate(info):
            self.text(s, 430, 56 + i * 19, "h", TEXT if i == 0 else mix(DIM, TEXT, 0.6))
        self.hints("up down choose   left right change   A confirm")

    def draw_browser(self):
        br = self.browser
        self.box(60, 34, 520, 296, PANEL, ORANGE, 6, 2)
        if br["dir"] is None or not self.root_of(br["dir"]):
            where = "choose a folder"
        else:
            label, rd = self.root_of(br["dir"])
            sub = os.path.relpath(br["dir"], rd)
            where = label if sub == "." else "%s / %s" % (label, sub[-30:])
        self.text("PAD %d SAMPLE   %s" % (self.sel + 1, where), 74, 50, "m", ORANGE)
        items = br["items"]
        if len([i for i in items if i[0] != "up"]) == 0:
            self.text("No WAV or MP3 files here yet.", 320, 190, "m", DIM, "c")
        rows = 12
        top = max(0, min(br["idx"] - rows // 2, len(items) - rows))
        for n, (kind, name) in enumerate(items[top:top + rows]):
            y = 66 + n * 20
            on = top + n == br["idx"]
            if on:
                self.box(68, y, 504, 19, mix(PANEL, TEAL, 0.35), None, 3)
            label = name[:52] if kind == "wav" else "[ %s ]" % name[:48]
            if kind == "root":
                label = name
            self.text(label, 78, y + 10, "m", TEXT if kind == "wav" else TEAL)
            if kind == "wav" and not name.lower().endswith(".wav"):
                self.text(os.path.splitext(name)[1][1:].upper(), 562, y + 10, "s", ORANGE, "r")
        self.text("A choose   X hear   Y folder onto pads   START beat   R1 in the beat: %s   B cancel" % ("ON" if self.in_beat else "OFF"),
                  320, 317, "h", mix(DIM, TEXT, 0.6), "c")

    # ------------------------------------------------------------ scripted testing
    def load_script(self):
        path = os.environ.get("PP_SCRIPT")
        if not path:
            return []
        with open(path) as f:
            return [ln.split() for ln in f if ln.strip() and not ln.startswith("#")]

    def run_script(self):
        while self.script and time.time() >= self.script_wait:
            cmd = self.script.pop(0)
            if cmd[0] == "press":
                self.button(cmd[1], True)
                self.button(cmd[1], False)
            elif cmd[0] in ("down", "up"):
                self.button(cmd[1], cmd[0] == "down")
            elif cmd[0] == "wheel":
                self.wheel = float(cmd[1])
            elif cmd[0] == "stick":             # stick lx ly rx ry
                self.sticks.update(zip(("lx", "ly", "rx", "ry"), map(float, cmd[1:5])))
            elif cmd[0] == "wait":
                self.script_wait = time.time() + float(cmd[1])
            elif cmd[0] == "shot":
                self.draw()
                pygame.image.save(self.screen, cmd[1])
            elif cmd[0] == "midi":           # e.g. "midi note 38 100", "midi cc 21 64"
                self.midi.events.append((cmd[1],) + tuple(int(x) for x in cmd[2:]))
            elif cmd[0] == "quit":
                self.running = False

    # ------------------------------------------------------------ MIDI
    # What a knob can change: name -> (pad or engine, attribute, lowest value, highest value, re-render the pad?)
    TARGETS = {"VOLUME": ("pad", "vol", 0.0, 1.5, False), "PITCH": ("pad", "pitch", -24, 24, True),
               "FILTER": ("pad", "filt", -100, 100, True), "DELAY": ("pad", "dsend", 0.0, 1.0, False),
               "REVERB": ("pad", "rsend", 0.0, 1.0, False), "START": ("pad", "start", 0.0, 0.98, True),
               "END": ("pad", "end", 0.02, 1.0, True), "FADE OUT": ("pad", "fout", 0, 2000, True),
               "TEMPO": ("eng", "bpm", 60, 187, False), "MASTER": ("eng", "master", 0.0, 1.5, False),
               "SWING": ("eng", "swing", 50, 75, False), "COMPRESSOR": ("eng", "comp", 0.0, 1.0, False),
               "DELAY FEEDBACK": ("eng", "delay_fb", 0.0, 0.9, False), "REVERB SIZE": ("eng", "reverb_size", 0.0, 1.0, False),
               "OFF": None}
    # Knobs 1-8 shape the selected pad; a second set of eight (a controller's knob bank) shapes the whole mix.
    KNOB_DEFAULT = ["VOLUME", "PITCH", "FILTER", "DELAY", "REVERB", "FADE OUT", "START", "END",
                    "TEMPO", "MASTER", "SWING", "COMPRESSOR", "DELAY FEEDBACK", "REVERB SIZE", "OFF", "OFF"]

    # Controller numbers that are not knobs: bank select, mod wheel, data entry, channel volume, pedals
    # and the channel-mode messages (120-127, such as "all notes off", which many controllers send on stop).
    NOT_KNOBS = frozenset([0, 1, 6, 7, 32, 38] + list(range(64, 70)) + list(range(96, 102)) + list(range(120, 128)))

    # The M-Vave SMC-PAD as it comes, read from a real unit over Bluetooth: knobs 1-8 send controllers 30-37
    # (knob 1 is bottom left, 2 bottom right, and so on upward), and 38-45 after its KNOB BANK button. They send
    # a position, 0-127. PAUSE sends the same as STOP. PAD BANK moves the pads from notes 36-51 to 52-67.
    # Used only while a device with SMC in its name is connected.
    SMC_KNOBS = list(range(30, 46))
    SMC_BUTTONS = {25: "back", 26: "forward", 27: "play", 28: "stop", 29: "record"}
    MMC = {1: "stop", 2: "play", 3: "play", 6: "record", 7: "record", 9: "pause"}

    def midi_names(self):
        """Every connected controller, USB and Bluetooth."""
        return self.midi.devices + self.ble.devices

    def smc(self):
        """An SMC-PAD is connected (the full-size one: the Pocket has pads only)."""
        return any("smc" in d.lower() and "pocket" not in d.lower() for d in self.midi_names())

    def transport(self, what):
        """A controller's transport button."""
        e = self.eng
        if what == "play" and not e.playing:
            e.play()
        elif what == "stop" and e.playing:
            e.stop()
        elif what == "pause":
            e.toggle_play()
        elif what == "record":
            e.rec = not e.rec
            self.say("RECORD ON: PLAY PADS WHILE RUNNING" if e.rec else "RECORD OFF")
        elif what in ("back", "forward") and not self.modal():
            if e.playing and e.song_on and e.song:
                self.say("SONG MODE IS ON: PATTERNS FOLLOW THE CHAIN", 2.5)
            else:
                d = 1 if what == "forward" else -1
                e.select_pattern(((e.pending_pat if e.pending_pat is not None else e.cur_pat) + d) % NPAT)

    def turn_knob(self, cc, value):
        """A knob is known by its controller number. One not seen before becomes the next knob in the list."""
        e = self.eng
        if cc not in self.knob_ccs:
            if cc in self.NOT_KNOBS or len(self.knob_ccs) >= len(self.KNOB_DEFAULT):
                return
            self.knob_ccs.append(cc)
        k = self.knob_ccs.index(cc)
        if self.knobs:                              # the knob list is open: turning a knob finds its row
            self.knobs["row"] = k
            return
        spec = self.TARGETS[self.knob_fn[k]]
        if spec is None or self.modal():
            return
        where, attr, lo, hi, bake = spec
        v = lo + (hi - lo) * min(127, max(0, value)) / 127.0
        v = int(round(v)) if isinstance(lo, int) else round(v, 3)
        if where == "eng":
            setattr(e, attr, float(v))
            self.say("KNOB %d: %s %s" % (k + 1, self.knob_fn[k], v if isinstance(lo, int) else round(v * 100)), 1.2)
            return
        p = e.pads[self.sel]
        setattr(p, attr, v)
        p.start, p.end = min(p.start, 0.98), max(p.end, min(1.0, p.start + 0.02))
        self.say("KNOB %d: %s, PAD %d" % (k + 1, self.knob_fn[k], self.sel + 1), 1.2)
        if bake and p.raw is not None:
            if self.pending_bake and self.pending_bake[0] is not p:
                self.flush_bake()
            self.pending_bake = (p, time.time() + 0.15)     # wait until the knob stops turning

    def knob_rows(self):
        return min(len(self.KNOB_DEFAULT), max(8, len(self.knob_ccs)))

    def press_knobs(self, b):
        kn = self.knobs
        n = self.knob_rows()
        kn["row"] = min(kn["row"], n - 1)
        if b == "up":
            kn["row"] = (kn["row"] - 1) % n
        elif b == "down":
            kn["row"] = (kn["row"] + 1) % n
        elif b in ("left", "right"):
            names = list(self.TARGETS)
            k = kn["row"]
            self.knob_fn[k] = names[(names.index(self.knob_fn[k]) + (1 if b == "right" else -1)) % len(names)]
        elif b == "X":
            def reset():
                self.knob_fn = list(self.KNOB_DEFAULT)
                self.knob_ccs = list(self.SMC_KNOBS) if self.smc() else []
            self.confirm = ("PUT ALL KNOBS BACK AS THEY CAME?", reset)
        elif b in ("B", "A", "start"):
            self.knobs = None
            try:
                self.save_state()
            except OSError as e:
                self.say("SAVE FAILED: %s" % e, 4)

    def draw_knobs(self):
        kn = self.knobs
        n = self.knob_rows()
        kn["row"] = min(kn["row"], n - 1)
        self.box(60, 34, 520, 296, PANEL, ORANGE, 6, 2)
        self.text("MIDI KNOBS", 74, 50, "m", ORANGE)
        self.text(self.clip(", ".join(self.midi_names()), 34) or "no controller connected", 566, 50, "m", DIM, "r")
        top = max(0, min(kn["row"] - 5, n - 10))
        for i in range(top, min(n, top + 10)):
            y = 64 + (i - top) * 20
            on = i == kn["row"]
            if on:
                self.box(68, y, 504, 19, mix(PANEL, TEAL, 0.35), None, 3)
            self.text("KNOB %d" % (i + 1), 78, y + 10, "m", ORANGE if on else DIM)
            name = self.knob_fn[i]
            spec = self.TARGETS[name]
            self.text(name, 170, y + 10, "m", TEXT if spec else DIM)
            if spec:
                self.text("selected pad" if spec[0] == "pad" else "whole mix", 330, y + 10, "h", DIM)
            self.text("CC %d" % self.knob_ccs[i] if i < len(self.knob_ccs) else "turn it to add it", 564, y + 10, "h", DIM, "r")
        self.text("LAST RECEIVED:  %s" % (self.midi_last or "nothing yet"), 320, 280, "h", TEXT, "c")
        self.text("left right change   turn a knob to find it   X all back to default   B done", 320, 317, "h", mix(DIM, TEXT, 0.6), "c")

    def tick_midi(self):
        """Feed the MIDI port what it needs for clock, and act on what controllers sent."""
        e, m = self.eng, self.midi
        m.bpm, m.playing = e.bpm * 1.5 ** e.fx_tempo, e.playing
        m.latency = (e.queue_blocks + 1) * BLOCK / SR
        names = self.midi_names()
        if len(names) < self.midi_seen:         # a controller was unplugged: let go of whatever it held
            for pad in self.midi_held.values():
                e.note_off(pad)
            for pad in self.midi_rep.values():
                e.release(pad)
            self.midi_held.clear()
            self.midi_rep.clear()
            self.midi_block = None
        self.midi_seen = len(names)
        smc = self.smc()
        if smc and self.knob_ccs != self.SMC_KNOBS:     # an SMC-PAD: its two banks of knobs are knobs 1-16
            self.knob_ccs = list(self.SMC_KNOBS)
        for ev in m.poll():
            kind = ev[0]
            if kind != "off":
                fmt = {"note": "NOTE %d", "cc": "CC %d = %d", "pc": "PROGRAM %d", "mmc": "MMC %d",
                       "start": "START", "stop": "STOP"}[kind]
                self.midi_last = fmt % tuple(ev[1:1 + fmt.count("%")])
                if ev[:2] not in self.midi_logged and len(self.midi_logged) < 300:
                    self.midi_logged.add(ev[:2])
                    self.logline("midi in: %s" % self.midi_last)
            if kind == "note":
                # A controller plays the bank on screen: note 36 (C1) is the bank's first pad, and the sixteen
                # pads repeat every sixteen notes, so a controller's own pads land on them wherever they start.
                block, i = divmod(ev[1] - midi.BASE_NOTE, BANK)
                if smc and self.midi_block not in (None, block) and not self.modal():
                    # Its PAD BANK button moved its pads to another sixteen notes: its first bank (notes 36-51)
                    # is our pads 1-16 and its second is 17-32, whatever bank was on screen before.
                    self.flush_bake()
                    self.sel = (block % NBANKS) * BANK + self.sel % BANK
                    self.say("BANK %d: PADS %d-%d" % (self.sel // BANK + 1, self.base() + 1, self.base() + BANK), 1.5)
                self.midi_block = block
                pad = self.base() + i
                if not self.modal():                # a dialog names its pads: do not move them under it
                    if pad != self.sel:
                        self.flush_bake()
                    self.sel = pad
                if e.repeat_on:                     # note repeat: it keeps firing until the pad is let go
                    e.hold(pad)
                    self.midi_rep[ev[1]] = pad
                    continue
                # echo=False: a note that came in is not sent back out, or a device with "thru" would loop
                e.trigger(pad, min(1.3, max(0.3, ev[2] / 100.0)), echo=False)
                if e.pads[pad].mode == HOLD:
                    self.midi_held[ev[1]] = pad
            elif kind == "off":
                if ev[1] in self.midi_held:
                    e.note_off(self.midi_held.pop(ev[1]))
                if ev[1] in self.midi_rep:
                    e.release(self.midi_rep.pop(ev[1]))
            elif kind == "cc":
                if smc and ev[1] in self.SMC_BUTTONS:
                    if ev[2] > 0:                   # the press; letting go sends 0
                        self.transport(self.SMC_BUTTONS[ev[1]])
                else:
                    self.turn_knob(ev[1], ev[2])
            elif kind in ("start", "stop"):
                self.transport("play" if kind == "start" else "stop")
            elif kind == "mmc" and ev[1] in self.MMC:
                self.transport(self.MMC[ev[1]])

    # ------------------------------------------------------------ main loop
    def run(self):
        try:
            self.loop()
        finally:                                    # even after an error, what was made is saved
            self.save_project(quiet=True, autosave=True)

    def loop(self):
        clock = pygame.time.Clock()
        while self.running:
            for ev in pygame.event.get():
                self.handle_event(ev)
            self.tick_repeat()
            self.tick_record()
            self.flush_bake(force=False)
            self.tick_live()
            self.tick_sticks()
            self.tick_wheel(1 / 30.0)
            self.tick_beat_sticks(1 / 30.0)
            self.tick_trial()
            self.tick_midi()
            if self.resampling and time.time() - self.resampling[0] > 60:
                self.finish_resample()
            self.run_script()
            self.draw()
            if self.previewing and time.time() >= self.preview_end:
                self.previewing = None
            clock.tick(30)
        if self.eng.render_errors:
            self.logline("render errors: %d, first:\n%s" % (self.eng.render_errors, self.eng.last_error))
        self.logline("exit: dropouts=%d peak_render=%.1fms" % (self.eng.underruns, self.eng.max_render_ms))
        self.midi.stop()
        self.ble.stop()
        self.eng.shutdown()
        pygame.quit()
        return self.exit_code


if __name__ == "__main__":
    try:
        sys.exit(App().run())
    except Exception:
        with open(os.path.join(APP_DIR, "pocketpads_crash.log"), "w") as f:
            traceback.print_exc(file=f)
        traceback.print_exc()
        sys.exit(1)
