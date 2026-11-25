"""Renders the generated kits in packs/generated/. Every sound here is made from arithmetic.

    python packs/make_generated.py

The WAV files it writes are kept in the repository, so nobody needs to run this to use the kits; it is
here so the sounds can be changed and made again. It borrows the building blocks of the app's own
starter kit and style kits (pocketpads/kit.py and pocketpads/styles.py) and needs only NumPy.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pocketpads"))

from engine import SR, write_wav                                                    # noqa: E402
from kit import _hp, _lp, _metal, _sweep, _t                                        # noqa: E402
from styles import TAU, _bell, _clap, _env, _ep, _hat, _hz, _kick, _noise, _rich, _riser, _saws, _st, _swell   # noqa: E402


def _tom(t, rng, f):
    return np.tanh(1.7 * _sweep(t, f * 1.7, f, 0.05) * np.exp(-t / 0.15)) + 0.12 * _hp(_noise(rng, t), 6) * np.exp(-t / 0.006)


def _snare(t, rng, f, tone, tail):
    return tone * _sweep(t, f * 1.4, f, 0.03) * np.exp(-t / 0.06) + 0.85 * _hp(_noise(rng, t), 8) * np.exp(-t / tail)


def _cymbal(t, rng, decay):
    return _hp(0.5 * _metal(t) + 0.8 * _noise(rng, t), 4) * np.exp(-t / decay)


def _conga(t, rng, f):
    return _sweep(t, f * 1.35, f, 0.018) * np.exp(-t / 0.11) + 0.3 * _hp(_noise(rng, t), 5) * np.exp(-t / 0.006)


def analog_drums(rng):
    """Round, boomy drum-machine sounds: long kick, snappy snare, ticking hats."""
    s = {}
    s["KICK LONG"] = np.tanh(1.5 * _sweep(_t(0.9), 130, 46, 0.03)) * np.exp(-_t(0.9) / 0.3)
    s["KICK SHORT"] = np.tanh(1.5 * _sweep(_t(0.35), 140, 52, 0.03)) * np.exp(-_t(0.35) / 0.1)
    s["SNARE"] = _snare(_t(0.3), rng, 185, 0.6, 0.08)
    s["SNARE TIGHT"] = _snare(_t(0.18), rng, 220, 0.5, 0.04)
    s["CLAP"] = _clap(_t(0.36), rng)
    t = _t(0.07)
    s["RIM"] = (np.sin(TAU * 860 * t) + 0.6 * np.sign(np.sin(TAU * 1760 * t))) * np.exp(-t / 0.011)
    s["HAT CLOSED"] = _hat(_t(0.1), rng, 0.024)
    s["HAT OPEN"] = _hat(_t(0.6), rng, 0.2)
    s["TOM LOW"] = _tom(_t(0.5), rng, 95)
    s["TOM MID"] = _tom(_t(0.45), rng, 135)
    s["TOM HIGH"] = _tom(_t(0.4), rng, 185)
    t = _t(0.4)
    s["COWBELL"] = _hp(np.sign(np.sin(TAU * 540 * t)) + np.sign(np.sin(TAU * 800 * t)), 30) * (0.3 * np.exp(-t / 0.15) + 0.7 * np.exp(-t / 0.02))
    t = _t(0.06)
    s["CLAVE"] = np.sin(TAU * 2450 * t) * np.exp(-t / 0.012)
    t = _t(0.1)
    s["MARACAS"] = _hp(_noise(rng, t), 2) * np.minimum(t / 0.012, 1) * np.exp(-np.maximum(t - 0.012, 0) / 0.02)
    s["CYMBAL"] = _cymbal(_t(1.5), rng, 0.42)
    s["CONGA"] = _conga(_t(0.3), rng, 250.0)
    return s


def hard_drums(rng):
    """Punchy and bright, for techno and house: clicky kicks, cracking snares, long cymbals."""
    s = {}
    s["KICK HARD"] = _kick(_t(0.4), rng, 220, 50, 0.026, 0.13, 2.8, 0.3)
    s["KICK DIRTY"] = np.tanh(3.0 * _kick(_t(0.45), rng, 200, 46, 0.03, 0.17, 3.5, 0.2))
    s["KICK DEEP"] = _kick(_t(0.6), rng, 120, 40, 0.05, 0.22, 1.8, 0.1)
    s["SNARE"] = _snare(_t(0.3), rng, 200, 0.5, 0.09)
    s["SNARE NOISE"] = _hp(_noise(rng, _t(0.25)), 6) * np.exp(-_t(0.25) / 0.06)
    s["CLAP BIG"] = _clap(_t(0.5), rng, 0.16)
    s["HAT CLOSED"] = _hat(_t(0.08), rng, 0.018)
    s["HAT OPEN"] = _hat(_t(0.45), rng, 0.13)
    s["HAT PEDAL"] = _hat(_t(0.16), rng, 0.045)
    t = _t(0.8)
    s["RIDE"] = _hp(0.8 * _metal(t) + 0.25 * _noise(rng, t), 2) * (0.6 * np.exp(-t / 0.28) + 0.4 * np.exp(-t / 0.02))
    s["CRASH"] = _cymbal(_t(1.8), rng, 0.5)
    s["TOM LOW"] = _tom(_t(0.4), rng, 88)
    s["TOM HIGH"] = _tom(_t(0.35), rng, 150)
    t = _t(0.08)
    s["RIM"] = (np.sin(TAU * 950 * t) + 0.6 * np.sign(np.sin(TAU * 2000 * t))) * np.exp(-t / 0.01)
    t = _t(0.12)
    s["PERC"] = (np.sin(TAU * 1240 * t) + 0.5 * np.sin(TAU * 1873 * t)) * np.exp(-t / 0.022)
    t = _t(0.5)
    s["NOISE HIT"] = _lp(_noise(rng, t), 2) * np.exp(-t / 0.12)
    return s


def _dusty(x, rng, bits=8, hold=3, width=3, hiss=0.02):
    """Make a sound old: dull it, hold every few samples, round it to a few bits and lay hiss under it."""
    x = np.asarray(x, np.float64)
    x = _lp(x / max(1e-9, np.abs(x).max()), width)
    x = np.repeat(x[::hold], hold)[:len(x)]
    q = float(2 ** (bits - 1))
    x = np.round(x * q) / q
    fade = np.minimum(np.arange(len(x)) / 200.0, 1) * np.minimum((len(x) - np.arange(len(x))) / 400.0, 1)
    return x + hiss * _hp(rng.uniform(-1, 1, len(x)), 2) * fade


def lofi_drums(rng):
    """The same kind of drums put through an old sampler: dull, gritty, with hiss under them."""
    a = analog_drums(rng)
    s = {}
    s["KICK DUSTY"] = _dusty(a["KICK SHORT"], rng, 8, 3, 4)
    s["KICK THUD"] = _dusty(_kick(_t(0.4), rng, 110, 48, 0.05, 0.14, 1.6, 0.05), rng, 7, 4, 6)
    s["SNARE DUSTY"] = _dusty(a["SNARE"], rng, 8, 3, 2)
    s["SNARE CRUNCH"] = _dusty(a["SNARE TIGHT"], rng, 6, 4, 2)
    s["CLAP DUSTY"] = _dusty(a["CLAP"], rng, 8, 3, 2)
    s["RIM DUSTY"] = _dusty(a["RIM"], rng, 8, 2, 1)
    s["HAT DUSTY"] = _dusty(a["HAT CLOSED"], rng, 8, 2, 1, 0.01)
    s["HAT OPEN DUSTY"] = _dusty(a["HAT OPEN"], rng, 8, 2, 1, 0.01)
    s["SHAKER DUSTY"] = _dusty(a["MARACAS"], rng, 8, 2, 1, 0.01)
    s["TOM DUSTY"] = _dusty(a["TOM MID"], rng, 8, 3, 3)
    s["CONGA DUSTY"] = _dusty(a["CONGA"], rng, 8, 3, 2)
    s["COWBELL DUSTY"] = _dusty(a["COWBELL"], rng, 7, 3, 2)
    t = _t(4.0)
    pops = (rng.uniform(0, 1, len(t)) > 0.9993) * rng.uniform(-1, 1, len(t))
    s["VINYL CRACKLE"] = (_lp(np.convolve(pops, np.exp(-np.arange(40) / 6.0), mode="same"), 1) + 0.035 * _hp(_noise(rng, t), 2)
                          ) * np.minimum(t / 0.02, 1) * np.minimum((t[-1] - t) / 0.02, 1)
    s["TAPE HISS"] = _lp(_hp(_noise(rng, t), 3), 1) * np.minimum(t / 0.05, 1) * np.minimum((t[-1] - t) / 0.05, 1)
    return s


def bass(rng):
    """Bass sounds, all tuned to C so the pitch setting and step notes read as real notes."""
    f = _hz(36)
    s = {}
    t = _t(1.2)
    s["SUB C"] = np.sin(TAU * f * t) * _env(t, 0.005, 0.5)
    s["LONG BOOM C"] = np.tanh(1.9 * _sweep(_t(1.6), 130, _hz(24) * 2, 0.02)) * _env(_t(1.6), 0.003, 0.6)
    t = _t(0.8)
    s["SAW C"] = _lp(2 * ((t * f) % 1.0) - 1, 4) * _env(t, 0.004, 0.3)
    s["SQUARE C"] = _lp(np.sign(np.sin(TAU * f * t)), 5) * _env(t, 0.004, 0.3)
    t = _t(0.35)
    s["ACID C"] = np.tanh(2.2 * _rich(t, f * 2, 24, 0.75, 0.22)) * _env(t, 0.002, 0.14)
    t = _t(0.5)
    s["PLUCK C"] = (_rich(t, f, 10, 1.1, 0.25) + 0.7 * np.sin(TAU * f * t)) * _env(t, 0.003, 0.18)
    t = _t(1.5)
    l, r = _saws(t, [f, f * 2], rng, 18, 5)
    s["WIDE C"] = _st(l, r) * (_env(t, 0.01, 0.9) * np.minimum((t[-1] - t) / 0.1, 1))[:, None]
    t = _t(0.7)
    s["FM C"] = np.sin(TAU * f * t + 3.0 * np.exp(-t / 0.12) * np.sin(TAU * f * 2 * t)) * _env(t, 0.003, 0.28)
    s["ROUND C"] = (0.9 * np.sin(TAU * f * t) + 0.6 * _rich(t, f, 8, 1.2, 0.3)) * _env(t, 0.004, 0.24)
    s["ORGAN C"] = sum(a * np.sin(TAU * f * k * t) for k, a in ((1, 1.0), (2, 0.8), (3, 0.4))) * (
        np.minimum(t / 0.004, 1) * np.minimum((t[-1] - t) / 0.06, 1))
    return s


def keys(rng):
    """Notes and chords, tuned to C. Single notes are middle C; chords are C minor or C major."""
    s = {}
    f = _hz(60)
    s["PIANO ELECTRIC C"] = _ep(_t(2.2), f, 1.0, 0.9)
    t = _t(3.0)
    s["BELL C"] = _st(_bell(t, f * 2, 3.5, 2.2, 1.2), _bell(t, f * 2.004, 3.5, 2.2, 1.2))
    s["GLASS C"] = _st(_bell(t, f * 4, 5.4, 1.2, 1.4), _bell(t, f * 3.996, 5.4, 1.2, 1.4))
    t = _t(0.6)
    s["ORGAN C"] = sum(a * np.sin(TAU * f * k * t) for k, a in ((1, 1.0), (2, 0.7), (3, 0.5), (4, 0.25), (6, 0.2))) * (
        np.minimum(t / 0.004, 1) * np.minimum((t[-1] - t) / 0.08, 1))
    t = _t(1.2)
    s["PLUCK C"] = _rich(t, f, 10, 1.1, 0.5) * _env(t, 0.003, 0.35)
    t = _t(0.7)
    s["MARIMBA C"] = (np.sin(TAU * f * t) + 0.35 * np.sin(TAU * f * 4 * t) * np.exp(-t / 0.03)) * _env(t, 0.002, 0.2)
    t = _t(0.5)
    for name, notes in (("STAB MINOR", (60, 63, 67, 72)), ("STAB MAJOR", (60, 64, 67, 72)),
                        ("STAB MINOR 7", (60, 63, 67, 70)), ("STAB MAJOR 7", (60, 64, 67, 71))):
        l, r = _saws(t, [_hz(n) for n in notes], rng, 9, 6)
        s[name] = _st(l, r) * _env(t, 0.003, 0.15)[:, None]
    t = _t(5.0)
    for name, notes in (("PAD MINOR", (48, 55, 60, 63, 67)), ("PAD MAJOR", (48, 55, 60, 64, 67))):
        l, r = _saws(t, [_hz(n) for n in notes], rng, 9, 18)
        s[name] = _st(l, r) * _swell(t, 1.0, 3.0, 2.0)[:, None]
    t = _t(4.0)
    l, r = _saws(t, [_hz(n) for n in (60, 67, 72, 79)], rng, 7, 12)
    s["STRINGS C"] = _st(l, r) * _swell(t, 0.5, 2.6, 1.4)[:, None]
    t = _t(2.4)
    s["CHORD ELECTRIC MINOR 9"] = _st(sum(_ep(t, _hz(n) * 1.0015, 1.1, 1.3) for n in (48, 55, 58, 62, 63)),
                                      sum(_ep(t, _hz(n) * 0.9985, 1.1, 1.3) for n in (48, 55, 58, 62, 63)))
    return s


def effects(rng):
    """Risers, drops, hits and noises for getting from one section to the next."""
    s = {}
    s["RISER 2 SECONDS"] = _riser(_t(2.0), rng)
    s["RISER 4 SECONDS"] = _riser(_t(4.0), rng)
    s["FALL 2 SECONDS"] = _riser(_t(2.0), rng)[::-1]
    t = _t(1.6)
    s["BOOM"] = np.tanh(2.0 * _sweep(t, 120, 36, 0.09) * np.exp(-t / 0.5)) + 0.2 * _lp(_noise(rng, t), 30) * np.exp(-t / 0.3)
    t = _t(1.4)
    s["CYMBAL REVERSED"] = _cymbal(t, rng, 0.4)[::-1] * np.minimum((t[-1] - t) / 0.01, 1)
    t = _t(0.35)
    s["ZAP"] = _sweep(t, 4200, 120, 0.03) * np.exp(-t / 0.08)
    t = _t(0.5)
    s["LASER"] = np.sin(TAU * np.cumsum(2400 * np.exp(-t / 0.12) + 200) / SR) * np.exp(-t / 0.2)
    t = _t(2.0)
    s["SIREN"] = np.sin(TAU * np.cumsum(700 + 250 * np.sin(TAU * 3 * t)) / SR) * np.minimum(t / 0.02, 1) * np.minimum((t[-1] - t) / 0.3, 1)
    t = _t(2.2)
    s["SUB DROP"] = np.tanh(1.5 * np.sin(TAU * np.cumsum(90 * np.exp(-t / 0.7) + 28) / SR)) * np.minimum((t[-1] - t) / 0.4, 1)
    t = _t(0.6)
    s["NOISE HIT"] = _st(_hp(_noise(rng, t), 2), _hp(_noise(rng, t), 2)) * np.exp(-t / 0.14)[:, None]
    t = _t(0.4)
    steps = np.repeat(rng.uniform(200, 3000, 16), len(t) // 16 + 1)[:len(t)]
    s["GLITCH"] = np.sign(np.sin(TAU * np.cumsum(steps) / SR)) * np.minimum((t[-1] - t) / 0.01, 1) * 0.8
    t = _t(6.0)
    s["WIND"] = _st(_lp(_hp(_noise(rng, t), 60), 12), _lp(_hp(_noise(rng, t), 60), 12)) * (
        _swell(t, 2.2, 3.0, 3.0) * (0.7 + 0.3 * np.sin(TAU * 0.23 * t)))[:, None]
    return s


KITS = [("analog-drums", analog_drums), ("hard-drums", hard_drums), ("lofi-drums", lofi_drums),
        ("bass", bass), ("keys-and-chords", keys), ("effects", effects)]


def main(root=None):
    root = root or os.path.join(HERE, "generated")
    total = 0
    for folder, build in KITS:
        sounds = build(np.random.default_rng(23))
        out = os.path.join(root, folder)
        os.makedirs(out, exist_ok=True)
        for i, (name, x) in enumerate(sounds.items()):
            x = np.asarray(x, np.float64)
            if not np.isfinite(x).all() or np.abs(x).max() < 1e-6:
                raise ValueError("%s/%s is silent or broken" % (folder, name))
            path = os.path.join(out, "%02d %s.wav" % (i + 1, name))
            write_wav(path, x / np.abs(x).max() * 0.9)
            total += os.path.getsize(path)
        print("%-16s %2d sounds   %s" % (folder, len(sounds), build.__doc__.strip()))
    print("%.1f MB in %s" % (total / 1048576.0, root))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
