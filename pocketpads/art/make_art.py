"""Draws PocketPads' menu artwork: an 8-bit style picture of a classic pad sampler."""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

W, H, SCALE = 160, 120, 6
BG, BG2, STAR = (24, 20, 37), (34, 28, 52), (92, 84, 130)
OUT, BODY, BODY_HI, BODY_LO, PANEL = (30, 26, 36), (208, 202, 188), (236, 232, 220), (156, 150, 138), (182, 176, 162)
LCD, LCD_MID, LCD_DARK, LCD_INK = (155, 188, 15), (139, 172, 15), (48, 98, 48), (15, 56, 15)
PAD, PAD_HI, PAD_LO = (92, 88, 100), (128, 124, 138), (58, 54, 66)
ORANGE, TEAL, RED, WHITE = (255, 152, 62), (62, 205, 182), (226, 72, 72), (244, 240, 230)

GLYPHS = {
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "C": [".####", "#....", "#....", "#....", "#....", "#....", ".####"],
    "K": ["#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],
}


def main(path):
    pygame.display.init()
    s = pygame.Surface((W, H))
    px = lambda x, y, c: s.set_at((x, y), c)
    box = lambda x, y, w, h, c: s.fill(c, (x, y, w, h))

    def word(text, x, y, color, shadow=None):
        for ch in text:
            for r, row in enumerate(GLYPHS[ch]):
                for c, bit in enumerate(row):
                    if bit == "#":
                        if shadow:
                            px(x + c + 1, y + r + 1, shadow)
            for r, row in enumerate(GLYPHS[ch]):
                for c, bit in enumerate(row):
                    if bit == "#":
                        px(x + c, y + r, color)
            x += 6

    def button(x, y, w, h, c, hi, lo):
        box(x, y, w, h, c)
        box(x, y, w, 1, hi)
        box(x, y + h - 1, w, 1, lo)

    # backdrop: night sky, dithered horizon band, floor
    s.fill(BG)
    for y in range(H):
        for x in range(W):
            if y > 92 and (x + y) % 2 == 0:
                px(x, y, BG2)
            elif 84 < y <= 92 and (x + y) % 2 == 0 and (x + y // 2) % 4 == 0:
                px(x, y, BG2)
    for i in range(46):
        x, y = (i * 53 + 17) % W, (i * 29 + 5) % 80
        px(x, y, STAR if i % 3 else WHITE)

    word("POCKET", 47, 6, ORANGE, OUT)
    word("PADS", 89, 6, TEAL, OUT)

    # the machine
    bx, by, bw, bh = 12, 22, 136, 86
    box(bx + 3, by + bh, bw - 2, 3, (12, 10, 20))           # ground shadow
    box(bx - 1, by - 1, bw + 2, bh + 2, OUT)
    box(bx, by, bw, bh, BODY)
    box(bx, by, bw, 2, BODY_HI)
    box(bx, by, 2, bh, BODY_HI)
    box(bx, by + bh - 3, bw, 3, BODY_LO)
    box(bx + bw - 2, by, 2, bh, BODY_LO)
    box(bx + 4, by + 5, bw - 8, 34, PANEL)                   # raised top panel

    # screen with a tiny beat grid on it
    box(20, 30, 58, 26, OUT)
    box(22, 32, 54, 22, LCD)
    for y in range(32, 54, 2):
        box(22, y, 54, 1, LCD_MID)
    grid = ["#...#...#...#.#.", "....#.......#...", "#.#.#.#.#.#.#.#.", "#..#..#...#..#.."]
    for r, row in enumerate(grid):
        for c, bit in enumerate(row):
            box(26 + c * 3, 36 + r * 4, 2, 3, LCD_INK if bit == "#" else LCD_DARK if c % 4 == 0 else LCD_MID)
    box(26 + 6 * 3 - 1, 34, 1, 18, LCD_INK)                  # playhead
    for i in range(6):                                       # soft keys
        button(21 + i * 10, 58, 7, 3, PAD, PAD_HI, PAD_LO)

    # data wheel
    cx, cy = 112, 43
    for y in range(-13, 14):
        for x in range(-13, 14):
            d = x * x + y * y
            if d <= 169:
                px(cx + x, cy + y, OUT if d > 132 else PAD_LO if d > 100 else PAD if (x + y) > 2 else PAD_HI)
    box(cx - 5, cy - 6, 3, 3, OUT)                           # finger dimple
    for i, c in enumerate((PAD, PAD, ORANGE)):               # keys beside the wheel
        button(86, 32 + i * 8, 9, 5, c, PAD_HI if c == PAD else WHITE, PAD_LO)
        button(131, 32 + i * 8, 9, 5, PAD, PAD_HI, PAD_LO)

    # number keys and transport
    for r in range(4):
        for c in range(3):
            button(20 + c * 10, 68 + r * 8, 8, 5, WHITE, WHITE, BODY_LO)
    for i, c in enumerate((RED, PAD, PAD, TEAL)):
        button(52, 68 + i * 8, 18, 5, c, WHITE if c != PAD else PAD_HI, PAD_LO if c == PAD else OUT)

    # sixteen pads, a few lit
    lit = {0: ORANGE, 5: TEAL, 10: ORANGE, 15: TEAL}
    for r in range(4):
        for c in range(4):
            x, y = 78 + c * 16, 64 + r * 10
            col = lit.get(r * 4 + c, PAD)
            box(x - 1, y - 1, 15, 9, OUT)
            box(x, y, 13, 7, col)
            box(x, y, 13, 1, WHITE if col != PAD else PAD_HI)
            box(x, y, 1, 7, WHITE if col != PAD else PAD_HI)
            box(x, y + 6, 13, 1, PAD_LO if col == PAD else OUT)

    pygame.image.save(pygame.transform.scale(s, (W * SCALE, H * SCALE)), path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "pocketpads.png"))
