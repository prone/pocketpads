"""Colours shared by every screen layout."""

BG, PANEL, LINE = (17, 18, 23), (30, 32, 40), (72, 76, 90)
TEXT, DIM = (228, 229, 235), (128, 132, 148)
TEAL, ORANGE, RED, DARK = (62, 205, 182), (255, 152, 62), (236, 84, 84), (10, 11, 14)

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
