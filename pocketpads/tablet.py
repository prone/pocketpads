"""The tablet layout: PocketPads worked by touch alone, on a screen with room for it (an iPad).

It is the same app underneath: the same engine, projects and actions as the handheld layout in main.py.
What changes is how a page is laid out and how it is worked. There are no on-screen gamepad buttons;
pads are hit with fingers, settings are knobs that are dragged, and the top bar is tapped.

A page with a tablet layout is drawn by tab_<page> here. A page that has none yet is still drawn by
main.py, as on a handheld, under the tablet's top bar.

Every control a page draws is also entered in self.hot with what a touch on it does, so the picture
and the touch handling cannot drift apart.

Units are the same as the handheld's: the screen is 640 wide. Its height (self.LH) depends on the
screen's shape: about 441 on an 11 inch iPad.
"""
import math
import time

import pygame

from engine import (BANK, CHOKE_GROUPS, CHORDS, CRUSH_BITS, CRUSH_RATES, DELAY_TIMES, FITS, MAX_STEPS, MIN_STEPS, MODES,
                    NBANKS, NPAT, PAT_NAMES, RATES, SONG_SLOTS, STRETCH_MAX, STRETCH_MIN, STUTTERS, VARIANT_MAX_SECONDS)
from theme import BG, DARK, DIM, LINE, ORANGE, PANEL, RED, TEAL, TEXT, mix

TOP = 38                    # height of the bar along the top
KNOB_TRAVEL = 160.0         # a drag this long takes a knob from one end to the other
DOUBLE_TAP = 0.35           # seconds: two taps on a knob this close together put it back to its usual value


def _percent(v):
    return "%d" % round(v * 100)


def _filter(v):
    return "OFF" if not v else ("LOW %d" % -v if v < 0 else "HIGH %d" % v)


def _fade(v):
    return "OFF" if not v else "%d ms" % v


# The knobs for the selected pad on the PLAY page: (label, what it changes (a name in App.TARGETS),
# its usual value, how the value is written, whether the middle is zero).
PLAY_KNOBS = [("VOLUME", "VOLUME", 0.8, _percent, False), ("PITCH", "PITCH", 0, lambda v: "%+d" % v, True),
              ("FILTER", "FILTER", 0, _filter, True), ("FADE OUT", "FADE OUT", 0, _fade, False),
              ("START", "START", 0.0, lambda v: "%d%%" % round(v * 100), False),
              ("END", "END", 1.0, lambda v: "%d%%" % round(v * 100), False),
              ("DELAY", "DELAY", 0.0, _percent, False), ("REVERB", "REVERB", 0.0, _percent, False),
              # a third row, where the screen has room for it
              ("FINE TUNE", "FINE TUNE", 0, lambda v: "%+d" % v, True), ("FADE IN", "FADE IN", 0, _fade, False),
              ("BIT CRUSH", "BIT CRUSH", 0, lambda v: "%d BIT" % CRUSH_BITS[v] if v else "OFF", False),
              ("CHOKE", "CHOKE", 0, lambda v: "GROUP %d" % v if v else "OFF", False)]
# Settings the tablet has knobs for that App.TARGETS (the MIDI knobs' list) does not: the same five things each.
TAB_TARGETS = {"FINE TUNE": ("pad", "fine", -50, 50, True), "FADE IN": ("pad", "fin", 0, 2000, True),
               "BIT CRUSH": ("pad", "crush", 0, len(CRUSH_BITS) - 1, True), "CHOKE": ("pad", "choke", 0, CHOKE_GROUPS, False),
               "LO-FI RATE": ("pad", "down", 0, len(CRUSH_RATES) - 1, True),
               "STRETCH": ("pad", "stretch", STRETCH_MIN, STRETCH_MAX, True)}
# Every knob there is, by what it changes: the pad's on PLAY, then the ones only other pages have.
KNOBS = {k[1]: k for k in PLAY_KNOBS + [
    ("LO-FI RATE", "LO-FI RATE", 0, lambda v: "1/%d" % CRUSH_RATES[v] if v else "OFF", False),
    ("STRETCH", "STRETCH", 100.0, lambda v: "OFF" if abs(v - 100) < 0.05 else "%d%%" % round(v), True),
    ("COMPRESSOR", "COMPRESSOR", 0.0, lambda v: "%d%%" % round(v * 100) if v else "OFF", False),
    ("MASTER", "MASTER", 0.8, _percent, False), ("SWING", "SWING", 50, lambda v: "%d%%" % round(v), False),
    ("DELAY FEEDBACK", "DELAY FEEDBACK", 0.45, lambda v: "%d%%" % round(v * 100), False),
    ("REVERB SIZE", "REVERB SIZE", 0.4, lambda v: "%d%%" % round(v * 100), False)]}
# BEAT page: what a touch on the grid does.
BEAT_MODES = ("DRAW", "ACCENT", "NOTE")
BEAT_HELP = ("tap a step, or drag along a row", "tap a step to make it louder", "drag a step up or down to change its note")


class Tablet:
    """Mixed into App. Everything here is used only when self.tablet is set."""

    def tablet_init(self):
        self.hot = []               # the controls on screen: (x, y, w, h, what a touch does)
        self.grabs = {}             # finger -> the control it is on
        self.tab_mute = False       # PLAY page: tapping a pad mutes it instead of playing it
        self.tab_taps = []          # when the tempo was last tapped, for tap tempo
        self.tab_last = (None, 0.0)  # the knob last tapped and when, to catch a double tap
        self.tab_baked = 0.0        # when a knob last re-rendered a pad
        self.beat_mode = 0          # BEAT page: index into BEAT_MODES

    def tab_target(self, name):
        return TAB_TARGETS.get(name) or self.TARGETS[name]

    def tablet_lay_out(self, left, top, w, h):
        self.u = w / 640.0
        self.LH = h / self.u
        if self.LH < 400:           # a screen wider than it is useful: keep the height, centre the page
            self.u, self.LH = h / 400.0, 400.0
        self.ox = left + (w - 640 * self.u) / 2
        self.oy = top
        self.dlg_dy = (self.LH - 360) / 2       # dialogs are drawn for a 360 high screen: centre them

    # ------------------------------------------------------------ drawing
    def tablet_describe(self):
        self.hot = []
        self.tab_top()
        name = self.PAGE_NAMES[self.page].lower()
        own = getattr(self, "tab_" + name, None)
        if own:
            own()
        else:                                   # no tablet layout yet: the handheld's page, under our bar
            self.dy = TOP - 29
            self.area(0, 29, 640, 301)
            getattr(self, "draw_" + name)()
        self.dy = self.dlg_dy
        self.describe_over(toast_dy=TOP + 4 - 300)      # messages drop down from under the bar
        self.dy = 0

    def tab_button(self, x, y, w, h, label, on=False, fn=None, font="m", colour=TEAL, radius=5):
        """A button that is tapped. `on` lights it."""
        self.box(x, y, w, h, colour if on else PANEL, None if on else LINE, radius)
        if label:
            self.text(label, x + w / 2, y + h / 2, font, DARK if on else TEXT, "c")
        if fn:                                      # a finger is wider than the picture: the gaps around it count too
            self.hot.append((x - 1, y - 3, w + 2, h + 6, {"kind": "tap", "fn": fn}))

    def tab_top(self):
        e = self.eng
        self.area(0, 0, 602, TOP)
        for i, name in enumerate(self.PAGE_NAMES):
            self.tab_button(6 + i * 46, 5, 44, 28, name, i == self.page, lambda i=i: setattr(self, "page", i))
        blink = int(time.time() * 4) % 2
        for i in range(NPAT):
            on = i == e.cur_pat
            wait = e.pending_pat == i and blink
            x = 288 + i * 23
            self.box(x, 5, 21, 28, ORANGE if on else (mix(PANEL, ORANGE, 0.5) if wait else PANEL), None if on else LINE, 5)
            self.text(PAT_NAMES[i], x + 10.5, 19, "m", DARK if on else TEXT, "c")
            self.hot.append((x - 1, 0, 23, TOP, {"kind": "tap", "fn": lambda i=i: self.tab_pattern(i)}))
        # tempo: drag it up or down, or tap it in time
        self.box(478, 5, 50, 28, DARK, LINE, 5)
        self.text("%d" % round(e.bpm), 503, 16, "m", TEXT, "c")
        self.text("SONG" if e.song_on else "BPM", 503, 27, "t", TEAL if e.song_on else DIM, "c")
        self.hot.append((476, 0, 54, TOP, {"kind": "tempo"}))
        self.tab_button(532, 5, 32, 28, "", e.rec, self.tab_rec, colour=mix(PANEL, RED, 0.45))
        self.custom(self._tab_icon, "dot", 548, 19, RED if (not e.rec or not e.playing or blink) else mix(RED, DARK, 0.5))
        self.tab_button(568, 5, 32, 28, "", e.playing, e.toggle_play)
        self.custom(self._tab_icon, "stop" if e.playing else "play", 584, 19, DARK if e.playing else TEAL)
        # the output meter, in an area of its own: it moves all the time and the rest of the bar does not
        self.area(602, 0, 38, TOP)
        self.box(606, 5, 28, 28, DARK, LINE, 4)
        lv = round(min(1.0, e.peak) * 24) / 24.0
        if lv > 0.01:
            self.box(609, 8 + 22 * (1 - lv), 22, 22 * lv, RED if lv > 0.95 else TEAL, None, 1)

    def _tab_icon(self, kind, cx, cy, col):
        px, py = int(self.ox + cx * self.u), int(self.oy + cy * self.u)
        s = self.S(6)
        if kind == "dot":
            pygame.draw.circle(self.screen, col, (px, py), s)
        elif kind == "play":
            pygame.draw.polygon(self.screen, col, [(px - s + 1, py - s), (px - s + 1, py + s), (px + s, py)])
        else:
            pygame.draw.rect(self.screen, col, (px - s + 1, py - s + 1, 2 * s - 2, 2 * s - 2), 0, self.S(1))

    def tab_play(self):
        e = self.eng
        now = time.time()
        side = min(self.LH - TOP - 8, 420.0)            # the square the sixteen pads fill
        pitch = side / 4.0
        first = self.base()
        for i, p in enumerate(self.bank_pads(), first):
            r, c = divmod(i - first, 4)
            x, y = 6 + c * pitch, TOP + 4 + (3 - r) * pitch     # pad 1 bottom left, as on pad controllers
            w = pitch - 4
            self.area(x, y, w, w)                       # an area per pad: a hit redraws one pad, not sixteen
            glow = round(min(1.0, max(0.0, 1.0 - (now - e.pad_flash[i]) / 0.18)) * 3) / 3.0
            fill = mix(PANEL if p.buf is not None else DARK, TEAL, glow)
            self.box(x, y, w, w, fill, ORANGE if i == self.sel else LINE, 8, 2 if i == self.sel else 1)
            col = DIM if (p.mute or p.buf is None) else TEXT
            self.text(str(i + 1), x + 9, y + 17, "l", col)
            self.text(p.name[:11], x + w / 2, y + w - 14, "s", col, "c")
            if p.mute:
                self.text("MUTE", x + w - 7, y + 13, "s", RED, "r")
            self.hot.append((x - 2, y - 2, pitch, pitch, {"kind": "pad", "pad": i}))
        # the selected pad
        p = e.pads[self.sel]
        px, py = 6 + side + 2, TOP + 4
        pw, ph = 640 - px - 6, side - 4
        panel = lambda: self.box(px, py, pw, ph, PANEL, LINE, 8)        # noqa: E731
        self.area(px, py, pw, 98)                       # the areas tile the panel: each redraws its own strip of it
        panel()
        self.text("PAD %d" % (self.sel + 1), px + 10, py + 17, "l", ORANGE)
        self.text(p.name[:12], px + 10, py + 86, "s", DIM)
        self.text("%.2fs" % p.seconds(), px + pw - 10, py + 86, "s", DIM, "r")
        for n in range(NBANKS):                         # the four banks of sixteen pads
            on = n == self.sel // BANK
            bx = px + pw - 8 - (NBANKS - n) * 25
            self.box(bx, py + 6, 23, 23, ORANGE if on else DARK, None if on else LINE, 4)
            self.text(str(n + 1), bx + 11.5, py + 17.5, "m", DARK if on else DIM, "c")
            self.hot.append((bx - 1, py, 25, 32, {"kind": "tap", "fn": lambda n=n: self.tab_bank(n)}))
        self.wave(p, px + 8, py + 34, pw - 16, 44, TEAL, trim=p.start > 0 or p.end < 1)
        self.hot.append((px + 8, py + 34, pw - 16, 44, {"kind": "tap", "fn": lambda: e.trigger(self.sel)}))
        by = py + ph - 74
        rows = 3 if by - (py + 98) >= 3 * 68 else 2
        for k, (label, target, usual, fmt, centred) in enumerate(PLAY_KNOBS[:rows * 4]):
            cx = px + pw * (k % 4 + 0.5) / 4.0
            cy = py + 132 + (k // 4) * 68
            self.area(cx - pw / 8.0, cy - 34, pw / 4.0, 68)
            panel()
            self.tab_knob(cx, cy, pw / 4.0, label, target, usual, fmt, centred)
        # what the pads do when touched, and the way to the rest of the pad's settings
        self.area(px, py + 98 + rows * 68, pw, by - (py + 98 + rows * 68))
        panel()
        self.area(px, by, pw, 74)
        panel()
        bw = (pw - 16 - 8) / 3.0
        self.tab_button(px + 8, by, bw, 30, "MUTE", self.tab_mute, self.tab_mute_mode, colour=mix(PANEL, RED, 0.7))
        self.tab_button(px + 12 + bw, by, bw, 30, "REPEAT", e.repeat_on, self.tab_repeat, colour=ORANGE)
        self.tab_button(px + 16 + 2 * bw, by, bw, 30, RATES[e.repeat_div][0], False, self.tab_rate)
        bw = (pw - 16 - 4) / 2.0
        self.tab_button(px + 8, by + 36, bw, 30, "LOAD SOUND", False, self.open_browser)
        self.tab_button(px + 12 + bw, by + 36, bw, 30, "MORE", False,
                        lambda: setattr(self, "page", self.PAGE_NAMES.index("SOUND")))

    def tab_knob(self, cx, cy, w, label, target, usual, fmt, centred):
        """A knob: dragged up to turn it up, tapped twice to put it back to its usual value."""
        where, attr, lo, hi, _ = self.tab_target(target)
        v = getattr(self.eng if where == "eng" else self.eng.pads[self.sel], attr)
        frac = round((v - lo) / float(hi - lo), 3)
        held = any(g.get("target") == target for g in self.grabs.values())
        self.text(label, cx, cy - 27, "t", TEXT if held else DIM, "c")
        zero = round((usual - lo) / float(hi - lo), 3) if centred else 0.0     # where the coloured arc starts
        self.custom(self._tab_knob, cx, cy, 16, frac, zero, held)
        self.text(fmt(v), cx, cy + 27, "s", TEXT, "c")
        self.hot.append((cx - w / 2.0, cy - 34, w, 68, {"kind": "knob", "target": target, "usual": usual}))

    def _tab_knob(self, cx, cy, r, frac, start, held):
        px, py = self.ox + cx * self.u, self.oy + cy * self.u
        R = r * self.u
        angle = lambda f: math.radians(135 + 270 * f)       # noqa: E731   from lower left, over the top, to lower right
        arc = lambda a, b, rad: [(px + rad * math.cos(angle(a + (b - a) * k / 24.0)),       # noqa: E731
                                  py + rad * math.sin(angle(a + (b - a) * k / 24.0))) for k in range(25)]
        pygame.draw.circle(self.screen, mix(PANEL, LINE, 0.55 if held else 0.3), (px, py), R)
        pygame.draw.lines(self.screen, mix(PANEL, LINE, 0.8), False, arc(0, 1, R + 3 * self.u), max(2, self.S(1.5)))
        if abs(frac - start) > 0.004:
            pygame.draw.lines(self.screen, ORANGE if held else TEAL, False, arc(start, frac, R + 3 * self.u), max(3, self.S(2.5)))
        a = angle(frac)
        pygame.draw.line(self.screen, TEXT, (px + R * 0.3 * math.cos(a), py + R * 0.3 * math.sin(a)),
                         (px + R * 0.92 * math.cos(a), py + R * 0.92 * math.sin(a)), max(2, self.S(1.6)))

    # ------------------------------------------------------------ what the controls do
    def tab_pattern(self, i):
        e = self.eng
        if e.playing and e.song_on and e.song:
            self.say("SONG MODE IS ON: PATTERNS FOLLOW THE CHAIN", 2.5)
        else:
            e.select_pattern(i)

    def tab_rec(self):
        e = self.eng
        e.rec = not e.rec
        if e.rec and not e.playing:
            self.say("RECORD IS ON: PRESS PLAY, THEN PLAY THE PADS", 2.5)

    def tab_bank(self, n):
        self.flush_bake()
        self.sel = n * BANK + self.sel % BANK

    # The buttons below light up to show what they have done, so they say nothing.
    def tab_mute_mode(self):
        self.tab_mute = not self.tab_mute

    def tab_repeat(self):
        e = self.eng
        e.repeat_on = not e.repeat_on

    def tab_rate(self):
        e = self.eng
        e.repeat_div = (e.repeat_div + 1) % len(RATES)

    def tab_set(self, target, v):
        """Give a knob's setting a new value."""
        where, attr, lo, hi, bake = self.tab_target(target)
        v = min(hi, max(lo, v))
        v = int(round(v)) if isinstance(lo, int) else round(v, 3)
        if where == "eng":
            setattr(self.eng, attr, float(v))
            return
        p = self.eng.pads[self.sel]
        if attr == "stretch":
            v = float(round(v))
            if v != p.stretch and p.region_seconds() > self.STRETCH_LIMIT:
                return self.say("TOO LONG TO STRETCH (%d s MAX) - TRIM OR CHOP IT" % self.STRETCH_LIMIT, 3.5)
        if getattr(p, attr) == v:
            return
        setattr(p, attr, v)
        if attr == "start":
            p.start = min(p.start, max(0.0, p.end - 0.02))
        elif attr == "end":
            p.end = max(p.end, min(1.0, p.start + 0.02))
        if bake and p.raw is not None:
            # Re-render at most twenty times a second while the knob moves, and once more when it stops.
            if self.pending_bake and self.pending_bake[0] is not p:
                self.flush_bake()
            now = time.time()
            if now - self.tab_baked > 0.05:
                self.tab_baked = now
                self.rebake(p)
            else:
                self.pending_bake = (p, now + 0.05)

    # ------------------------------------------------------------ touch
    def tablet_down(self, finger, px, py):
        lx, ly = (px - self.ox) / self.u, (py - self.oy) / self.u
        self.tablet_up(finger)                      # a finger we never saw lift
        name = None
        if self.modal():                            # a dialog or a list: the handheld's, moved to the middle
            name = self.touch_page(finger, lx, ly - self.dlg_dy)
        else:
            for x, y, w, h, c in reversed(self.hot):
                if x <= lx < x + w and y <= ly < y + h:
                    return self.tab_touch(finger, c, lx, ly)
            if ly >= TOP and not hasattr(self, "tab_" + self.PAGE_NAMES[self.page].lower()):
                name = self.touch_page(finger, lx, ly - (TOP - 29))
        if name:
            self.grabs[finger] = {"kind": "button", "name": name}

    def tab_touch(self, finger, c, lx, ly):
        e = self.eng
        kind = c["kind"]
        if kind == "tap":
            c["fn"]()
        elif kind == "pad":
            i = c["pad"]
            self.flush_bake()
            self.sel = i
            if self.tab_mute:
                e.pads[i].mute = not e.pads[i].mute
            else:
                name = "touch%s" % finger           # a button of its own, so letting go ends a held or repeating pad
                self.hit(i, name)
                self.grabs[finger] = {"kind": "button", "name": name}
        elif kind == "knob":
            now = time.time()
            if self.tab_last[0] == c["target"] and now - self.tab_last[1] < DOUBLE_TAP:
                self.tab_last = (None, 0.0)
                self.tab_set(c["target"], c["usual"])
                return
            self.tab_last = (c["target"], now)
            where, attr, lo, hi, _ = self.tab_target(c["target"])
            v = getattr(e if where == "eng" else e.pads[self.sel], attr)
            self.grabs[finger] = {"kind": "knob", "target": c["target"], "y0": ly, "v0": v, "span": hi - lo}
        elif kind == "tempo":
            self.grabs[finger] = {"kind": "tempo", "y0": ly, "v0": e.bpm, "moved": False}
        elif kind in ("grid", "tracks"):
            self.beat_touch(finger, kind, lx, ly)
        elif kind == "hold":                        # an effect that lasts as long as the finger is down
            e.perf[c["fx"]] = True
            self.grabs[finger] = {"kind": "hold", "fx": c["fx"]}
        elif kind == "xy":                          # a square that stands in for a stick
            self.grabs[finger] = dict(c)
            self.tablet_move(finger, self.ox + lx * self.u, self.oy + ly * self.u)
        elif kind == "trim":                        # the waveform: drag the nearer of its two markers
            p = e.pads[self.sel]
            f = (lx - c["x"]) / c["w"]
            self.grabs[finger] = {"kind": "trim", "x": c["x"], "w": c["w"], "x0": lx, "moved": False,
                                  "which": "START" if abs(f - p.start) <= abs(f - p.end) else "END"}

    # ------------------------------------------------------------ BEAT
    def beat_geom(self):
        """Where the step grid is: its left, its top, a step's width and a row's height."""
        gy = TOP + 18
        return 112.0, gy, (640 - 112 - 6) / 16.0, (self.LH - gy - 48) / 16.0

    def tab_beat(self):
        e = self.eng
        pat = e.patterns[e.cur_pat]
        length = pat["length"]
        self.cur_step = min(self.cur_step, length - 1)
        if e.playing and e.step >= 0 and time.time() >= self.follow_after and e.step // 16 != self.cur_step // 16:
            self.cur_step = min(length - 1, (e.step // 16) * 16 + self.cur_step % 16)    # follow the beat
        view = (self.cur_step // 16) * 16
        gx, gy, cw, rh = self.beat_geom()
        first = self.base()
        any_solo = any(p.solo for p in e.pads)
        self.area(0, TOP, 640, self.LH - TOP - 46)
        self.text(BEAT_HELP[self.beat_mode], 8, TOP + 9, "t", DIM)
        for s in range(0, 16, 4):
            if view + s < length:
                self.text(str(view + s + 1), gx + s * cw + 3, TOP + 9, "t", DIM)
        self.text("%d steps" % length, 634, TOP + 9, "t", DIM, "r")
        if e.playing and view <= e.step < view + 16:
            self.box(gx + (e.step - view) * cw, gy - 1, cw - 2, rh * 16 + 1, mix(BG, TEAL, 0.22), None, 2)
        for i, p in enumerate(self.bank_pads(), first):
            y = gy + (i - first) * rh
            silent = p.mute or (any_solo and not p.solo) or p.buf is None
            on = i == self.sel
            if on:
                self.box(6, y, 102, rh - 2, PANEL, ORANGE, 3)
            self.text("%02d %s" % (i + 1, p.name[:9]), 11, y + rh / 2 - 1, "s", ORANGE if on else (DIM if silent else TEXT))
            if p.solo or p.mute:
                self.text("S" if p.solo else "M", 104, y + rh / 2 - 1, "s", TEAL if p.solo else RED, "r")
            for s in range(16):
                st = view + s
                if st >= length:
                    continue
                x = gx + s * cw
                v = pat["steps"][i][st]
                if v:
                    col = ORANGE if v == 2 else TEXT
                    self.box(x + 1, y + 1, cw - 4, rh - 4, mix(col, BG, 0.6) if silent else col, None, 3)
                    note = pat["notes"][i][st]
                    if note:
                        self.text("%+d" % note, x + cw / 2 - 1, y + rh / 2 - 1, "s", DARK, "c")
                else:
                    self.box(x + 1, y + 1, cw - 4, rh - 4, PANEL if (st // 4) % 2 == 0 else mix(PANEL, BG, 0.5), None, 3)
        self.hot.append((0, gy, 110, rh * 16, {"kind": "tracks"}))
        self.hot.append((110, gy, 530, rh * 16, {"kind": "grid"}))
        # along the bottom: bank, what a touch does, the chosen track, which bar is shown, the pattern's length
        y = self.LH - 42
        self.area(0, self.LH - 46, 640, 46)
        for n in range(NBANKS):
            self.tab_button(6 + n * 26, y, 24, 34, str(n + 1), n == self.sel // BANK, lambda n=n: self.tab_bank(n), colour=ORANGE)
        for k, name in enumerate(BEAT_MODES):
            self.tab_button(116 + k * 54, y, 52, 34, name, self.beat_mode == k, lambda k=k: setattr(self, "beat_mode", k), "s")
        p = e.pads[self.sel]
        self.tab_button(284, y, 48, 34, "MUTE", p.mute, lambda: setattr(p, "mute", not p.mute), "s", mix(PANEL, RED, 0.7))
        self.tab_button(336, y, 48, 34, "SOLO", p.solo, lambda: setattr(p, "solo", not p.solo), "s")
        for b in range((length + 15) // 16):
            self.tab_button(390 + b * 23, y, 21, 34, str(b + 1), b == view // 16, lambda b=b: self.tab_bar(b), colour=ORANGE)
        self.tab_button(578, y, 26, 34, "-", False, lambda: self.change_length(-1), "l")
        self.tab_button(608, y, 26, 34, "+", False, lambda: self.change_length(1), "l")

    def tab_bar(self, b):
        """Show another bar of the pattern."""
        length = self.eng.patterns[self.eng.cur_pat]["length"]
        self.cur_step = min(length - 1, b * 16 + self.cur_step % 16)
        self.follow_after = time.time() + self.FOLLOW_HOLD

    def beat_step(self, lx):
        """The step under a point of the grid, or None when there is none there."""
        gx, _, cw, _ = self.beat_geom()
        s = int((lx - gx) // cw)
        st = (self.cur_step // 16) * 16 + s
        return st if lx >= gx and 0 <= s < 16 and st < self.eng.patterns[self.eng.cur_pat]["length"] else None

    def beat_put(self, pad, st, val):
        pat = self.eng.patterns[self.eng.cur_pat]
        row = pat["steps"][pad]
        if val and row[st]:
            return                                  # an accent already there is left alone
        row[st] = val
        if not val:
            pat["notes"][pad][st] = 0
        self.eng.refresh_variants(pad)

    def beat_touch(self, finger, kind, lx, ly):
        e = self.eng
        _, gy, _, rh = self.beat_geom()
        i = self.base() + min(BANK - 1, max(0, int((ly - gy) // rh)))
        self.flush_bake()
        self.sel = i
        self.follow_after = time.time() + self.FOLLOW_HOLD
        st = self.beat_step(lx)
        if kind == "tracks" or st is None:
            if kind == "tracks" and not e.playing:
                e.trigger(i)                        # the track's name: hear it
            return
        self.cur_step = st
        pat = e.patterns[e.cur_pat]
        row = pat["steps"][i]
        mode = BEAT_MODES[self.beat_mode]
        if mode == "DRAW":
            val = 0 if row[st] else 1
            self.beat_put(i, st, val)
            if val and not e.playing:
                e.trigger(i)
            self.grabs[finger] = {"kind": "paint", "pad": i, "val": val, "last": st}
        elif mode == "ACCENT":
            row[st] = 1 if row[st] == 2 else 2
            e.refresh_variants(i)
        elif e.pads[i].raw is None:
            self.say("THIS PAD HAS NO SOUND")
        elif e.pads[i].seconds() > VARIANT_MAX_SECONDS:
            self.say("THIS SOUND IS TOO LONG FOR STEP NOTES (%d s MAX)" % VARIANT_MAX_SECONDS, 3)
        else:
            if not row[st]:
                row[st] = 1
            self.grabs[finger] = {"kind": "note", "pad": i, "step": st, "pat": e.cur_pat, "y0": ly, "n0": pat["notes"][i][st]}

    # ------------------------------------------------------------ SONG
    def tab_song(self):
        e = self.eng
        song = e.song
        slots = min(len(song) + 1, SONG_SLOTS)
        self.song_cur = cur = min(self.song_cur, slots - 1)
        adding = cur >= len(song)
        pat = e.patterns[e.cur_pat]
        name = PAT_NAMES[e.cur_pat]
        self.area(0, TOP, 640, self.LH - TOP)
        y = TOP + 6
        self.tab_button(6, y, 130, 32, "SONG MODE ON" if e.song_on else "SONG MODE OFF", e.song_on, self.tab_song_mode)
        secs = e.song_steps() * 60.0 / e.bpm / 4.0
        self.text("%d pattern%s in the chain, %d:%02d long" % (len(song), "" if len(song) == 1 else "s", secs // 60, secs % 60)
                  if song else "The chain is empty. Tap a pattern below to add it.", 148, y + 16, "m", TEXT if song else DIM)
        for i in range(slots):                      # the chain: sixteen to a line
            r, c = divmod(i, 16)
            x, ty = 6 + c * 39.3, TOP + 46 + r * 38
            if i < len(song):
                playing = e.playing and e.song_on and e.song_slot == i
                self.box(x, ty, 36, 34, TEAL if playing else PANEL, LINE, 5)
                self.text(PAT_NAMES[song[i]], x + 18, ty + 14, "l", DARK if playing else TEXT, "c")
                self.text(str(i + 1), x + 18, ty + 28, "t", DARK if playing else DIM, "c")
            else:
                self.box(x, ty, 36, 34, None, LINE, 5)
                self.text("+", x + 18, ty + 16, "l", DIM, "c")
            if i == cur:
                self.box(x - 1.5, ty - 1.5, 39, 37, None, ORANGE, 6, 2)
            self.hot.append((x - 1.6, ty - 2, 39.3, 38, {"kind": "tap", "fn": lambda i=i: setattr(self, "song_cur", i)}))
        y = TOP + 46 + ((SONG_SLOTS + 15) // 16) * 38 + 6
        self.text("ADD TO THE END" if adding else "SLOT %d PLAYS" % (cur + 1), 6, y + 17, "s", DIM)
        for k in range(NPAT):
            self.tab_button(112 + k * 40, y, 38, 34, PAT_NAMES[k], not adding and song[cur] == k,
                            lambda k=k: self.tab_song_set(k), colour=ORANGE)
        if not adding:
            self.tab_button(444, y, 92, 34, "REPEAT IT", False, lambda: self.tab_song_edit("repeat"))
            self.tab_button(542, y, 92, 34, "REMOVE IT", False, lambda: self.tab_song_edit("remove"))
        y += 52                                     # the pattern chosen in the bar along the top
        self.text("PATTERN %s" % name, 6, y + 17, "m", ORANGE)
        bars, odd = divmod(pat["length"], 16)
        self.text("%d steps" % pat["length"] + ("" if odd else "  (%d bar%s)" % (bars, "" if bars == 1 else "s")), 112, y + 17, "m", TEXT)
        for k, (label, step, d) in enumerate((("- BAR", False, -1), ("+ BAR", False, 1), ("- STEP", True, -1), ("+ STEP", True, 1))):
            self.tab_button(262 + k * 62, y, 60, 34, label, False, lambda step=step, d=d: self.tab_length(step, d), "s")
        self.tab_button(542, y, 92, 34, "CLEAR IT", False, self.ask_clear_pattern)
        y += 44
        self.text("COPY %s TO" % name, 6, y + 17, "s", DIM)
        for k in range(NPAT):
            if k != e.cur_pat:
                self.tab_button(112 + k * 40, y, 38, 34, PAT_NAMES[k], False, lambda k=k: self.tab_copy(k))

    def tab_song_mode(self):
        e = self.eng
        if not e.song:
            self.say("THE CHAIN IS EMPTY - ADD PATTERNS FIRST", 3)
        else:
            e.song_on = not e.song_on
            e.song_pos = 0

    def tab_song_set(self, k):
        """Give the chosen slot a pattern, or add one to the end of the chain."""
        e = self.eng
        song = list(e.song)             # edited as a copy and swapped in whole (the audio thread reads it)
        if self.song_cur >= len(song):
            song.append(k)
            self.song_cur = min(len(song), SONG_SLOTS - 1)
            if len(song) == SONG_SLOTS:
                self.say("THE CHAIN IS FULL (%d)" % SONG_SLOTS)
        else:
            song[self.song_cur] = k
        e.song = song

    def tab_song_edit(self, what):
        e = self.eng
        song = list(e.song)
        if self.song_cur >= len(song):
            return
        if what == "remove":
            del song[self.song_cur]
            if not song:
                e.song_on = False
        elif len(song) >= SONG_SLOTS:
            return self.say("THE CHAIN IS FULL (%d)" % SONG_SLOTS)
        else:
            song.insert(self.song_cur, song[self.song_cur])
        e.song = song

    def tab_length(self, step, d):
        if step:
            pat = self.eng.patterns[self.eng.cur_pat]
            pat["length"] = int(min(MAX_STEPS, max(MIN_STEPS, pat["length"] + d)))
        else:
            self.change_length(d)

    def tab_copy(self, k):
        """Copy the current pattern over another: the handheld's COPY PATTERN row does the asking."""
        self.copy_to = k
        self.song_row = self.SONG_ROWS.index("COPY PATTERN")
        self.press_song("A", False)

    def tablet_move(self, finger, px, py):
        g = self.grabs.get(finger)
        if not g:
            return
        lx, ly = (px - self.ox) / self.u, (py - self.oy) / self.u
        if g["kind"] == "knob":
            self.tab_set(g["target"], g["v0"] + (g["y0"] - ly) / KNOB_TRAVEL * g["span"])
        elif g["kind"] == "xy":
            self.sticks[g["ax"]] = min(1.0, max(-1.0, (lx - g["cx"]) / g["half"]))
            self.sticks[g["ay"]] = min(1.0, max(-1.0, (ly - g["cy"]) / g["half"]))
        elif g["kind"] == "trim":
            if abs(lx - g["x0"]) > 3:
                g["moved"] = True
            if g["moved"]:
                self.tab_set(g["which"], (lx - g["x"]) / g["w"])
        elif g["kind"] == "paint":                  # a finger drawn along a row fills it, or wipes it
            st = self.beat_step(lx)
            if st is not None:                      # a quick finger skips steps: fill in the ones it passed over
                for s in range(min(st, g["last"]), max(st, g["last"]) + 1):
                    self.beat_put(g["pad"], s, g["val"])
                g["last"] = st
        elif g["kind"] == "note" and g["pat"] == self.eng.cur_pat:
            note = g["n0"] + int((g["y0"] - ly) / 7.0)
            if note != self.eng.patterns[g["pat"]]["notes"][g["pad"]][g["step"]]:
                self.eng.set_note(g["pat"], g["pad"], g["step"], note)
        elif g["kind"] == "tempo":
            if abs(g["y0"] - ly) > 4:
                g["moved"] = True
            if g["moved"]:
                self.eng.bpm = float(min(300, max(40, round(g["v0"] + (g["y0"] - ly) / 2.5))))

    def tablet_up(self, finger):
        g = self.grabs.pop(finger, None)
        if not g:
            return
        if g["kind"] == "button":
            self.button(g["name"], False)
        elif g["kind"] == "knob":
            self.flush_bake()
        elif g["kind"] == "hold":
            self.eng.perf[g["fx"]] = False
        elif g["kind"] == "xy":                     # let go and it snaps back, as a stick does
            self.sticks[g["ax"]] = self.sticks[g["ay"]] = 0.0
        elif g["kind"] == "trim":
            if g["moved"]:
                self.flush_bake()
            else:
                self.toggle_audition()              # a tap on the waveform plays it, or stops it
        elif g["kind"] == "note":
            e = self.eng
            if not e.playing:                       # hear the note that was chosen
                p = e.pads[g["pad"]]
                e.preview(g["pad"], p.variants.get(e.patterns[g["pat"]]["notes"][g["pad"]][g["step"]], p.buf))
        elif g["kind"] == "tempo" and not g["moved"]:
            # Tapped, not dragged: tap it in time and the tempo follows the taps.
            now = time.time()
            self.tab_taps = [t for t in self.tab_taps if now - t < 2.0][-3:] + [now]
            if len(self.tab_taps) >= 2:
                gap = (self.tab_taps[-1] - self.tab_taps[0]) / (len(self.tab_taps) - 1)
                self.eng.bpm = float(min(300, max(40, round(60.0 / gap))))

    # ------------------------------------------------------------ shared pieces
    def tab_knob_at(self, cx, cy, w, target):
        """A knob in an area of its own, so turning it redraws nothing else."""
        label, _, usual, fmt, centred = KNOBS[target]
        self.area(cx - w / 2.0, cy - 34, w, 68)
        self.tab_knob(cx, cy, w, label, target, usual, fmt, centred)

    def tab_row(self, name, button="A"):
        """Do what a button does on one of the handheld's rows (SOUND and PROJ): the same action, reached by touch."""
        page = self.PAGE_NAMES[self.page]
        if page == "SOUND":
            self.sound_row = self.SOUND_ROWS.index(name)
            self.press_sound(button, False)
        else:
            self.proj_row = self.PROJ_ROWS.index(name)
            self.press_proj(button, False)

    # ------------------------------------------------------------ SOUND
    def tab_sound(self):
        e = self.eng
        p = e.pads[self.sel]
        t = self.auditioning()
        self.area(0, TOP, 640, 126)
        self.text("PAD %d" % (self.sel + 1), 8, TOP + 20, "l", ORANGE)
        self.text(p.name[:22], 92, TOP + 21, "m", TEXT)
        self.text("%.2fs" % p.seconds(), 430, TOP + 21, "m", DIM, "r")
        self.tab_button(440, TOP + 4, 30, 30, "<", False, lambda: self.step_pad(-1), "l")
        self.tab_button(474, TOP + 4, 30, 30, ">", False, lambda: self.step_pad(1), "l")
        self.tab_button(510, TOP + 4, 58, 30, "STOP" if t is not None else "PLAY", t is not None, self.toggle_audition, "s", ORANGE)
        self.tab_button(572, TOP + 4, 62, 30, "LOAD", False, self.open_browser, "s")
        wx, wy, ww, wh = 6, TOP + 40, 628, 84
        self.wave(p, wx, wy, ww, wh, TEAL, trim=True)
        if t is not None:                           # where the sound has got to
            f = p.end - (p.end - p.start) * t if p.rev else p.start + (p.end - p.start) * t
            self.box(wx + 2 + f * (ww - 4) - 0.75, wy + 2, 1.5, wh - 4, TEXT, None, 0)
        self.hot.append((wx, wy, ww, wh, {"kind": "trim", "x": wx + 2, "w": ww - 4}))
        cell = 628 / 8.0
        rows = (("VOLUME", "PITCH", "FINE TUNE", "STRETCH", "FILTER", "BIT CRUSH", "LO-FI RATE", "CHOKE"),
                ("START", "END", "FADE IN", "FADE OUT", "DELAY", "REVERB"))
        for r, names in enumerate(rows):
            for k, target in enumerate(names):
                self.tab_knob_at(6 + cell * (k + 0.5), TOP + 160 + r * 68, cell, target)
        y = TOP + 211
        self.area(6 + cell * 6, y, cell * 2, 34)
        self.tab_button(6 + cell * 6 + 2, y, cell - 4, 34, MODES[p.mode], p.mode != 0, lambda: self.tab_row("MODE", "right"), "s")
        self.tab_button(6 + cell * 7 + 2, y, cell - 4, 34, "REVERSE", p.rev, lambda: self.tab_row("REVERSE", "right"), "s")
        # the tools: each has what it will do on a button that changes it, and a button that does it
        y = TOP + 270
        self.area(0, y - 4, 640, 84)
        self.tab_button(6, y, 150, 34, "CHORD: %s" % CHORDS[p.chord][0], p.chord != 0, lambda: self.tab_row("CHORD", "right"), "s")
        self.tab_button(162, y, 188, 34, "CHOP: %s" % self.CHOPS[self.chop_mode][0], False, lambda: self.tab_row("CHOP", "right"), "s")
        self.tab_button(356, y, 80, 34, "CHOP IT", False, lambda: self.tab_row("CHOP"), "s")
        self.tab_button(442, y, 192, 34, "SPREAD OVER THE PADS", False, lambda: self.tab_row("SPREAD"), "s")
        y += 40
        self.tab_button(6, y, 150, 34, "FIT TO: %s" % FITS[self.fit_idx][0], False, lambda: self.tab_row("FIT TO TEMPO", "right"), "s")
        self.tab_button(162, y, 188, 34, "KEEP PITCH (stretch)" if self.fit_keep else "CHANGE PITCH (classic)", False,
                        lambda: self.tab_row("FIT STYLE", "right"), "s")
        self.tab_button(356, y, 80, 34, "FIT IT", False, lambda: self.tab_row("FIT TO TEMPO"), "s")
        self.tab_button(442, y, 110, 34, self.KEYS[self.key_idx][0], False, lambda: self.tab_row("KEY CHORDS", "right"), "s")
        self.tab_button(558, y, 76, 34, "CHORDS", False, lambda: self.tab_row("KEY CHORDS"), "s")

    # ------------------------------------------------------------ FX
    def tab_fx(self):
        e = self.eng
        top = TOP + 4
        self.area(0, top, 300, 16)
        self.text("THE WHOLE MIX", 8, top + 9, "t", DIM)
        cell = 294 / 3.0
        for k, target in enumerate(("COMPRESSOR", "MASTER", "SWING", "DELAY FEEDBACK", "REVERB SIZE")):
            self.tab_knob_at(6 + cell * (k % 3 + 0.5), top + 52 + (k // 3) * 68, cell, target)
        y = top + 164
        self.area(0, y, 300, 50)
        for k, (label, value, attr, count) in enumerate((("DELAY TIME", DELAY_TIMES[e.delay_div][0], "delay_div", len(DELAY_TIMES)),
                                                         ("STUTTER", STUTTERS[e.stutter_div][0], "stutter_div", len(STUTTERS)),
                                                         ("NOTE REPEAT", RATES[e.repeat_div][0], "repeat_div", len(RATES)))):
            x = 6 + cell * k
            self.text(label, x + cell / 2, y + 6, "t", DIM, "c")
            self.tab_button(x + 4, y + 14, cell - 8, 32, value, False,
                            lambda attr=attr, count=count: setattr(e, attr, (getattr(e, attr) + 1) % count))
        # the right: effects played by hand. Hold a button; slide a finger about a square.
        self.area(306, top, 334, 96)
        for k, (fx, label) in enumerate((self.PERFORM[b] for b in "XYAB")):
            x, y = 310 + (k % 2) * 164, top + 4 + (k // 2) * 44
            on = e.perf[fx]
            self.box(x, y, 160, 40, ORANGE if on else PANEL, None if on else LINE, 6)
            self.text(label, x + 80, y + 20, "m", DARK if on else TEXT, "c")
            self.hot.append((x - 2, y - 2, 164, 44, {"kind": "hold", "fx": fx}))
        side = min(150.0, self.LH - (top + 104) - 74)
        lv = self.stick_level
        for cx, ax, ay in ((391, "lx", "ly"), (553, "rx", "ry")):
            x0, y0 = cx - side / 2, top + 104
            self.area(x0 - 4, y0 - 2, side + 8, side + 72)
            self.box(x0, y0, side, side, DARK, LINE, 8)
            self.box(x0 + 6, y0 + side / 2 - 0.5, side - 12, 1, LINE, None, 0)
            self.box(cx - 0.5, y0 + 6, 1, side - 12, LINE, None, 0)
            busy = False
            for axis, lo_at, hi_at in ((ax, (x0 + 8, y0 + side / 2 - 9, "l"), (x0 + side - 8, y0 + side / 2 - 9, "r")),
                                       (ay, (cx, y0 + side - 10, "c"), (cx, y0 + 10, "c"))):
                labels = self.STICK_FX[self.stick_fx[axis]]
                level = lv.get(self.stick_fx[axis], 0.0)
                busy = busy or bool(level)
                if len(labels) == 2:
                    self.text(labels[0], lo_at[0], lo_at[1], "s", ORANGE if level < 0 else DIM, lo_at[2])
                    self.text(labels[1], hi_at[0], hi_at[1], "s", ORANGE if level > 0 else DIM, hi_at[2])
                elif labels:
                    self.text(labels[0], hi_at[0], hi_at[1], "s", ORANGE if level else DIM, hi_at[2])
            half = side / 2 - 12
            self.box(cx + self.sticks[ax] * half - 9, y0 + side / 2 + self.sticks[ay] * half - 9, 18, 18,
                     ORANGE if busy else LINE, None, 9)
            self.hot.append((x0, y0, side, side, {"kind": "xy", "ax": ax, "ay": ay, "cx": cx, "cy": y0 + side / 2, "half": half}))
            for n, (axis, word) in enumerate(((ax, "ACROSS"), (ay, "UP, DOWN"))):
                self.tab_button(x0, y0 + side + 6 + n * 32, side, 28, "%s: %s" % (word, self.stick_fx[axis]), False,
                                lambda axis=axis: self.tab_stick_fx(axis), "s")

    def tab_stick_fx(self, axis):
        names = list(self.STICK_FX)
        self.stick_fx[axis] = names[(names.index(self.stick_fx[axis]) + 1) % len(names)]

    # ------------------------------------------------------------ PROJ
    def tab_tile(self, x, y, w, title, sub, fn):
        self.box(x, y, w, 44, PANEL, LINE, 6)
        self.text(title, x + 10, y + 14, "m", TEXT)
        self.text(sub[:44], x + 10, y + 32, "t", DIM)
        self.hot.append((x - 2, y - 3, w + 4, 50, {"kind": "tap", "fn": fn}))

    def tab_proj(self):
        e = self.eng
        top = TOP + 4
        free = self.free_slot()
        first = self.base()
        self.area(0, TOP, 424, self.LH - TOP)
        self.text("THIS PROJECT", 8, top + 9, "t", DIM)
        tiles = [("SAVE", self.slot_label(self.loaded)),
                 ("SAVE A COPY", "into P%02d, a new project" % free if free else "no empty slot left"),
                 ("NAME", e.name or "not named yet"), ("OPEN", "another project"),
                 ("NEW", "a kit: its sounds only, or with a full song")]
        for k, (name, sub) in enumerate(tiles):
            self.tab_tile(6, top + 18 + k * 50, 204, name, sub, lambda name=name: self.tab_row(name))
        self.text("SOUND IN AND OUT", 218, top + 9, "t", DIM)
        tiles = [("RECORD MIC", "chopped onto pads %d-%d" % (first + 1, first + BANK)),
                 ("RESAMPLE", "record what you hear onto pad %d" % (self.sel + 1)),
                 ("EXPORT AUDIO", "%s, as a WAV file" % ("the song" if (e.song_on and e.song) else "pattern %s x 4" % PAT_NAMES[e.cur_pat])),
                 ("BLUETOOTH MIDI", self.clip(self.ble.status, 40) if self.ble.running else "search for controllers")]
        for k, (name, sub) in enumerate(tiles):
            self.tab_tile(216, top + 18 + k * 50, 204, name, sub, lambda name=name: self.tab_row(name))
        self.tab_knob_at(478, top + 52, 100, "MASTER")
        self.tab_knob_at(582, top + 52, 100, "SWING")
        y = top + 96
        self.area(424, y, 216, self.LH - y)
        self.text("MIDI OUT", 530, y + 6, "t", DIM, "c")
        ch = self.midi.out_channel
        self.tab_button(430, y + 14, 44, 32, "-", False, lambda: setattr(self.midi, "out_channel", (ch - 1) % 17), "l")
        self.text("CHANNEL %d" % ch if ch else "OFF", 530, y + 30, "m", TEXT, "c")
        self.tab_button(586, y + 14, 44, 32, "+", False, lambda: setattr(self.midi, "out_channel", (ch + 1) % 17), "l")
        lines = ["controllers: %s" % ((self.clip(", ".join(self.midi_names()), 24) or "none") if self.midi.running else "not available"),
                 "audio: %s" % ("off" if e.dev is None else "ok"), "dropouts: %d" % e.underruns,
                 "peak load: %.1f ms" % e.max_render_ms]
        for n, s in enumerate(lines):
            self.text(s, 432, y + 66 + n * 16, "s", DIM)
        self.text("PocketPads", 632, self.LH - 12, "t", DIM, "r")
