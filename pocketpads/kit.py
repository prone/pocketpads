"""Synthesizes PocketPads' built-in starter kit, so no sample files need to be shipped."""
import os

import numpy as np

from engine import SR, write_wav

KIT_VERSION = "2"
NAMES = ["KICK", "SNARE", "CLAP", "RIM", "HAT CL", "HAT OP", "TOM LO", "TOM HI",
         "COWBELL", "SHAKER", "CRASH", "BLIP", "BASS C", "BASS G", "STAB", "ZAP"]


def _t(sec):
    return np.arange(int(SR * sec)) / SR


def _lp(x, width):
    k = np.hanning(width * 2 + 1)
    return np.convolve(x, k / k.sum(), mode="same")


def _hp(x, width):
    return x - _lp(x, width)


def _sweep(t, f0, f1, tau):
    f = f1 + (f0 - f1) * np.exp(-t / tau)
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def _metal(t):
    freqs = [205.3, 304.4, 369.6, 522.7, 540.0, 800.0]
    return sum(np.sign(np.sin(2 * np.pi * f * 4.1 * t)) for f in freqs) / 6


def _saw(t, f):
    return _lp(2 * ((t * f) % 1.0) - 1, 6)


def build(rng):
    n = lambda t: rng.uniform(-1, 1, len(t))
    s = {}
    t = _t(0.45)
    # Driven so the kick has harmonics a small speaker can reproduce, not just sub-bass.
    s["KICK"] = np.tanh(2.2 * _sweep(t, 170, 52, 0.035) * np.exp(-t / 0.15)) + 0.25 * _hp(n(t), 6) * np.exp(-t / 0.004)
    t = _t(0.3)
    s["SNARE"] = 0.55 * _sweep(t, 260, 180, 0.03) * np.exp(-t / 0.07) + 0.8 * _hp(n(t), 8) * np.exp(-t / 0.09)
    t = _t(0.35)
    env = sum(np.exp(-np.maximum(t - d, 0) / 0.012) * (t >= d) for d in (0, 0.011, 0.023)) / 2
    s["CLAP"] = _hp(_lp(n(t), 3), 20) * (env + 0.5 * np.exp(-t / 0.09) * (t > 0.03))
    t = _t(0.08)
    s["RIM"] = (np.sin(2 * np.pi * 820 * t) + 0.6 * np.sign(np.sin(2 * np.pi * 1730 * t))) * np.exp(-t / 0.012)
    t = _t(0.12)
    s["HAT CL"] = _hp(0.6 * _metal(t) + 0.6 * n(t), 3) * np.exp(-t / 0.028)
    t = _t(0.6)
    s["HAT OP"] = _hp(0.6 * _metal(t) + 0.6 * n(t), 3) * np.exp(-t / 0.2)
    t = _t(0.5)
    s["TOM LO"] = np.tanh(1.8 * _sweep(t, 190, 105, 0.06) * np.exp(-t / 0.17)) + 0.15 * _hp(n(t), 6) * np.exp(-t / 0.006)
    s["TOM HI"] = np.tanh(1.8 * _sweep(t, 290, 160, 0.06) * np.exp(-t / 0.14)) + 0.15 * _hp(n(t), 6) * np.exp(-t / 0.006)
    t = _t(0.4)
    s["COWBELL"] = _hp(np.sign(np.sin(2 * np.pi * 540 * t)) + np.sign(np.sin(2 * np.pi * 800 * t)), 30) * (
        0.3 * np.exp(-t / 0.15) + 0.7 * np.exp(-t / 0.02))
    t = _t(0.14)
    s["SHAKER"] = _hp(n(t), 2) * np.minimum(t / 0.03, 1) * np.exp(-np.maximum(t - 0.03, 0) / 0.03)
    t = _t(1.3)
    s["CRASH"] = _hp(0.5 * _metal(t) + 0.8 * n(t), 4) * np.exp(-t / 0.4)
    t = _t(0.18)
    s["BLIP"] = np.sin(2 * np.pi * 880 * t) * np.exp(-t / 0.05)
    t = _t(0.5)
    for name, f in (("BASS C", 65.41), ("BASS G", 98.0)):
        s[name] = (0.7 * _lp(_saw(t, f), 14) + 0.6 * np.sin(2 * np.pi * f * t)) * np.exp(-t / 0.22) * np.minimum(t / 0.004, 1)
    t = _t(0.45)
    s["STAB"] = sum(_saw(t, f) for f in (261.63, 311.13, 392.0, 523.25)) * np.exp(-t / 0.13) * np.minimum(t / 0.003, 1)
    t = _t(0.3)
    s["ZAP"] = _sweep(t, 3200, 90, 0.035) * np.exp(-t / 0.09)
    return s


def ensure_kit(folder):
    """Create the starter kit WAVs if missing; returns their paths in pad order."""
    os.makedirs(folder, exist_ok=True)
    paths = [os.path.join(folder, "%02d %s.wav" % (i + 1, nm)) for i, nm in enumerate(NAMES)]
    stamp = os.path.join(folder, ".version")
    current = False
    if os.path.exists(stamp):
        with open(stamp) as f:
            current = f.read().strip() == KIT_VERSION
    if not current or not all(os.path.exists(p) for p in paths):
        sounds = build(np.random.default_rng(7))
        for p, nm in zip(paths, NAMES):
            x = sounds[nm].astype(np.float64)
            write_wav(p, x / max(1e-9, np.abs(x).max()) * 0.9)
        with open(stamp, "w") as f:
            f.write(KIT_VERSION)
    return paths


def demo_patterns(patterns):
    """A starter beat in pattern A and a variation in B."""
    def put(pat, pad, steps, v=1):
        for st in steps:
            patterns[pat]["steps"][pad][st] = v
    for pat in (0, 1):
        put(pat, 0, (0, 4, 8, 12))
        put(pat, 1, (4, 12))
        put(pat, 4, range(0, 16, 2))
        put(pat, 4, (0, 8), 2)
    put(0, 5, (14,))
    put(0, 12, (0, 3, 6))
    put(0, 13, (10, 13))
    put(1, 0, (10,))
    put(1, 2, (4, 12))
    put(1, 9, range(1, 16, 2))
    put(1, 12, (0, 3))
    put(1, 13, (6, 11, 14))
    put(1, 14, (2, 9), 2)
