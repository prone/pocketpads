"""Style starters: for each style a synthesized kit, a set of patterns and a song that chains them.

Like the starter kit, every sound is made here from arithmetic, so no sample files are shipped. A style
is written to `samples/styles/<name>/` the first time it is asked for and then loaded like any other
project: `project()` returns a dictionary in the project-file format, ready for Engine.from_dict.

Patterns are written as text, one row per pad:

  drums   "x...X...x...x..."            x = hit, X = accented hit, . = rest, | is ignored (bar lines)
  notes   "0 . . 3 . . 7! . ..."        a number plays the pad that many semitones up or down,
                                        ! accents it, . is a rest (tokens are separated by spaces)

A row shorter than its pattern repeats to fill it.
"""
import os

import numpy as np

from engine import BANK, MAX_STEPS, NPADS, NPAT, PAT_NAMES, SR, write_wav
from kit import _hp, _lp, _metal, _sweep, _t

STYLES_VERSION = "1"
ORDER = ["AMBIENT", "TECHNO", "HOUSE", "HIP HOP"]
ABOUT = {"AMBIENT": "72 BPM, slow pads, bells and a soft pulse",
         "TECHNO": "132 BPM, driving kick, rolling bass, acid line",
         "HOUSE": "124 BPM, four on the floor, chords, organ hook",
         "HIP HOP": "88 BPM, swung boom bap, dusty keys, long 808"}
TAU = 2 * np.pi


# ---------------------------------------------------------------- building blocks
def _hz(note):
    """Frequency of a MIDI note number (69 = the A above middle C)."""
    return 440.0 * 2.0 ** ((note - 69) / 12.0)


def _env(t, attack, decay):
    """Straight-line attack, then an exponential fall."""
    return np.minimum(t / attack, 1.0) * np.exp(-np.maximum(t - attack, 0) / decay)


def _swell(t, attack, hold, release):
    """Slow rise, a level stretch until `hold` seconds, then a smooth fall to silence."""
    up = 0.5 - 0.5 * np.cos(np.pi * np.clip(t / attack, 0, 1))
    down = 0.5 + 0.5 * np.cos(np.pi * np.clip((t - hold) / release, 0, 1))
    return up * down


def _rich(t, f, count=10, tilt=1.0, tau=None):
    """A tone built from harmonics. With `tau` the higher ones die away sooner, like a filter closing."""
    out = np.zeros(len(t))
    for k in range(1, count + 1):
        if f * k > SR * 0.42:
            break
        part = np.sin(TAU * f * k * t) / k ** tilt
        out += part * np.exp(-t * k / tau) if tau else part
    return out


def _saws(t, freqs, rng, cents=8.0, width=10):
    """Detuned sawtooth waves, softened: the body of a pad or a chord stab. Returns (left, right)."""
    sides = []
    for sign in (1, -1):
        x = np.zeros(len(t))
        for f in freqs:
            for c in (sign * cents, -sign * cents * 0.55):
                x += 2.0 * ((t * f * 2.0 ** (c / 1200.0) + rng.uniform()) % 1.0) - 1.0
        sides.append(_lp(x, width))
    return sides


def _st(left, right=None):
    return np.stack([left, left if right is None else right], axis=1)


def _ep(t, f, bite=1.2, decay=0.9):
    """Electric-piano note: a sine bent by itself at the start, with a short bell on top."""
    body = np.sin(TAU * f * t + bite * np.exp(-t / 0.25) * np.sin(TAU * f * t))
    return (body + 0.12 * np.sin(TAU * f * 4 * t) * np.exp(-t / 0.05)) * _env(t, 0.004, decay)


def _bell(t, f, ratio=3.5, depth=2.5, decay=1.2):
    return np.sin(TAU * f * t + depth * np.exp(-t / (decay * 0.6)) * np.sin(TAU * f * ratio * t)) * _env(t, 0.002, decay)


def _noise(rng, t):
    return rng.uniform(-1, 1, len(t))


def _kick(t, rng, f0, f1, tau, decay, drive, click=0.2):
    return np.tanh(drive * _sweep(t, f0, f1, tau) * np.exp(-t / decay)) + click * _hp(_noise(rng, t), 6) * np.exp(-t / 0.004)


def _clap(t, rng, tail=0.09):
    env = sum(np.exp(-np.maximum(t - d, 0) / 0.012) * (t >= d) for d in (0, 0.011, 0.023)) / 2
    return _hp(_lp(_noise(rng, t), 3), 20) * (env + 0.5 * np.exp(-t / tail) * (t > 0.03))


def _hat(t, rng, decay):
    return _hp(0.6 * _metal(t) + 0.6 * _noise(rng, t), 3) * np.exp(-t / decay)


def _riser(t, rng):
    """Noise that grows louder and brighter to the end, for the bar before a drop."""
    x = t / t[-1]
    n = _noise(rng, t)
    return (_hp(_lp(n, 4), 40) * x ** 2 + 0.7 * _hp(n, 3) * x ** 4) * np.minimum((t[-1] - t) / 0.01, 1.0)


# ---------------------------------------------------------------- the kits
def _techno(rng):
    s = {}
    s["KICK"] = _kick(_t(0.4), rng, 210, 48, 0.028, 0.13, 2.6, 0.3)
    s["CLAP"] = _clap(_t(0.3), rng)
    s["HAT CL"] = _hat(_t(0.09), rng, 0.02)
    s["HAT OP"] = _hat(_t(0.4), rng, 0.11)
    t = _t(0.7)
    s["RIDE"] = _hp(0.8 * _metal(t) + 0.25 * _noise(rng, t), 2) * (0.6 * np.exp(-t / 0.25) + 0.4 * np.exp(-t / 0.02))
    t = _t(0.08)
    s["RIM"] = (np.sin(TAU * 900 * t) + 0.6 * np.sign(np.sin(TAU * 1900 * t))) * np.exp(-t / 0.011)
    t = _t(0.4)
    s["TOM"] = np.tanh(1.8 * _sweep(t, 170, 92, 0.05) * np.exp(-t / 0.14)) + 0.12 * _hp(_noise(rng, t), 6) * np.exp(-t / 0.006)
    t = _t(0.12)
    s["PERC"] = (np.sin(TAU * 1240 * t) + 0.5 * np.sin(TAU * 1873 * t)) * np.exp(-t / 0.022)
    t = _t(0.26)
    s["BASS"] = np.tanh(1.6 * (_rich(t, _hz(33), 12, 1.0, 0.35) + 0.8 * np.sin(TAU * _hz(33) * t))) * _env(t, 0.003, 0.11)
    t = _t(0.3)
    s["ACID"] = np.tanh(2.2 * _rich(t, _hz(45), 24, 0.75, 0.22)) * _env(t, 0.002, 0.13)
    t = _t(0.4)
    l, r = _saws(t, [_hz(n) for n in (57, 60, 64, 69)], rng, 10, 5)
    s["STAB"] = _st(l, r) * _env(t, 0.002, 0.1)[:, None]
    t = _t(4.2)
    l, r = _saws(t, [_hz(n) for n in (45, 52, 57, 60)], rng, 9, 22)
    s["PAD"] = _st(l, r) * _swell(t, 0.9, 2.6, 1.6)[:, None]
    s["RISER"] = _riser(_t(1.8), rng)
    t = _t(1.6)
    s["CRASH"] = _hp(0.5 * _metal(t) + 0.8 * _noise(rng, t), 4) * np.exp(-t / 0.45)
    t = _t(0.35)
    s["ZAP"] = _sweep(t, 4200, 120, 0.03) * np.exp(-t / 0.08)
    t = _t(1.4)
    s["BOOM"] = np.tanh(2.0 * _sweep(t, 120, 36, 0.09) * np.exp(-t / 0.5)) + 0.2 * _lp(_noise(rng, t), 30) * np.exp(-t / 0.3)
    return s


def _house(rng):
    s = {}
    s["KICK"] = _kick(_t(0.42), rng, 160, 50, 0.04, 0.16, 2.0, 0.18)
    s["CLAP"] = _clap(_t(0.36), rng, 0.11)
    t = _t(0.26)
    s["SNARE"] = 0.5 * _sweep(t, 250, 185, 0.03) * np.exp(-t / 0.06) + 0.8 * _hp(_noise(rng, t), 8) * np.exp(-t / 0.075)
    s["HAT CL"] = _hat(_t(0.1), rng, 0.024)
    s["HAT OP"] = _hat(_t(0.5), rng, 0.15)
    t = _t(0.13)
    s["SHAKER"] = _hp(_noise(rng, t), 2) * np.minimum(t / 0.025, 1) * np.exp(-np.maximum(t - 0.025, 0) / 0.028)
    t = _t(0.3)
    for name, f in (("CONGA HI", 330.0), ("CONGA LO", 215.0)):
        s[name] = _sweep(t, f * 1.35, f, 0.018) * np.exp(-t / 0.11) + 0.3 * _hp(_noise(rng, t), 5) * np.exp(-t / 0.006)
    t = _t(0.42)
    s["BASS"] = (0.9 * np.sin(TAU * _hz(36) * t) + 0.6 * _rich(t, _hz(36), 8, 1.2, 0.3)) * _env(t, 0.004, 0.17)
    t = _t(0.55)
    l, r = _saws(t, [_hz(n) for n in (60, 63, 67, 70)], rng, 9, 7)             # C minor seventh
    s["CHORD"] = _st(l, r) * _env(t, 0.003, 0.16)[:, None]
    t = _t(0.5)
    s["ORGAN"] = sum(a * np.sin(TAU * _hz(60) * k * t) for k, a in ((1, 1.0), (2, 0.7), (3, 0.5), (4, 0.25), (6, 0.2))) * (
        np.minimum(t / 0.004, 1) * np.minimum((t[-1] - t) / 0.08, 1) * (0.6 + 0.4 * np.exp(-t / 0.08)))
    t = _t(7.7)
    l, r = _saws(t, [_hz(n) for n in (60, 67, 72, 79)], rng, 7, 14)             # no third: it sits over every chord for four bars
    s["STRINGS"] = _st(l, r) * _swell(t, 0.7, 6.2, 1.5)[:, None]
    t = _t(1.3)
    s["PIANO"] = sum(_rich(t, _hz(n), 8, 1.3, 1.1) for n in (48, 60, 63, 67, 70)) * _env(t, 0.003, 0.45)
    t = _t(1.6)
    s["CRASH"] = _hp(0.5 * _metal(t) + 0.8 * _noise(rng, t), 4) * np.exp(-t / 0.45)
    t = _t(0.6)
    s["RIDE"] = _hp(0.8 * _metal(t) + 0.25 * _noise(rng, t), 2) * (0.6 * np.exp(-t / 0.22) + 0.4 * np.exp(-t / 0.02))
    s["SWEEP"] = _riser(_t(1.93), rng)
    return s


def _hiphop(rng):
    s = {}
    s["KICK"] = _kick(_t(0.4), rng, 140, 55, 0.045, 0.14, 2.4, 0.15)
    t = _t(0.3)
    s["SNARE"] = 0.6 * _sweep(t, 240, 170, 0.03) * np.exp(-t / 0.06) + 0.9 * _hp(_lp(_noise(rng, t), 2), 10) * np.exp(-t / 0.085)
    s["CLAP"] = _clap(_t(0.34), rng, 0.1)
    t = _t(0.07)
    s["RIM"] = (np.sin(TAU * 780 * t) + 0.6 * np.sign(np.sin(TAU * 1650 * t))) * np.exp(-t / 0.012)
    s["HAT CL"] = _lp(_hat(_t(0.1), rng, 0.026), 1)
    s["HAT OP"] = _lp(_hat(_t(0.45), rng, 0.14), 1)
    t = _t(0.15)
    s["SHAKER"] = _hp(_noise(rng, t), 2) * np.minimum(t / 0.03, 1) * np.exp(-np.maximum(t - 0.03, 0) / 0.035)
    t = _t(0.3)
    s["TAMB"] = _hp(0.5 * _metal(t) + 0.7 * _noise(rng, t), 2) * (0.7 * np.exp(-t / 0.05) + 0.3 * np.exp(-t / 0.15))
    t = _t(1.5)
    s["808"] = np.tanh(1.9 * _sweep(t, 130, _hz(29), 0.02)) * _env(t, 0.003, 0.55)      # F, low, driven so small speakers show it
    t = _t(0.4)
    s["BASS"] = (_rich(t, _hz(41), 8, 1.1, 0.25) + 0.7 * np.sin(TAU * _hz(41) * t)) * _env(t, 0.003, 0.16)
    t = _t(3.6)
    fade = np.minimum((t[-1] - t) / 0.5, 1)
    for name, notes in (("KEYS MIN", (53, 60, 63, 67, 68)), ("KEYS MAJ", (49, 56, 60, 63, 65))):   # F minor 9, D flat major 7
        l = sum(_ep(t, _hz(n) * 1.0015, 1.1, 1.6) for n in notes)
        r = sum(_ep(t, _hz(n) * 0.9985, 1.1, 1.6) for n in notes)
        s[name] = _st(l, r) * ((1 + 0.12 * np.sin(TAU * 4.5 * t)) * fade)[:, None]
    t = _t(0.9)
    s["LEAD"] = _ep(t, _hz(65), 0.9, 0.5)
    t = _t(2.74)
    pops = (rng.uniform(0, 1, len(t)) > 0.9993) * rng.uniform(-1, 1, len(t))
    s["VINYL"] = (_lp(np.convolve(pops, np.exp(-np.arange(40) / 6.0), mode="same"), 1) + 0.035 * _hp(_noise(rng, t), 2)
                  ) * np.minimum(t / 0.02, 1) * np.minimum((t[-1] - t) / 0.02, 1)
    t = _t(1.36)
    s["REVERSE"] = (_hp(0.5 * _metal(t) + 0.8 * _noise(rng, t), 4) * np.exp(-t / 0.4))[::-1] * np.minimum((t[-1] - t) / 0.01, 1)
    t = _t(1.2)
    s["BELL"] = _bell(t, _hz(77), 4.0, 1.4, 0.6)
    return s


def _ambient(rng):
    s = {}
    t = _t(6.2)
    shape = _swell(t, 1.3, 3.6, 2.6)[:, None]
    for name, notes in (("PAD MIN", (50, 57, 62, 64, 65)), ("PAD MAJ", (46, 53, 58, 60, 62))):     # D minor add 9, B flat add 9
        l, r = _saws(t, [_hz(n) for n in notes], rng, 9, 20)
        s[name] = _st(l, r) * shape
    t = _t(13.3)                                                                # a whole pattern long, so it never drops out
    beat = 0.75 + 0.25 * np.sin(TAU * 0.11 * t)
    l = np.sin(TAU * _hz(38) * t) + 0.5 * np.sin(TAU * _hz(45) * 1.002 * t) + 0.2 * np.sin(TAU * _hz(50) * t)
    r = np.sin(TAU * _hz(38) * 1.003 * t) + 0.5 * np.sin(TAU * _hz(45) * t) + 0.2 * np.sin(TAU * _hz(50) * 0.998 * t)
    s["DRONE"] = _st(l, r) * (_swell(t, 2.5, 9.5, 3.8) * beat)[:, None]
    t = _t(3.5)
    s["BELL"] = _st(_bell(t, _hz(74), 3.5, 2.2, 1.3), _bell(t, _hz(74) * 1.002, 3.5, 2.2, 1.3))
    s["GLASS"] = _st(_bell(t, _hz(81), 5.4, 1.2, 1.6), _bell(t, _hz(81) * 0.998, 5.4, 1.2, 1.6))
    t = _t(1.4)
    s["PLUCK"] = _rich(t, _hz(62), 10, 1.1, 0.5) * _env(t, 0.003, 0.4)
    t = _t(2.2)
    s["KEYS"] = _ep(t, _hz(62), 0.7, 0.9)
    t = _t(4.0)
    s["SUB"] = np.tanh(1.5 * np.sin(TAU * _hz(38) * t)) * _swell(t, 0.6, 1.8, 2.2)
    t = _t(8.0)
    shape = _swell(t, 3.0, 4.0, 4.0)
    s["WIND"] = _st(_lp(_hp(_noise(rng, t), 60), 12), _lp(_hp(_noise(rng, t), 60), 12)) * (shape * (0.7 + 0.3 * np.sin(TAU * 0.23 * t)))[:, None]
    t = _t(5.0)
    spark = sum(np.sin(TAU * _hz(n) * t + rng.uniform(0, TAU)) * (0.5 + 0.5 * np.sin(TAU * rng.uniform(0.3, 1.1) * t + rng.uniform(0, TAU)))
                for n in (86, 89, 93, 96, 98))
    s["SHIMMER"] = (spark / 5 + 0.15 * _hp(_noise(rng, t), 2)) * _swell(t, 1.8, 2.5, 2.5)
    t = _t(6.0)
    grains = (rng.uniform(0, 1, len(t)) > 0.9985) * rng.uniform(-1, 1, len(t))
    s["TEXTURE"] = _st(np.convolve(grains, np.exp(-np.arange(300) / 60.0) * np.sin(np.arange(300) * 0.35), mode="same"),
                       np.convolve(grains[::-1], np.exp(-np.arange(300) / 60.0) * np.sin(np.arange(300) * 0.3), mode="same")
                       ) * _swell(t, 1.0, 4.0, 2.0)[:, None]
    t = _t(0.5)
    s["HEART"] = _sweep(t, 95, 44, 0.05) * _env(t, 0.006, 0.16)
    t = _t(0.06)
    s["TICK"] = _lp(np.sin(TAU * 2100 * t) + 0.5 * _noise(rng, t), 1) * np.exp(-t / 0.008)
    t = _t(0.2)
    s["SHAKER"] = _lp(_hp(_noise(rng, t), 3), 1) * np.minimum(t / 0.05, 1) * np.exp(-np.maximum(t - 0.05, 0) / 0.04)
    t = _t(3.3)
    l, r = _saws(t, [_hz(n) for n in (62, 69, 74, 77)], rng, 10, 12)
    s["SWELL"] = _st(l, r) * ((t / t[-1]) ** 2.5 * np.minimum((t[-1] - t) / 0.05, 1))[:, None]
    return s


# ---------------------------------------------------------------- the styles
# Each style: tempo and mix settings, the pads in order (name, then any settings that differ from
# a plain pad; "file" lets two pads share one sound), the patterns A to H, and the song.
def _bars(*bars):
    return " ".join(bars)


_AMB_PLUCK = _bars("0 . 7 . 12 . 15 . 14 . 12 . 7 . 3 .", "-4 . 3 . 8 . 12 . 10 . 8 . 5 . 3 .",
                   "3 . 10 . 15 . 19 . 17 . 15 . 10 . 7 .", "-2 . 5 . 10 . 14 . 12 . 10 . 5 . 2 .")
_AMB_PADS = {"PAD MIN": "x" + "." * 63, "PAD MAJ": _bars(". " * 16, "0 " + ". " * 15, ". " * 16, "2 " + ". " * 15),
             "PAD MAJ 2": _bars(". " * 32, "-5 " + ". " * 15, ". " * 16)}
_AMB_BELL = _bars("12 . . . . . . . . . 7 . . . . .", ". . . . 8 . . . . . . . 12 . . .",
                  "15 . . . . . . . . . 10 . . . . .", ". . . . 14 . . . . . 12 . . . . .")
_AMB_KEYS = _bars(". . . . 0 . . 3 . . 7 . . . . .", ". . . . . . 8 . . 5 . . 3 . . .",
                  ". . . . 3 . . 7 . . 10 . . . 7 .", ". . . . . . 5 . . 2 . . 0 . . .")

_TEC_BASS = ". . 0 0 . . 0 0 . . 0 0 . . 0 3"
_TEC_BASS2 = _bars(". . 0 0 . . 0 0 . . 0 0 . . 0 3", ". . 0 0 . . 0 0 . . -2 -2 . . 5 3")
_TEC_ACID = _bars("0 . 0 12! . 0 . 3 0 . 12 0 . 7! . 5", "0 . 0 12! . 0 . 3 0 . 10 0 . 15! 12 10")
_TEC_ACID2 = _bars("0 0 . 12! . 0 3 . 0 12 . 0 7! . 5 .", "0 0 . 12! . 0 3 . 15! 12 . 10 7 . 5 3")
_TEC_FILL = "x...x...x...x...|x...x...x...x.x."

_HOU_CHORD = _bars(". . 0 . . 0 . . . . 0 . . 0! . .", ". . 0 . . 0 . . . . 0 . . . 0 .",
                   ". . 5 . . 5 . . . . 5 . . 5! . .", ". . 7 . . 7 . . . . 7 . 7 . . .")
_HOU_BASS = _bars("0 . . 0 . . 0 . . . 0 . . . 12 .", "0 . . 0 . . 0 . . . 0 . 12 . 10 .",
                  "5 . . 5 . . 5 . . . 5 . . . 17 .", "7 . . 7 . . 7 . . . 7 . 10 . 7 .")
_HOU_ORGAN = _bars("12 . . 10 . . 7 . . . . . 7 . 10 .", "12 . . . . . . . . . 7 . 3 . 7 .",
                   "12 . . 10 . . 8 . . . . . 5 . 8 .", "14 . . 10 . . 7 . . . 5 . 7 . . .")
_HOU_PIANO = _bars("0! . . . . . . . . . . . . . . .", ". . . . . . . . . . . . . . . .",
                   "5! . . . . . . . . . . . . . . .", "7! . . . . . . . . . 7 . . . . .")

_HIP_MIN = _bars("0 " + ". " * 31, "5 " + ". " * 15, "7 " + ". " * 15)        # F minor, (D flat), B flat minor, C minor
_HIP_MAJ = _bars(". " * 16, "0 " + ". " * 15, ". " * 32)
_HIP_808 = _bars("0 . . . . . . . . . 0 . . . . .", "-4 . . . . . . . . . -4 . . 3 . .",
                 "5 . . . . . . . . . 5 . . . . .", "7 . . . . . . 7 . . 7 . . . 10 .")
_HIP_BASS = _bars(". . . 0 . . 12 . . . . . . 7 . .", ". . . -4 . . 8 . . . . . . 3 . .",
                  ". . . 5 . . 17 . . . . . . 12 . .", ". . . 7 . . 19 . . . . . 14 . 10 .")
_HIP_LEAD = _bars("7 . . 3 . . 0 . . . . . 3 . 5 .", "7 . . . . . 3 . . . 0 . . . . .",
                  "8 . . 5 . . 3 . . . . . 5 . 8 .", "10 . . 7 . . 3 . . . 2 . . . . .")
_HIP_KICK = "x......x..x.....|x..x...x..x..x.."
_HIP_HATS = "X.x.x.x.X.x.x.x.|X.x.x.x.X.x.x.xx"

STYLES = {
    "AMBIENT": {
        "bpm": 72, "swing": 50, "delay_div": 2, "delay_fb": 0.6, "reverb_size": 0.9, "comp": 0.2,
        "pads": [("PAD MIN", {"vol": 0.42, "rsend": 0.35}), ("PAD MAJ", {"vol": 0.42, "rsend": 0.35}),
                 ("PAD MAJ 2", {"vol": 0.42, "rsend": 0.35, "file": "PAD MAJ"}), ("DRONE", {"vol": 0.45, "rsend": 0.2}),
                 ("BELL", {"vol": 0.5, "dsend": 0.5, "rsend": 0.5}), ("GLASS", {"vol": 0.4, "dsend": 0.55, "rsend": 0.6}),
                 ("PLUCK", {"vol": 0.45, "dsend": 0.45, "rsend": 0.4}), ("KEYS", {"vol": 0.55, "dsend": 0.35, "rsend": 0.45}),
                 ("SUB", {"vol": 0.6}), ("WIND", {"vol": 0.4, "rsend": 0.3}), ("SHIMMER", {"vol": 0.3, "rsend": 0.5}),
                 ("TEXTURE", {"vol": 0.25, "dsend": 0.3, "rsend": 0.4}), ("HEART", {"vol": 0.75, "rsend": 0.1}),
                 ("TICK", {"vol": 0.35, "dsend": 0.5, "rsend": 0.3}), ("SHAKER", {"vol": 0.3, "rsend": 0.3}),
                 ("SWELL", {"vol": 0.35, "rsend": 0.5})],
        "patterns": [
            (64, {"PAD MIN": "x" + "." * 63, "DRONE": "x" + "." * 63, "WIND": "x" + "." * 63,
                  "BELL": _bars(". " * 20 + "12 " + ". " * 11, ". " * 8 + "7 " + ". " * 23)}),
            (64, dict(_AMB_PADS, DRONE="x" + "." * 63, BELL=_AMB_BELL, WIND="." * 32 + "x" + "." * 31)),
            (64, dict(_AMB_PADS, DRONE="x" + "." * 63, BELL=_AMB_BELL, PLUCK=_AMB_PLUCK, HEART="x" + "." * 15,
                      TICK="....x.......x.x.")),
            (64, dict(_AMB_PADS, DRONE="x" + "." * 63, PLUCK=_AMB_PLUCK, KEYS=_AMB_KEYS, HEART="x.........x.....",
                      TICK="....x.......x.x.", SHAKER="..x...x...x...x.", SHIMMER="x" + "." * 63, SUB="x" + "." * 31)),
            (64, {"DRONE": "x" + "." * 63, "TEXTURE": "x" + "." * 31,
                  "GLASS": _bars("0 . . . . . . . . . . . 7 . . .", ". " * 16, "5 . . . . . . . . . 3 . . . . .", ". " * 16),
                  "SWELL": "." * 48 + "x" + "." * 15, "WIND": "." * 16 + "x" + "." * 47}),
            (64, dict(_AMB_PADS, DRONE="x" + "." * 63, BELL=_AMB_BELL, PLUCK=_AMB_PLUCK, KEYS=_AMB_KEYS,
                      HEART="x.........x.....", TICK="....x.......x.x.", SHAKER="..x...x...x...x.", SUB="x" + "." * 31,
                      GLASS=_bars(". " * 24 + "12 " + ". " * 7, ". " * 24 + "10 " + ". " * 7))),
            (64, {"PAD MIN": "x" + "." * 63, "WIND": "x" + "." * 63, "BELL": _bars("12 " + ". " * 15, ". " * 8 + "7 " + ". " * 7,
                                                                                    "3 " + ". " * 15, ". " * 8 + "0 " + ". " * 7),
                  "SHIMMER": "." * 16 + "x" + "." * 47}),
        ],
        "song": "ABBCCDDEFFDEG",
    },
    "TECHNO": {
        "bpm": 132, "swing": 50, "delay_div": 2, "delay_fb": 0.5, "reverb_size": 0.45, "comp": 0.35,
        "pads": [("KICK", {"vol": 0.95}), ("CLAP", {"vol": 0.6, "rsend": 0.2}), ("HAT CL", {"vol": 0.4, "choke": 1}),
                 ("HAT OP", {"vol": 0.45, "choke": 1}), ("RIDE", {"vol": 0.3}), ("RIM", {"vol": 0.45, "dsend": 0.3}),
                 ("TOM", {"vol": 0.6, "rsend": 0.15}), ("PERC", {"vol": 0.4, "dsend": 0.35}), ("BASS", {"vol": 0.7}),
                 ("ACID", {"vol": 0.42, "dsend": 0.25}), ("STAB", {"vol": 0.4, "dsend": 0.4, "rsend": 0.2}),
                 ("PAD", {"vol": 0.4, "rsend": 0.3}), ("RISER", {"vol": 0.45}), ("CRASH", {"vol": 0.45, "rsend": 0.2}),
                 ("ZAP", {"vol": 0.4, "dsend": 0.4}), ("BOOM", {"vol": 0.8, "rsend": 0.2})],
        "patterns": [
            (16, {"KICK": "x...x...x...x...", "PERC": ".x....x..x....x.", "RIM": "......x......x.."}),
            (16, {"KICK": "x...x...x...x...", "HAT CL": "xx.xxx.xxx.xxx.x", "HAT OP": "..x...x...x...x.", "CLAP": "....x.......x...",
                  "BASS": _TEC_BASS, "PERC": ".x....x..x....x."}),
            (32, {"KICK": "x...x...x...x...", "HAT CL": "xx.xxx.xxx.xxx.x", "HAT OP": "..x...x...x...x.", "CLAP": "....x.......x...",
                  "BASS": _TEC_BASS2, "ACID": _TEC_ACID, "RIM": "......x......x..", "TOM": "." * 16 + "............x.xx"}),
            (32, {"KICK": _TEC_FILL, "HAT CL": "xx.xxx.xxx.xxx.x", "HAT OP": "..X...x...X...x.", "CLAP": "....X.......X...",
                  "BASS": _TEC_BASS2, "ACID": _TEC_ACID2, "STAB": "...x......x.....|...x......x..x..", "RIDE": "x.x.x.x.x.x.x.x.",
                  "PERC": ".x....x..x....x."}),
            (32, {"PAD": "x" + "." * 31, "ACID": _bars("0 . . 12 . . . 3 . . 12 . . 7 . .", "0 . . 12 . 0 . 3 0 . 12 0 . 7! 12 15!"),
                  "HAT CL": "..x...x...x...x.", "RISER": "." * 16 + "x" + "." * 15, "CLAP": "." * 16 + "........x...x.xx",
                  "ZAP": "." * 12 + "x" + "." * 19}),
            (32, {"KICK": _TEC_FILL, "CRASH": "x" + "." * 31, "HAT CL": "xx.xxx.xxx.xxx.x", "HAT OP": "..X...x...X...x.",
                  "CLAP": "....X.......X...", "BASS": _TEC_BASS2, "ACID": _TEC_ACID, "STAB": "...x......x.....|...x......x..x..",
                  "RIDE": "x.x.x.x.x.x.x.x.", "RIM": "......x......x.."}),
            (16, {"KICK": "x...x...x...x...", "HAT CL": "..x...x...x...x.", "BASS": _TEC_BASS, "PERC": ".x....x..x....x."}),
            (16, {"KICK": "x...x...x...x...", "PERC": ".x....x..x....x.", "BOOM": "x" + "." * 15, "RIM": "......x......x.."}),
        ],
        "song": "AAAABBBBCCDDEFFDDCCEFFDDGGHH",
    },
    "HOUSE": {
        "bpm": 124, "swing": 54, "delay_div": 2, "delay_fb": 0.4, "reverb_size": 0.5, "comp": 0.3,
        "pads": [("KICK", {"vol": 0.95}), ("CLAP", {"vol": 0.6, "rsend": 0.25}), ("SNARE", {"vol": 0.5, "rsend": 0.15}),
                 ("HAT CL", {"vol": 0.38, "choke": 1}), ("HAT OP", {"vol": 0.45, "choke": 1}), ("SHAKER", {"vol": 0.32}),
                 ("CONGA HI", {"vol": 0.45, "rsend": 0.15}), ("CONGA LO", {"vol": 0.5, "rsend": 0.15}), ("BASS", {"vol": 0.75}),
                 ("CHORD", {"vol": 0.4, "dsend": 0.3, "rsend": 0.2}), ("ORGAN", {"vol": 0.4, "dsend": 0.25, "rsend": 0.15}),
                 ("STRINGS", {"vol": 0.36, "rsend": 0.3}), ("PIANO", {"vol": 0.45, "rsend": 0.25}),
                 ("CRASH", {"vol": 0.45, "rsend": 0.2}), ("RIDE", {"vol": 0.28}), ("SWEEP", {"vol": 0.45})],
        "patterns": [
            (64, {"KICK": "x...x...x...x...", "SHAKER": "..x...x...x...x.", "CONGA HI": "......x......x..", "CONGA LO": "...x......x....x",
                  "HAT CL": "..x...x...x...x."}),
            (64, {"KICK": "x...x...x...x...", "CLAP": "....x.......x...", "HAT CL": "x.xxx.xxx.xxx.xx", "HAT OP": "..x...x...x...x.",
                  "SHAKER": "..x...x...x...x.", "CONGA HI": "......x......x..", "CONGA LO": "...x......x....x", "BASS": _HOU_BASS}),
            (64, {"KICK": "x...x...x...x...", "CLAP": "....x.......x...", "HAT CL": "x.xxx.xxx.xxx.xx", "HAT OP": "..x...x...x...x.",
                  "SHAKER": "..x...x...x...x.", "CONGA LO": "...x......x....x", "BASS": _HOU_BASS, "CHORD": _HOU_CHORD}),
            (64, {"KICK": "x...x...x...x...", "CLAP": "....X.......X...", "HAT CL": "x.xxx.xxx.xxx.xx", "HAT OP": "..X...x...X...x.",
                  "SHAKER": "..x...x...x...x.", "CONGA HI": "......x......x..", "CONGA LO": "...x......x....x", "BASS": _HOU_BASS,
                  "CHORD": _HOU_CHORD, "ORGAN": _HOU_ORGAN, "RIDE": "x.x.x.x.x.x.x.x."}),
            (64, {"STRINGS": "x" + "." * 63, "CHORD": _HOU_CHORD, "SHAKER": "..x...x...x...x.", "CONGA HI": "......x......x..",
                  "ORGAN": _bars(". " * 32, "12 . . 10 . . 8 . . . . . 5 . 8 .", ". " * 16),
                  "SWEEP": "." * 48 + "x" + "." * 15, "SNARE": "." * 48 + "x...x...x.x.xxxx"}),
            (64, {"KICK": "x...x...x...x...", "CRASH": "x" + "." * 63, "CLAP": "....X.......X...", "HAT CL": "x.xxx.xxx.xxx.xx",
                  "HAT OP": "..X...x...X...x.", "SHAKER": "..x...x...x...x.", "CONGA HI": "......x......x..", "CONGA LO": "...x......x....x",
                  "BASS": _HOU_BASS, "CHORD": _HOU_CHORD, "PIANO": _HOU_PIANO, "ORGAN": _HOU_ORGAN, "RIDE": "x.x.x.x.x.x.x.x."}),
            (64, {"KICK": "x...x...x...x...", "SHAKER": "..x...x...x...x.", "CONGA HI": "......x......x..", "CONGA LO": "...x......x....x",
                  "BASS": _HOU_BASS, "HAT CL": "..x...x...x...x.", "STRINGS": "x" + "." * 63}),
        ],
        "song": "ABBCCDDEFFDDEFFGG",
    },
    "HIP HOP": {
        "bpm": 88, "swing": 60, "delay_div": 2, "delay_fb": 0.35, "reverb_size": 0.4, "comp": 0.4,
        "pads": [("KICK", {"vol": 0.95}), ("SNARE", {"vol": 0.75, "rsend": 0.12}), ("CLAP", {"vol": 0.4, "rsend": 0.2}),
                 ("RIM", {"vol": 0.45, "dsend": 0.2}), ("HAT CL", {"vol": 0.36, "choke": 1}), ("HAT OP", {"vol": 0.4, "choke": 1}),
                 ("SHAKER", {"vol": 0.3}), ("TAMB", {"vol": 0.3, "rsend": 0.15}), ("808", {"vol": 0.8}), ("BASS", {"vol": 0.5}),
                 ("KEYS MIN", {"vol": 0.4, "rsend": 0.2, "crush": 1, "down": 1}),
                 ("KEYS MAJ", {"vol": 0.4, "rsend": 0.2, "crush": 1, "down": 1}),
                 ("LEAD", {"vol": 0.42, "dsend": 0.35, "rsend": 0.25}), ("VINYL", {"vol": 0.2}),
                 ("REVERSE", {"vol": 0.35, "rsend": 0.2}), ("BELL", {"vol": 0.32, "dsend": 0.4, "rsend": 0.3})],
        "patterns": [
            (64, {"KEYS MIN": _HIP_MIN, "KEYS MAJ": _HIP_MAJ, "VINYL": "x" + "." * 15, "RIM": "....x.......x...",
                  "REVERSE": "." * 56 + "x" + "." * 7}),
            (64, {"KEYS MIN": _HIP_MIN, "KEYS MAJ": _HIP_MAJ, "VINYL": "x" + "." * 15, "KICK": _HIP_KICK, "SNARE": "....x.......x...",
                  "HAT CL": _HIP_HATS, "HAT OP": "." * 30 + "x."}),
            (64, {"KEYS MIN": _HIP_MIN, "KEYS MAJ": _HIP_MAJ, "VINYL": "x" + "." * 15, "KICK": _HIP_KICK, "SNARE": "....x.......x...",
                  "HAT CL": _HIP_HATS, "HAT OP": "." * 30 + "x.", "808": _HIP_808, "BASS": _HIP_BASS}),
            (64, {"KEYS MIN": _HIP_MIN, "KEYS MAJ": _HIP_MAJ, "VINYL": "x" + "." * 15, "KICK": _HIP_KICK, "SNARE": "....X.......X...",
                  "CLAP": "....x.......x...", "HAT CL": _HIP_HATS, "HAT OP": "." * 30 + "x.", "808": _HIP_808, "BASS": _HIP_BASS,
                  "LEAD": _HIP_LEAD}),
            (64, {"KEYS MIN": _HIP_MIN, "KEYS MAJ": _HIP_MAJ, "VINYL": "x" + "." * 15, "LEAD": _HIP_LEAD, "SHAKER": "..x...x...x...x.",
                  "808": _bars("0 " + ". " * 15, "-4 " + ". " * 15, "5 " + ". " * 15, "7 " + ". " * 15), "RIM": "....x.......x...",
                  "REVERSE": "." * 56 + "x" + "." * 7}),
            (64, {"KEYS MIN": _HIP_MIN, "KEYS MAJ": _HIP_MAJ, "VINYL": "x" + "." * 15, "KICK": _HIP_KICK, "SNARE": "....X.......X...",
                  "CLAP": "....x.......x...", "HAT CL": _HIP_HATS, "HAT OP": "." * 30 + "x.", "808": _HIP_808, "BASS": _HIP_BASS,
                  "LEAD": _HIP_LEAD, "TAMB": "..x...x...x...x.",
                  "BELL": _bars(". " * 12 + "12 . . .", ". " * 12 + "8 . 12 .", ". " * 12 + "12 . . .", ". " * 12 + "10 . 7 .")}),
            (64, {"KEYS MIN": _HIP_MIN, "KEYS MAJ": _HIP_MAJ, "VINYL": "x" + "." * 15, "KICK": "x.........x.....", "RIM": "....x.......x...",
                  "BELL": _bars("12 " + ". " * 31, "8 " + ". " * 31)}),
        ],
        "song": "ABCCDDEFFDG",
    },
}
BUILDERS = {"AMBIENT": _ambient, "TECHNO": _techno, "HOUSE": _house, "HIP HOP": _hiphop}


# ---------------------------------------------------------------- turning a style into a project
def parse_row(spec, length):
    """A row of pattern text as [(step, value, note), ...], repeated to fill `length` steps."""
    if " " in spec.strip():
        cells = [c for c in spec.split() if c != "|"]
    else:
        cells = [c for c in spec if c != "|"]
    if not cells or length % len(cells):
        raise ValueError("a row of %d steps does not fit a pattern of %d" % (len(cells), length))
    out = []
    for step in range(length):
        c = cells[step % len(cells)]
        if c == ".":
            continue
        if c in ("x", "X"):
            out.append((step, 2 if c == "X" else 1, 0))
        else:
            out.append((step, 2 if c.endswith("!") else 1, int(c.rstrip("!"))))
    return out


def folder_name(style):
    return style.lower().replace(" ", "_")


def ensure_sounds(style, root):
    """Create the style's WAV files under `root` if they are missing; returns {sound name: path}."""
    folder = os.path.join(root, folder_name(style))
    os.makedirs(folder, exist_ok=True)
    names = [name for name, opts in STYLES[style]["pads"] if "file" not in opts]
    paths = {name: os.path.join(folder, "%02d %s.wav" % (i + 1, name)) for i, name in enumerate(names)}
    stamp = os.path.join(folder, ".version")
    current = False
    if os.path.exists(stamp):
        with open(stamp) as f:
            current = f.read().strip() == STYLES_VERSION
    if not current or not all(os.path.exists(p) for p in paths.values()):
        sounds = BUILDERS[style](np.random.default_rng(11))
        for name in names:
            x = np.asarray(sounds[name], np.float64)
            write_wav(paths[name], x / max(1e-9, np.abs(x).max()) * 0.9)
        with open(stamp, "w") as f:
            f.write(STYLES_VERSION)
    return paths


def project(style, root):
    """The style as a project dictionary (see Engine.to_dict): kit on the first bank, patterns and song."""
    st = STYLES[style]
    paths = ensure_sounds(style, root)
    index = {name: i for i, (name, _) in enumerate(st["pads"])}
    pads = []
    for name, opts in st["pads"]:
        pad = {"path": paths[opts.get("file", name)], "name": name}
        pad.update((k, v) for k, v in opts.items() if k != "file")
        pads.append(pad)
    patterns = []
    for length, rows in st["patterns"]:
        steps = [[0] * length for _ in range(NPADS)]
        notes = [[0] * length for _ in range(NPADS)]
        for name, spec in rows.items():
            for step, value, note in parse_row(spec, length):
                steps[index[name]][step], notes[index[name]][step] = value, note
        patterns.append({"length": length, "steps": steps, "notes": notes})
    return {"bpm": st["bpm"], "swing": st["swing"], "master": 0.8, "comp": st["comp"], "delay_div": st["delay_div"],
            "delay_fb": st["delay_fb"], "reverb_size": st["reverb_size"], "pattern": 0, "patterns": patterns,
            "song": [PAT_NAMES.index(c) for c in st["song"]], "song_on": True, "pads": pads}


def check():
    """Every style must fit the app's limits; raises ValueError naming the first thing that does not."""
    for style in ORDER:
        st = STYLES[style]
        names = [name for name, _ in st["pads"]]
        if len(names) != BANK or len(set(names)) != BANK:
            raise ValueError("%s: needs %d differently named pads" % (style, BANK))
        if len(st["patterns"]) > NPAT:
            raise ValueError("%s: too many patterns" % style)
        for n, (length, rows) in enumerate(st["patterns"]):
            if not 16 <= length <= MAX_STEPS:
                raise ValueError("%s %s: bad length" % (style, PAT_NAMES[n]))
            for name, spec in rows.items():
                if name not in names:
                    raise ValueError("%s %s: no pad called %s" % (style, PAT_NAMES[n], name))
                try:
                    parse_row(spec, length)
                except ValueError as e:
                    raise ValueError("%s %s %s: %s" % (style, PAT_NAMES[n], name, e))
        if any(PAT_NAMES.index(c) >= len(st["patterns"]) for c in st["song"]):
            raise ValueError("%s: the song uses a pattern that is not there" % style)
