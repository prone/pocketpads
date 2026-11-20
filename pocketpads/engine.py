"""PocketPads audio engine: sample pads, step sequencer, SDL audio output."""
import ctypes
import glob
import json
import os
import re
import struct
import threading
import time
from collections import deque

import numpy as np

SR = 44100
BLOCK = 512
NPADS = 16
NPAT = 4
MAX_STEPS = 32
FADE = 256
HEADROOM = 0.7
KNEE = 0.7
MAX_VOICES = 32
APP_DIR = os.path.dirname(os.path.abspath(__file__))
_RAMP = np.linspace(1.0, 0.0, FADE, dtype=np.float32)

# Musical lengths, in sequencer steps (one step is a sixteenth note).
RATES = [("1/4", 4.0), ("1/8", 2.0), ("1/8T", 4 / 3), ("1/16", 1.0), ("1/16T", 2 / 3), ("1/32", 0.5)]
DELAY_TIMES = [("1/16", 1.0), ("1/8", 2.0), ("1/8 DOT", 3.0), ("1/4", 4.0)]
STUTTERS = [("1/8", 2.0), ("1/16", 1.0), ("1/32", 0.5)]
CHOKE_GROUPS = 4
ONESHOT, HOLD, LOOP = 0, 1, 2
MODES = ["ONE-SHOT", "HOLD", "LOOP"]
FITS = [("1 BEAT", 1), ("2 BEATS", 2), ("1 BAR", 4), ("2 BARS", 8), ("4 BARS", 16)]
MAX_CAPTURE_BLOCKS = 60 * 44100 // 512      # resampling stops collecting after a minute
HSIZE = 1 << 17          # master history for stutter / tape stop (about 3 s)
TAPS = 127               # master filter length
_HAM = np.hamming(TAPS)
_ARANGE = {}


def _ar(n):
    a = _ARANGE.get(n)
    if a is None:
        a = _ARANGE[n] = np.arange(n)
    return a


def fft_filter(seg, amount):
    """One-knob filter for a pad: negative closes a low-pass, positive raises a high-pass."""
    if abs(amount) < 0.01:
        return seg
    n = len(seg)
    size = 1 << max(8, (n - 1).bit_length())
    f = np.fft.rfftfreq(size, 1.0 / SR)
    if amount < 0:
        fc = 18000.0 * (120.0 / 18000.0) ** (-amount)
        h = 1.0 / np.sqrt(1.0 + (f / fc) ** 8)
    else:
        fc = 30.0 * (6000.0 / 30.0) ** amount
        h = 1.0 / np.sqrt(1.0 + (fc / np.maximum(f, 1e-3)) ** 8)
    out = np.empty_like(seg)
    for c in range(seg.shape[1]):
        out[:, c] = np.fft.irfft(np.fft.rfft(seg[:, c], size) * h, size)[:n]
    return out


def _kernel(fc, highpass):
    m = np.arange(TAPS) - (TAPS - 1) / 2
    h = np.sinc(2.0 * fc / SR * m) * _HAM
    h /= h.sum()
    if highpass:
        h = -h
        h[(TAPS - 1) // 2] += 1.0
    return h.astype(np.float32)


class Delay:
    """Tempo-synced ping-pong delay on a shared send bus."""
    SIZE = 1 << 17

    def __init__(self):
        self.ring = np.zeros((self.SIZE, 2), np.float32)
        self.w = 0
        self.prev = np.zeros(2, np.float32)
        self.tail = 0

    def process(self, inp, n, d, fb):
        if inp is None and self.tail <= 0:
            return None
        mask = self.SIZE - 1
        ar = _ar(n)
        rd = self.ring[(self.w - d + ar) & mask]
        damp = np.empty_like(rd)
        damp[1:] = 0.65 * rd[1:] + 0.35 * rd[:-1]
        damp[0] = 0.65 * rd[0] + 0.35 * self.prev
        self.prev = rd[-1].copy()
        wr = damp[:, ::-1] * fb
        if inp is not None:
            wr[:, 0] += inp.sum(axis=1) * 0.6
            repeats = np.log(1e-3) / np.log(min(0.95, max(0.05, fb)))
            self.tail = int(min(6000, (repeats + 1) * d / n + 2))
        else:
            self.tail -= 1
            if self.tail <= 0:
                self.ring.fill(0)
                self.prev[:] = 0
        self.ring[(self.w + ar) & mask] = wr
        self.w = (self.w + n) & mask
        return rd


class Reverb:
    """Eight-line feedback delay network: a dense tail from a few vector operations per block."""
    LENS = np.array([1117, 1361, 1559, 1733, 1933, 2137, 2339, 2543])
    SIZE = 4096

    def __init__(self):
        self.ring = np.zeros((8, self.SIZE), np.float32)
        self.w = 0
        self.prev = np.zeros(8, np.float32)
        self.tail = 0
        self.signs = np.array([1, -1, 1, -1, -1, 1, -1, 1], np.float32)[:, None]
        self.rows = np.arange(8)[:, None]

    def process(self, inp, n, t60):
        if inp is None and self.tail <= 0:
            return None
        mask = self.SIZE - 1
        ar = _ar(n)
        rd = self.ring[self.rows, (self.w - self.LENS[:, None] + ar[None, :]) & mask]
        damp = np.empty_like(rd)
        damp[:, 1:] = 0.7 * rd[:, 1:] + 0.3 * rd[:, :-1]
        damp[:, 0] = 0.7 * rd[:, 0] + 0.3 * self.prev
        self.prev = rd[:, -1].copy()
        g = (10.0 ** (-3.0 * self.LENS / (SR * t60))).astype(np.float32)[:, None]
        wr = (damp - 0.25 * damp.sum(axis=0)) * g
        if inp is not None:
            wr += (inp.sum(axis=1) * 0.25)[None, :] * self.signs
            self.tail = int(t60 * SR / n) + 4
        else:
            self.tail -= 1
            if self.tail <= 0:
                self.ring.fill(0)
                self.prev[:] = 0
        self.ring[self.rows, (self.w + ar[None, :]) & mask] = wr
        self.w = (self.w + n) & mask
        out = np.empty((n, 2), np.float32)
        out[:, 0] = rd[0::2].sum(axis=0)
        out[:, 1] = rd[1::2].sum(axis=0)
        return out * 0.3


# ---------------------------------------------------------------- WAV files
def resample(a, ratio):
    """Linear resample of an (n, 2) float array; ratio > 1 shortens it."""
    n = len(a)
    n_out = max(2, int(n / ratio))
    x = np.arange(n_out, dtype=np.float64) * ratio
    src = np.arange(n, dtype=np.float64)
    out = np.empty((n_out, a.shape[1]), np.float32)
    for c in range(a.shape[1]):
        out[:, c] = np.interp(x, src, a[:, c])
    return out


def load_wav(path, max_seconds=180):
    """Read a PCM or float WAV into an (n, 2) float32 array at SR."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    pos, fmt, pcm = 12, None, None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if cid == b"data" and (size == 0 or pos + 8 + size > len(data)):
            body = data[pos + 8:]
        else:
            body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            pcm = body
        pos += 8 + size + (size & 1)
    if fmt is None or pcm is None or len(fmt) < 16:
        raise ValueError("WAV has no audio data")
    tag, ch, rate, _, _, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == 0xFFFE and len(fmt) >= 26:
        tag = struct.unpack("<H", fmt[24:26])[0]
    if ch < 1 or rate < 1:
        raise ValueError("bad WAV header")
    pcm = pcm[:rate * max_seconds * ch * (bits // 8)]
    if tag == 1 and bits == 8:
        a = (np.frombuffer(pcm, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif tag == 1 and bits == 16:
        a = np.frombuffer(pcm[:len(pcm) // 2 * 2], "<i2").astype(np.float32) / 32768.0
    elif tag == 1 and bits == 24:
        b = np.frombuffer(pcm[:len(pcm) // 3 * 3], np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        a = v.astype(np.float32) / 8388608.0
    elif tag == 1 and bits == 32:
        a = np.frombuffer(pcm[:len(pcm) // 4 * 4], "<i4").astype(np.float32) / 2147483648.0
    elif tag == 3 and bits == 32:
        a = np.frombuffer(pcm[:len(pcm) // 4 * 4], "<f4").astype(np.float32)
    elif tag == 3 and bits == 64:
        a = np.frombuffer(pcm[:len(pcm) // 8 * 8], "<f8").astype(np.float32)
    else:
        raise ValueError("unsupported WAV format (%d, %d-bit)" % (tag, bits))
    a = a[:len(a) // ch * ch].reshape(-1, ch)
    if ch == 1:
        a = np.repeat(a, 2, axis=1)
    elif ch > 2:
        a = a[:, :2]
    if len(a) < 2:
        raise ValueError("WAV is empty")
    if rate != SR:
        a = resample(a, rate / SR)
    return np.ascontiguousarray(a, np.float32)


def write_wav(path, audio):
    """Write 16-bit PCM; a 1-D array is mono, an (n, 2) array is stereo."""
    ch = 2 if getattr(audio, "ndim", 1) == 2 else 1
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, ch, SR, SR * 2 * ch, 2 * ch, 16))
        f.write(b"data" + struct.pack("<I", len(pcm)) + pcm)


def find_chops(raw, count=16):
    """Slice a recording at its hits. Returns (start, end) fractions, at most `count` of them.

    Finds moments where the level jumps, keeps the strongest ones, and runs each slice to the
    next hit. A recording with no clear hits is cut into equal pieces instead.
    """
    mono = np.abs(raw).max(axis=1) if raw.ndim == 2 else np.abs(raw)
    n = len(mono)
    hop = SR // 200                                   # 5 ms
    frames = n // hop
    if frames < 40:
        return [(0.0, 1.0)]
    env = mono[:frames * hop].reshape(frames, hop).max(axis=1)
    peak = float(env.max())
    if peak <= 0:
        return [(0.0, 1.0)]
    floor = max(peak * 0.02, float(np.percentile(env, 20)) * 2.0)
    loud = np.nonzero(env > floor)[0]
    first, last = (int(loud[0]), int(loud[-1]) + 1) if len(loud) else (0, frames)   # trim quiet ends
    before = np.array([env[max(0, i - 8):i].mean() if i else 0.0 for i in range(frames)])
    rise = env / (before + floor)
    gap = 16                                          # hits at least 80 ms apart
    hits = []
    for i in np.argsort(-rise):
        if rise[i] < 1.8 or env[i] < floor * 2:
            continue
        if first <= i < last and all(abs(i - h) >= gap for h in hits):
            hits.append(int(i))
        if len(hits) >= count:
            break
    hits.sort()
    if len(hits) < 3:
        count = max(1, min(count, (last - first) // 50))   # no slice shorter than a quarter second
        edges = np.linspace(first, last, count + 1)
        return [(a * hop / n, z * hop / n) for a, z in zip(edges, edges[1:])]
    starts = [max(first, h - 1) for h in hits]        # 5 ms of lead-in keeps the attack
    ends = starts[1:] + [last]
    return [(a * hop / n, min(1.0, z * hop / n)) for a, z in zip(starts, ends)]


_cache = {}


def load_cached(path):
    """Load a WAV once and share it between pads (slices of one long sample cost no extra memory)."""
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_mtime, st.st_size)
    hit = _cache.get(key)
    if hit is None:
        raw = load_wav(path)
        mono = np.abs(raw).max(axis=1)
        cols = 512
        pad_to = -(-len(mono) // cols) * cols
        peaks = np.pad(mono, (0, pad_to - len(mono))).reshape(cols, -1).max(axis=1)
        hit = _cache[key] = (raw, peaks)
    return hit


def prune_cache(pads):
    """Forget samples no pad uses any more."""
    used = {id(p.raw) for p in pads if p.raw is not None}
    for k in [k for k, v in _cache.items() if id(v[0]) not in used]:
        del _cache[k]


# ---------------------------------------------------------------- model
class Pad:
    def __init__(self, index):
        self.index = index
        self.clear()

    def clear(self):
        self.name, self.path = "EMPTY", ""
        self.raw = self.buf = self.peaks = None
        self.vol, self.pitch, self.start, self.end = 0.8, 0, 0.0, 1.0
        self.filt, self.dsend, self.rsend, self.choke = 0, 0.0, 0.0, 0
        self.fine, self.mode, self.rev, self.fin, self.fout = 0, 0, False, 0, 0
        self.mute = self.solo = False

    def copy_settings(self, other):
        for k in ("vol", "pitch", "fine", "start", "end", "filt", "dsend", "rsend", "choke", "mode", "rev", "fin", "fout"):
            setattr(self, k, getattr(other, k))

    def region_seconds(self):
        """Length of the trimmed sample at its original pitch."""
        return 0.0 if self.raw is None else (self.end - self.start) * len(self.raw) / SR

    def load(self, path):
        self.raw, self.peaks = load_cached(path)
        self.path = path
        name = os.path.splitext(os.path.basename(path))[0].upper()
        self.name = re.sub(r"^\d+[ _-]+", "", name) or name
        self.bake()

    def bake(self):
        """Apply trim and pitch to the raw sample, producing the playable buffer."""
        if self.raw is None:
            self.buf = None
            return
        n = len(self.raw)
        a = min(int(self.start * n), n - 16)
        b = max(a + 16, int(self.end * n))
        seg = self.raw[a:b]
        if self.rev:
            seg = seg[::-1]
        if self.pitch or self.fine:
            seg = resample(seg, 2.0 ** ((self.pitch + self.fine / 100.0) / 12.0))
        seg = fft_filter(seg, self.filt / 100.0) if self.filt else seg.copy()
        fi = min(max(44, int(self.fin * SR / 1000)), len(seg) // 2)
        fo = min(max(220, int(self.fout * SR / 1000)), len(seg) // 2)
        seg[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)[:, None]
        seg[-fo:] *= np.linspace(1, 0, fo, dtype=np.float32)[:, None]
        self.buf = seg

    def seconds(self):
        return 0.0 if self.buf is None else len(self.buf) / SR


def new_pattern():
    return {"length": 16, "steps": [[0] * MAX_STEPS for _ in range(NPADS)]}


def _rel(path):
    try:
        r = os.path.relpath(path, APP_DIR)
        return path if r.startswith("..") else r
    except ValueError:
        return path


# ---------------------------------------------------------------- engine
class Engine:
    def __init__(self):
        self.pads = [Pad(i) for i in range(NPADS)]
        self.patterns = [new_pattern() for _ in range(NPAT)]
        self.cur_pat, self.pending_pat = 0, None
        self.bpm, self.swing, self.master = 120.0, 50.0, 0.8
        self.delay_div, self.delay_fb, self.reverb_size = 1, 0.45, 0.4
        self.repeat_on, self.repeat_div, self.stutter_div = False, 3, 1
        self.delay, self.reverb = Delay(), Reverb()
        self.holds = {}
        self.cap = None
        self.perf = {"lp": False, "hp": False, "stutter": False, "tape": False}
        self.hist = np.zeros((HSIZE, 2), np.float32)
        self.hw = 0
        self._st_loop, self._st_pos = None, 0
        self._tape_pos, self._tape_rate, self._tape_was = None, 1.0, False
        self._fir_tail = np.zeros((TAPS - 1, 2), np.float32)
        self._fir_p, self._fir_hp, self._fir_on = 0.0, False, False
        self._bar_clock = 0.0
        self.playing = self.rec = False
        self.step = -1
        self.voices = []
        self.cmds = deque()
        self.pad_flash = [0.0] * NPADS
        self.peak = 0.0
        self._clock = 0
        self._base = self._next_time = self._step_clock = 0.0
        self._next_step = 0
        self._skip = set()
        self.running = False
        self.sdl = self.dev = None
        self.queue_blocks = 3
        self.underruns = 0
        self.max_render_ms = 0.0
        self.audio_error = ""

    # ----- control (called from the UI thread)
    def trigger(self, pad, vel=1.0):
        self.cmds.append(("trig", pad, vel))
        self.pad_flash[pad] = time.time()
        if self.playing and self.rec:
            sf = self.step_frames()
            latency = (self.queue_blocks + 1) * BLOCK
            k = int(round((self._clock - latency - self._step_clock) / sf))
            pat = self.patterns[self.cur_pat]
            target = (self.step + k) % pat["length"]
            if not pat["steps"][pad][target]:
                pat["steps"][pad][target] = 1
            if k >= 1:
                self._skip.add((pad, target))

    def note_off(self, pad):
        """Fade out whatever this pad is playing (pad released in HOLD mode, or a preview stopped)."""
        self.cmds.append(("off", pad))

    def preview(self, pad, buf):
        """Play an arbitrary buffer through a pad's channel, e.g. a whole file while chopping live."""
        self.cmds.append(("raw", pad, buf))

    def capture_start(self):
        self.cap = []

    def capture_stop(self):
        """Stop resampling; returns everything played since capture_start as an (n, 2) array."""
        blocks, self.cap = self.cap, None
        return np.concatenate(blocks) if blocks else np.zeros((0, 2), np.float32)

    def hold(self, pad):
        """Note repeat: keep retriggering this pad at the repeat rate until release()."""
        self.cmds.append(("hold", pad))
        self.pad_flash[pad] = time.time()

    def release(self, pad):
        self.cmds.append(("rel", pad))

    def play(self):
        self.cmds.append(("play",))

    def stop(self):
        self.cmds.append(("stop",))

    def toggle_play(self):
        self.stop() if self.playing else self.play()

    def select_pattern(self, index):
        if self.playing:
            self.pending_pat = None if index == self.cur_pat else index
        else:
            self.cur_pat, self.pending_pat = index, None

    def step_frames(self):
        return SR * 60.0 / self.bpm / 4.0

    # ----- rendering (audio thread)
    def _start_voice(self, pad, vel, off, gate=None, buf=None):
        p = self.pads[pad]
        if buf is None:
            buf = p.buf
        if buf is None:
            return
        group = p.choke
        was_looping = False
        for v in self.voices:
            if not v[5] and (v[3] == pad or (group and self.pads[v[3]].choke == group)):
                was_looping = was_looping or (v[3] == pad and v[9])
                v[5], v[6] = True, off
        if p.mode == LOOP and was_looping and buf is p.buf:
            return                      # a looping pad is a switch: the second hit turns it off
        if p.mode != HOLD:
            gate = None
        # buf, pos, gain, pad, offset, fading, fade offset, delay send, reverb send, loop, gate frames
        self.voices.append([buf, 0, p.vol * vel, pad, off, False, 0, p.dsend, p.rsend,
                            p.mode == LOOP and buf is p.buf, gate])
        if len(self.voices) > MAX_VOICES:
            self.voices.pop(0)

    def render(self, n):
        out = np.zeros((n, 2), np.float32)
        while self.cmds:
            c = self.cmds.popleft()
            if c[0] == "trig":
                self._start_voice(c[1], c[2], 0)
            elif c[0] == "off":
                for v in self.voices:
                    if v[3] == c[1] and not v[5]:
                        v[5], v[6] = True, 0
            elif c[0] == "raw":
                self._start_voice(c[1], 1.0, 0, buf=c[2])
            elif c[0] == "hold":
                iv = self.step_frames() * RATES[self.repeat_div][1]
                if self.playing:
                    k = np.ceil((self._clock - self._bar_clock) / iv)
                    self.holds[c[1]] = self._bar_clock + k * iv
                else:
                    self.holds[c[1]] = float(self._clock)
            elif c[0] == "rel":
                self.holds.pop(c[1], None)
            elif c[0] == "play":
                self.playing = True
                self._base = self._next_time = self._bar_clock = float(self._clock)
                self._next_step = 0
                self._skip.clear()
            elif c[0] == "stop":
                for v in self.voices:           # looping pads stop with the beat
                    if v[9] and not v[5]:
                        v[5], v[6] = True, 0
                self.playing = False
                self.step = -1
                self.pending_pat = None
        end = self._clock + n
        if self.holds:
            sf = self.step_frames()
            iv = sf * RATES[self.repeat_div][1]
            for pad in list(self.holds):
                t = self.holds[pad]
                while t < end:
                    self._start_voice(pad, 1.0, max(0, int(t - self._clock)))
                    self.pad_flash[pad] = time.time()
                    if self.playing and self.rec:
                        pat = self.patterns[self.cur_pat]
                        k = int(round((t - self._bar_clock) / sf))
                        st = k % pat["length"]
                        if not pat["steps"][pad][st]:
                            pat["steps"][pad][st] = 1
                        if self._bar_clock + k * sf >= self._base - 2:
                            self._skip.add((pad, st))
                    t += iv
                self.holds[pad] = t
        if self.playing:
            while self._next_time < end:
                off = max(0, int(self._next_time - self._clock))
                st = self._next_step
                if st == 0 and self.pending_pat is not None:
                    self.cur_pat, self.pending_pat = self.pending_pat, None
                pat = self.patterns[self.cur_pat]
                length = pat["length"]
                if st >= length:
                    st = 0
                any_solo = any(p.solo for p in self.pads)
                now = time.time()
                for i in range(NPADS):
                    v = pat["steps"][i][st]
                    if not v:
                        continue
                    if (i, st) in self._skip:
                        self._skip.discard((i, st))
                        continue
                    p = self.pads[i]
                    if p.mute or (any_solo and not p.solo):
                        continue
                    self._start_voice(i, 1.0 if v == 1 else 1.3, off, gate=self.step_frames())
                    self.pad_flash[i] = now
                self.step = st
                self._step_clock = self._next_time
                if st == 0:
                    self._bar_clock = self._base
                sf = self.step_frames()
                self._base += sf
                nxt = (st + 1) % length
                self._next_step = nxt
                # MPC-style swing: 50% is straight, 66% is a triplet feel, 75% is the maximum.
                self._next_time = self._base + ((self.swing - 50.0) / 50.0 * sf if nxt % 2 else 0.0)
        dbus = rbus = None
        alive = []
        queue = list(self.voices)
        for v in queue:
            buf, pos, gain, _, off, fade, foff, ds, rs, loop, gate = v
            take = min(n - off, len(buf) - pos)
            if take <= 0:
                continue
            if gate is not None and not fade:
                if gate < n - off:              # a HOLD pad played by the sequencer ends after its step
                    fade, foff = True, off + int(gate)
                else:
                    v[10] = gate - (n - off)
            if fade:
                pre = max(0, min(take, foff - off))
                m = min(take - pre, FADE)
                chunk = buf[pos:pos + pre + max(m, 0)] * gain
                if m > 0:
                    chunk[pre:] *= _RAMP[:m, None]
                take = len(chunk)
            else:
                chunk = buf[pos:pos + take] * gain
                v[1], v[4] = pos + take, 0
                if v[1] < len(buf):
                    alive.append(v)
                elif loop and off + take < n and len(buf) > 64:
                    queue.append([buf, 0, gain, v[3], off + take, False, 0, ds, rs, True, None])
                elif loop and len(buf) > 64:
                    v[1] = 0
                    alive.append(v)
            out[off:off + take] += chunk
            if ds:
                if dbus is None:
                    dbus = np.zeros((n, 2), np.float32)
                dbus[off:off + take] += chunk * ds
            if rs:
                if rbus is None:
                    rbus = np.zeros((n, 2), np.float32)
                rbus[off:off + take] += chunk * rs
        self.voices = alive
        d = int(min(Delay.SIZE - n, max(n, self.step_frames() * DELAY_TIMES[self.delay_div][1])))
        wet = self.delay.process(dbus, n, d, self.delay_fb)
        if wet is not None:
            out += wet
        wet = self.reverb.process(rbus, n, 0.4 + 5.0 * self.reverb_size ** 2)
        if wet is not None:
            out += wet
        out *= self.master * HEADROOM
        self._perform(out, n)
        # Headroom keeps overlapping sounds clean; only real peaks get rounded off.
        mag = np.abs(out)
        over = mag > KNEE
        if over.any():
            out[over] = np.sign(out[over]) * (KNEE + (1 - KNEE) * np.tanh((mag[over] - KNEE) / (1 - KNEE)))
        self.peak = max(float(mag.max()), self.peak * 0.9)
        if self.cap is not None and len(self.cap) < MAX_CAPTURE_BLOCKS:
            self.cap.append(out.copy())
        self._clock += n
        return out

    def _perform(self, out, n):
        """Hold-to-apply effects on the whole mix: stutter, tape stop, filter sweeps."""
        pf = self.perf
        mask = HSIZE - 1
        ar = _ar(n)
        self.hist[(self.hw + ar) & mask] = out
        if pf["stutter"]:
            if self._st_loop is None:
                length = int(max(256, min(HSIZE // 2, self.step_frames() * STUTTERS[self.stutter_div][1])))
                loop = self.hist[(self.hw + n - length + _ar(length)) & mask].copy()
                f = min(64, length // 4)
                loop[:f] *= np.linspace(0, 1, f, dtype=np.float32)[:, None]
                loop[-f:] *= np.linspace(1, 0, f, dtype=np.float32)[:, None]
                self._st_loop, self._st_pos = loop, 0
            length = len(self._st_loop)
            out[:] = self._st_loop[(self._st_pos + ar) % length]
            self._st_pos = (self._st_pos + n) % length
        else:
            self._st_loop = None
        if pf["tape"]:
            if self._tape_pos is None:
                self._tape_pos, self._tape_rate = float(self.hw), 1.0
            rates = np.maximum(0.0, self._tape_rate - (ar + 1) / (0.7 * SR))
            pos = self._tape_pos + np.cumsum(rates)
            i0 = np.floor(pos).astype(np.int64)
            fr = (pos - i0).astype(np.float32)[:, None]
            a = self.hist[i0 & mask]
            out[:] = (a + (self.hist[(i0 + 1) & mask] - a) * fr) * np.minimum(1.0, rates * 6.0).astype(np.float32)[:, None]
            self._tape_pos, self._tape_rate = float(pos[-1]), float(rates[-1])
            self._tape_was = True
        elif self._tape_was:
            self._tape_pos, self._tape_was = None, False
            out *= np.linspace(0, 1, n, dtype=np.float32)[:, None]
        self.hw = (self.hw + n) & mask
        # filter sweep: closes while held, springs back on release
        held = pf["lp"] or pf["hp"]
        if held and self._fir_p <= 0:
            self._fir_hp = bool(pf["hp"])
        step = n / SR
        self._fir_p = min(1.0, self._fir_p + step / 1.2) if held else max(0.0, self._fir_p - step / 0.15)
        src = np.concatenate((self._fir_tail, out))
        self._fir_tail = src[-(TAPS - 1):].copy()
        if self._fir_p > 0 or self._fir_on:
            p = self._fir_p
            fc = 40.0 * (3500.0 / 40.0) ** p if self._fir_hp else 16000.0 * (200.0 / 16000.0) ** p
            h = _kernel(fc, self._fir_hp)
            wet = np.empty_like(out)
            wet[:, 0] = np.convolve(src[:, 0], h, "valid")
            wet[:, 1] = np.convolve(src[:, 1], h, "valid")
            now_on = p > 0
            if now_on and self._fir_on:
                out[:] = wet
            else:  # crossfade in or out for one block
                r = np.linspace(0, 1, n, dtype=np.float32)[:, None]
                out[:] = out * (1 - r) + wet * r if now_on else wet * (1 - r) + out * r
            self._fir_on = now_on

    def render_pcm(self, n):
        return (self.render(n) * 32767.0).astype("<i2")

    # ----- audio device
    def start_audio(self, enable=True):
        self.running = True
        if enable:
            try:
                self._open_device()
            except Exception as e:  # keep running silently so the UI still works
                self.audio_error = str(e)
                self.sdl = self.dev = None
        threading.Thread(target=self._run, daemon=True).start()

    def _open_device(self):
        cands = []
        try:
            import pygame
            d = os.path.dirname(pygame.__file__)
            cands += glob.glob(os.path.join(d, ".dylibs", "libSDL2-2*.dylib"))
            cands += glob.glob(os.path.join(d, "..", "pygame.libs", "libSDL2-2*.so*"))
        except ImportError:
            pass
        cands += ["libSDL2-2.0.so.0", "libSDL2.so", "libSDL2.dylib"]
        sdl = None
        for c in cands:
            try:
                sdl = ctypes.CDLL(c)
                break
            except OSError:
                continue
        if sdl is None:
            raise RuntimeError("SDL2 library not found")

        class Spec(ctypes.Structure):
            _fields_ = [("freq", ctypes.c_int), ("format", ctypes.c_uint16),
                        ("channels", ctypes.c_uint8), ("silence", ctypes.c_uint8),
                        ("samples", ctypes.c_uint16), ("padding", ctypes.c_uint16),
                        ("size", ctypes.c_uint32), ("callback", ctypes.c_void_p),
                        ("userdata", ctypes.c_void_p)]

        sdl.SDL_InitSubSystem.argtypes = [ctypes.c_uint32]
        sdl.SDL_OpenAudioDevice.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(Spec),
                                            ctypes.POINTER(Spec), ctypes.c_int]
        sdl.SDL_OpenAudioDevice.restype = ctypes.c_uint32
        sdl.SDL_QueueAudio.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        sdl.SDL_GetQueuedAudioSize.argtypes = [ctypes.c_uint32]
        sdl.SDL_GetQueuedAudioSize.restype = ctypes.c_uint32
        sdl.SDL_PauseAudioDevice.argtypes = [ctypes.c_uint32, ctypes.c_int]
        sdl.SDL_CloseAudioDevice.argtypes = [ctypes.c_uint32]
        sdl.SDL_GetError.restype = ctypes.c_char_p
        if sdl.SDL_InitSubSystem(0x10) != 0:
            raise RuntimeError("SDL audio init failed: %s" % sdl.SDL_GetError().decode())
        want, have = Spec(), Spec()
        want.freq, want.format, want.channels, want.samples = SR, 0x8010, 2, BLOCK
        dev = sdl.SDL_OpenAudioDevice(None, 0, ctypes.byref(want), ctypes.byref(have), 0)
        if not dev:
            raise RuntimeError("no audio device: %s" % sdl.SDL_GetError().decode())
        sdl.SDL_PauseAudioDevice(dev, 0)
        self.sdl, self.dev = sdl, dev

    def _run(self):
        target = BLOCK * 4 * self.queue_blocks
        started = False
        while self.running:
            if self.dev is None:
                self.render(BLOCK)
                time.sleep(BLOCK / SR)
                continue
            queued = self.sdl.SDL_GetQueuedAudioSize(self.dev)
            if queued < target:
                if queued == 0 and started:
                    self.underruns += 1
                t0 = time.perf_counter()
                pcm = self.render_pcm(BLOCK)
                self.max_render_ms = max(self.max_render_ms, (time.perf_counter() - t0) * 1000)
                self.sdl.SDL_QueueAudio(self.dev, pcm.ctypes.data, pcm.nbytes)
                started = True
            else:
                time.sleep(0.001)

    def shutdown(self):
        self.running = False
        time.sleep(0.05)
        if self.dev is not None:
            self.sdl.SDL_CloseAudioDevice(self.dev)
            self.dev = None

    # ----- projects
    def to_dict(self):
        return {
            "version": 2, "bpm": self.bpm, "swing": self.swing, "master": self.master,
            "delay_div": self.delay_div, "delay_fb": self.delay_fb, "reverb_size": self.reverb_size,
            "repeat_div": self.repeat_div, "stutter_div": self.stutter_div,
            "pattern": self.cur_pat,
            "patterns": [{"length": p["length"], "steps": p["steps"]} for p in self.patterns],
            "pads": [{"path": _rel(p.path) if p.path else "", "vol": p.vol, "pitch": p.pitch,
                      "start": p.start, "end": p.end, "mute": p.mute, "name": p.name,
                      "filt": p.filt, "dsend": p.dsend, "rsend": p.rsend, "choke": p.choke,
                      "fine": p.fine, "mode": p.mode, "rev": p.rev, "fin": p.fin, "fout": p.fout}
                     for p in self.pads],
        }

    def from_dict(self, d):
        """Load a project; returns a list of sample files that could not be loaded."""
        missing = []
        self.bpm = float(min(300, max(40, d.get("bpm", 120))))
        sw = float(d.get("swing", 50))
        if sw < 1.0:            # version 1 projects stored swing as 0..0.7
            sw = 50.0 + 25.0 * sw
        self.swing = float(min(75, max(50, sw)))
        self.delay_div = int(d.get("delay_div", 1)) % len(DELAY_TIMES)
        self.delay_fb = float(min(0.9, max(0, d.get("delay_fb", 0.45))))
        self.reverb_size = float(min(1, max(0, d.get("reverb_size", 0.4))))
        self.repeat_div = int(d.get("repeat_div", 3)) % len(RATES)
        self.stutter_div = int(d.get("stutter_div", 1)) % len(STUTTERS)
        self.holds.clear()
        self.master = float(min(1.5, max(0, d.get("master", 0.8))))
        self.cur_pat = int(d.get("pattern", 0)) % NPAT
        self.pending_pat = None
        for i in range(NPAT):
            pat = new_pattern()
            src = (d.get("patterns") or [])[i:i + 1]
            if src:
                pat["length"] = 32 if src[0].get("length") == 32 else 16
                for r, row in enumerate(src[0].get("steps", [])[:NPADS]):
                    for s, v in enumerate(row[:MAX_STEPS]):
                        pat["steps"][r][s] = int(v) if v in (0, 1, 2) else 0
            self.patterns[i] = pat
        for i, p in enumerate(self.pads):
            p.clear()
            src = (d.get("pads") or [])[i:i + 1]
            if not src:
                continue
            s = src[0]
            p.vol = float(min(1.5, max(0, s.get("vol", 0.8))))
            p.pitch = int(min(24, max(-24, s.get("pitch", 0))))
            p.start = float(min(0.999, max(0, s.get("start", 0))))
            p.end = float(min(1, max(p.start + 0.001, s.get("end", 1))))
            p.mute = bool(s.get("mute", False))
            p.filt = int(min(100, max(-100, s.get("filt", 0))))
            p.dsend = float(min(1, max(0, s.get("dsend", 0))))
            p.rsend = float(min(1, max(0, s.get("rsend", 0))))
            p.choke = int(s.get("choke", 0)) % (CHOKE_GROUPS + 1)
            p.fine = int(min(50, max(-50, s.get("fine", 0))))
            p.mode = int(s.get("mode", 0)) % len(MODES)
            p.rev = bool(s.get("rev", False))
            p.fin = int(min(2000, max(0, s.get("fin", 0))))
            p.fout = int(min(2000, max(0, s.get("fout", 0))))
            if s.get("path"):
                path = s["path"] if os.path.isabs(s["path"]) else os.path.join(APP_DIR, s["path"])
                try:
                    p.load(path)
                    if s.get("name"):
                        p.name = str(s["name"])[:24]
                except (OSError, ValueError):
                    missing.append(s["path"])
        prune_cache(self.pads)
        return missing

    def save(self, path):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.to_dict(), f)
        os.replace(tmp, path)

    def load(self, path):
        with open(path) as f:
            return self.from_dict(json.load(f))
