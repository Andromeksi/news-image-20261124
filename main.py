#!/usr/bin/env python3
"""News image: sum up 24 November 2026 world news in one 1024x1024 image.

100% program-drawn with a hand-built software rasterizer and a hand-built
5x7 bitmap font. Standard library only. Fully deterministic: no random
module, no clock, no network, no environment reads except
MAKEFUN_OUTPUT_DIR. Output: <MAKEFUN_OUTPUT_DIR>/entry.png (1024x1024).

Layout (leaves clear space for later additions):
  masthead  y 0..~115   (title + date + gold rule)
  Earth     centre (512, 472), r 172
  ICJ hero  left top    (16, 158, 300x312)
  NATO      right top   (708, 158, 300x312)
  Hormuz    left bottom (16, 490, 300x212)
  COP31     right bottom(708, 490, 300x212)
  bottom zone y 708..976: five more panels in two rows (Libya, Gaza,
  Nvidia / Grok, El Chapo Isidro) plus a secondary-list block, and a
  source strip at y ~992. The skyline band was dropped: it collided
  with the new bottom panels and the source strip.
"""

import os
import struct
import zlib

W, H = 1024, 1024

# ---------------------------------------------------------------- rasterizer

buf = bytearray(W * H * 3)


def put(x, y, c):
    if 0 <= x < W and 0 <= y < H:
        i = (y * W + x) * 3
        buf[i] = c[0]
        buf[i + 1] = c[1]
        buf[i + 2] = c[2]


def blend(x, y, c, a):
    """Alpha-blend colour c over the pixel; a in 0..255."""
    if 0 <= x < W and 0 <= y < H and a > 0:
        i = (y * W + x) * 3
        inv = 255 - a
        buf[i] = (c[0] * a + buf[i] * inv) // 255
        buf[i + 1] = (c[1] * a + buf[i + 1] * inv) // 255
        buf[i + 2] = (c[2] * a + buf[i + 2] * inv) // 255


def fill_rect(x, y, w, h, c):
    x0 = max(x, 0)
    y0 = max(y, 0)
    x1 = min(x + w, W)
    y1 = min(y + h, H)
    for yy in range(y0, y1):
        base = (yy * W + x0) * 3
        for xx in range(x0, x1):
            i = base + (xx - x0) * 3
            buf[i] = c[0]
            buf[i + 1] = c[1]
            buf[i + 2] = c[2]


def vgrad_rect(x, y, w, h, top, bottom):
    """Vertical gradient rectangle."""
    for yy in range(y, y + h):
        t = (yy - y) / max(h - 1, 1)
        c = tuple(int(top[k] + (bottom[k] - top[k]) * t) for k in range(3))
        fill_rect(x, yy, w, 1, c)


def draw_line(x0, y0, x1, y1, c):
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        put(x0, y0, c)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def draw_line_w(x0, y0, x1, y1, c, wdt=2):
    draw_line(x0, y0, x1, y1, c)
    if wdt > 1:
        draw_line(x0, y0 + 1, x1, y1 + 1, c)


def circle_outline(cx, cy, r, c):
    x = 0
    y = r
    d = 3 - 2 * r
    while x <= y:
        for px, py in ((cx + x, cy + y), (cx - x, cy + y),
                       (cx + x, cy - y), (cx - x, cy - y),
                       (cx + y, cy + x), (cx - y, cy + x),
                       (cx + y, cy - x), (cx - y, cy - x)):
            put(px, py, c)
        if d < 0:
            d += 4 * x + 6
        else:
            d += 4 * (x - y) + 10
            y -= 1
        x += 1


def fill_circle(cx, cy, r, c, clip=None):
    for y in range(cy - r, cy + r + 1):
        dy = y - cy
        if abs(dy) > r:
            continue
        dx = int((r * r - dy * dy) ** 0.5)
        for x in range(cx - dx, cx + dx + 1):
            if clip is not None:
                if (x - clip[0]) ** 2 + (y - clip[1]) ** 2 > clip[2] ** 2:
                    continue
            put(x, y, c)


def fill_circle_blend(cx, cy, r, c, a, clip=None):
    for y in range(cy - r, cy + r + 1):
        dy = y - cy
        if abs(dy) > r:
            continue
        dx = int((r * r - dy * dy) ** 0.5)
        for x in range(cx - dx, cx + dx + 1):
            if clip is not None:
                if (x - clip[0]) ** 2 + (y - clip[1]) ** 2 > clip[2] ** 2:
                    continue
            blend(x, y, c, a)


def fill_circle_vgrad(cx, cy, r, top, bottom):
    """Filled circle whose colour grades vertically top -> bottom."""
    for y in range(cy - r, cy + r + 1):
        dy = y - cy
        if abs(dy) > r:
            continue
        t = (y - (cy - r)) / max(2 * r, 1)
        c = tuple(int(top[k] + (bottom[k] - top[k]) * t) for k in range(3))
        dx = int((r * r - dy * dy) ** 0.5)
        fill_rect(cx - dx, y, 2 * dx + 1, 1, c)


def fill_polygon(pts, c, clip=None):
    ys = [p[1] for p in pts]
    y0 = max(min(ys), 0)
    y1 = min(max(ys), H - 1)
    n = len(pts)
    for y in range(y0, y1 + 1):
        nodes = []
        for i in range(n):
            x1, y1p = pts[i]
            x2, y2p = pts[(i + 1) % n]
            if (y1p <= y < y2p) or (y2p <= y < y1p):
                xi = x1 + (y - y1p) * (x2 - x1) / (y2p - y1p)
                nodes.append(xi)
        nodes.sort()
        for i in range(0, len(nodes) - 1, 2):
            xa = int(nodes[i] + 0.5)
            xb = int(nodes[i + 1] + 0.5)
            for x in range(max(xa, 0), min(xb, W - 1) + 1):
                if clip is not None:
                    if (x - clip[0]) ** 2 + (y - clip[1]) ** 2 > clip[2] ** 2:
                        continue
                put(x, y, c)


def draw_arc_bezier(p0, p1, p2, c, steps=28):
    """Quadratic bezier polyline (used for trade routes on the globe)."""
    prev = None
    for i in range(steps + 1):
        t = i / steps
        mt = 1 - t
        x = int(mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0])
        y = int(mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1])
        if prev is not None:
            draw_line(prev[0], prev[1], x, y, c)
            draw_line(prev[0], prev[1] + 1, x, y + 1, c)
        prev = (x, y)


# ---------------------------------------------------------------- 5x7 font
# Each glyph: 7 rows of 5 characters, '#' = ink, '.' = empty.

FONT = {
    "A": ("..#..", ".#.#.", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".####", "#....", "#....", "#....", "#....", "#....", ".####"),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".####", "#....", "#....", "#.###", "#...#", "#...#", ".###."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "J": ("..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#.#.#", "#..##", "#..##", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"),
    "X": ("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "0": (".###.", "#..##", "#.#.#", "##..#", "#...#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("####.", "....#", "....#", ".###.", "....#", "....#", "####."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": (".###.", "#....", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", "..#..", "..#..", "..#.."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "....#", ".###."),
    " ": (".....", ".....", ".....", ".....", ".....", ".....", "....."),
    ".": (".....", ".....", ".....", ".....", ".....", ".....", ".##.."),
    ",": (".....", ".....", ".....", ".....", ".....", ".##..", ".#..."),
    ":": (".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."),
    ";": (".....", ".##..", ".##..", ".....", ".##..", ".##..", ".#..."),
    "!": ("..#..", "..#..", "..#..", "..#..", "..#..", ".....", "..#.."),
    "?": (".###.", "#...#", "....#", "...#.", "..#..", ".....", "..#.."),
    "-": (".....", ".....", ".....", ".###.", ".....", ".....", "....."),
    "'": ("..#..", "..#..", ".#...", ".....", ".....", ".....", "....."),
    '"': (".#.#.", ".#.#.", ".#.#.", ".....", ".....", ".....", "....."),
    "/": ("....#", "....#", "...#.", "..#..", ".#...", "#....", "#...."),
    "(": ("...##", "..#..", ".#...", ".#...", ".#...", "..#..", "...##"),
    ")": ("##...", "..#..", "...#.", "...#.", "...#.", "..#..", "##..."),
    "%": ("##..#", "##.#.", "...#.", "..#..", ".#...", "#.##.", "#..##"),
    "&": (".##..", "#..#.", "#..#.", ".#...", "#.#.#", "#..#.", ".##.#"),
    "$": ("..#..", ".####", "#.#..", ".###.", "..#.#", "####.", "..#.."),
    "+": (".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."),
    "=": (".....", ".....", "#####", ".....", "#####", ".....", "....."),
}
BLANK = (".....", ".....", ".....", ".....", ".....", ".....", ".....")

for _g in FONT.values():
    assert len(_g) == 7 and all(len(_r) == 5 for _r in _g)


def text_width(s, scale):
    if not s:
        return 0
    return len(s) * 6 * scale - scale


def draw_text(x, y, s, scale, color):
    cx = x
    for ch in s:
        glyph = FONT.get(ch, BLANK)
        for row in range(7):
            grow = glyph[row]
            for col in range(5):
                if grow[col] == "#":
                    fill_rect(cx + col * scale, y + row * scale,
                              scale, scale, color)
        cx += 6 * scale


def draw_text_center(cx, y, s, scale, color):
    draw_text(cx - text_width(s, scale) // 2, y, s, scale, color)


# ---------------------------------------------------------------- deterministic LCG
# Fixed seed, so every run produces identical output.

_lcg_state = [987654321]


def lcg_next():
    _lcg_state[0] = (_lcg_state[0] * 1103515245 + 12345) & 0x7FFFFFFF
    return _lcg_state[0]


# ---------------------------------------------------------------- colours

BG_TOP = (4, 8, 20)
BG_BOTTOM = (12, 22, 48)
WHITE = (240, 244, 255)
SOFT = (198, 210, 228)
GOLD = (232, 196, 90)
PANEL_BG = (13, 24, 46)
PANEL_EDGE = (58, 78, 118)
OCEAN_TOP = (30, 96, 168)
OCEAN_BOTTOM = (8, 34, 92)
LAND = (74, 140, 86)
LAND_TAN = (186, 168, 108)
GLOW = (90, 180, 255)

# ---------------------------------------------------------------- background


def draw_background():
    vgrad_rect(0, 0, W, H, BG_TOP, BG_BOTTOM)
    # Stars: fixed-seed LCG, drawn before everything else.
    for _ in range(260):
        sx = lcg_next() % W
        sy = lcg_next() % H
        r = lcg_next()
        bright = 150 + (r % 106)
        size = 2 if r % 11 == 0 else 1
        c = (bright, bright, min(255, bright + 15))
        fill_rect(sx, sy, size, size, c)
        if r % 37 == 0:  # a few bright sparkles
            fill_rect(sx - 2, sy, 5, 1, c)
            fill_rect(sx, sy - 2, 1, 5, c)
    # Thin editorial frame.
    fill_rect(4, 4, W - 8, 2, (70, 90, 130))
    fill_rect(4, H - 6, W - 8, 2, (70, 90, 130))
    fill_rect(4, 4, 2, H - 8, (70, 90, 130))
    fill_rect(W - 6, 4, 2, H - 8, (70, 90, 130))


def draw_satellite_silhouette(x, y, s, c):
    fill_rect(x - 7 * s, y - 3 * s, 14 * s, 6 * s, c)
    fill_rect(x - 26 * s, y - 2 * s, 17 * s, 4 * s, c)
    fill_rect(x + 9 * s, y - 2 * s, 17 * s, 4 * s, c)
    draw_line(x, y - 3 * s, x + 4 * s, y - 9 * s, c)
    fill_circle(x + 5 * s, y - 10 * s, 2 * s, c)


def draw_skyline():
    # Faint city silhouettes just above the reserved bottom strip.
    base = 890
    x = 8
    while x < W - 8:
        wdt = 18 + lcg_next() % 18
        hgt = 8 + lcg_next() % 30
        fill_rect(x, base - hgt, wdt, hgt, (12, 18, 36))
        if lcg_next() % 5 == 0:  # antenna on a few towers
            draw_line(x + wdt // 2, base - hgt, x + wdt // 2,
                      base - hgt - 8, (12, 18, 36))
        # A few dim lit windows.
        for _ in range(3):
            wx = x + 3 + lcg_next() % max(wdt - 6, 1)
            wy = base - hgt + 3 + lcg_next() % max(hgt - 6, 1)
            if lcg_next() % 2 == 0:
                fill_rect(wx, wy, 2, 2, (190, 168, 100))
        x += wdt + 4


# ---------------------------------------------------------------- Earth

ECX, ECY, ER = 512, 472, 172


def _abs_poly(local_pts):
    return [(ECX + dx, ECY + dy) for dx, dy in local_pts]


def draw_earth():
    # Atmospheric glow: stacked translucent discs, outer first.
    for step in range(10, 0, -1):
        rr = ER + step * 2
        a = 6 + (10 - step) * 3
        fill_circle_blend(ECX, ECY, rr, GLOW, a)
    # Ocean with vertical gradient.
    fill_circle_vgrad(ECX, ECY, ER, OCEAN_TOP, OCEAN_BOTTOM)
    # Simplified continents (clipped to the globe disc).
    clip = (ECX, ECY, ER)
    continents = [
        [(-168, -52), (-150, -108), (-118, -128), (-84, -108), (-64, -78),
         (-72, -48), (-96, -28), (-128, -18), (-156, -22)],
        [(-52, -138), (-28, -142), (-18, -122), (-40, -116)],
        [(-98, 4), (-74, 18), (-58, 52), (-66, 96), (-84, 118),
         (-102, 88), (-108, 44), (-112, 16)],
        [(8, -72), (52, -80), (78, -34), (64, 16), (38, 58), (8, 48),
         (-12, 4), (-6, -38)],
        [(-2, -112), (28, -124), (52, -104), (44, -84), (18, -82), (0, -96)],
        [(58, -118), (120, -128), (158, -96), (168, -52), (146, -8),
         (112, -22), (84, -52), (62, -78)],
        [(118, 52), (158, 48), (166, 78), (140, 96), (116, 82)],
        [(108, 8), (126, 4), (132, 20), (112, 24)],
        [(96, 28), (110, 26), (114, 40), (98, 42)],
    ]
    for poly in continents:
        fill_polygon(_abs_poly(poly), LAND, clip)
    # Tan patches on two landmasses for depth.
    fill_polygon(_abs_poly([(20, -60), (48, -66), (60, -40), (40, -30),
                            (16, -38)]), LAND_TAN, clip)
    fill_polygon(_abs_poly([(90, -100), (130, -104), (140, -70), (100, -66)]),
                 LAND_TAN, clip)
    # Polar ice caps (clipped discs).
    fill_circle(ECX, ECY - ER + 12, 30, (225, 235, 245), clip)
    fill_circle(ECX, ECY + ER - 10, 26, (220, 230, 242), clip)
    # Faint graticule: latitude and longitude chords.
    grat = (140, 190, 240)
    for dy in (-110, -55, 0, 55, 110):
        half = int((ER * ER - dy * dy) ** 0.5)
        for xx in range(ECX - half, ECX + half + 1):
            blend(xx, ECY + dy, grat, 34)
    for dx in (-110, -55, 0, 55, 110):
        half = int((ER * ER - dx * dx) ** 0.5)
        for yy in range(ECY - half, ECY + half + 1):
            blend(ECX + dx, yy, grat, 34)
    # Soft daylight highlight, top-left of the globe.
    fill_circle_blend(ECX - 62, ECY - 72, 58, (255, 255, 255), 22, clip)
    # Trade-route arcs connecting points on the globe.
    routes = [
        ((-120, -40), (-50, -105), (20, -70)),
        ((-70, 70), (20, 130), (130, 60)),
        ((40, -90), (110, -140), (150, -30)),
    ]
    for a, ctrl, b in routes:
        p0 = (ECX + a[0], ECY + a[1])
        p1 = (ECX + ctrl[0], ECY + ctrl[1])
        p2 = (ECX + b[0], ECY + b[1])
        draw_arc_bezier(p0, p1, p2, (230, 200, 90))
        fill_circle(p0[0], p0[1], 4, (255, 220, 120))
        fill_circle(p2[0], p2[1], 4, (255, 220, 120))
    # Bright atmospheric rim.
    circle_outline(ECX, ECY, ER, (150, 210, 255))
    circle_outline(ECX, ECY, ER - 1, (110, 180, 240))


# ---------------------------------------------------------------- panels


def draw_panel(x, y, w, h, accent):
    fill_rect(x, y, w, h, PANEL_BG)
    fill_rect(x, y, w, 6, accent)
    # 1px edge.
    fill_rect(x, y + 6, w, 1, PANEL_EDGE)
    fill_rect(x, y + h - 1, w, 1, PANEL_EDGE)
    fill_rect(x, y, 1, h, PANEL_EDGE)
    fill_rect(x + w - 1, y, 1, h, PANEL_EDGE)


def motif_scales(x, y, accent):
    cx = x + 150
    gold = (216, 178, 60)
    # Base, post, beam, finial.
    fill_rect(cx - 45, y + 104, 90, 4, gold)
    fill_rect(cx - 2, y + 30, 4, 74, gold)
    fill_rect(cx - 55, y + 26, 110, 4, gold)
    fill_circle(cx, y + 20, 4, gold)
    # Left pan.
    draw_line(cx - 48, y + 30, cx - 62, y + 62, gold)
    draw_line(cx - 48, y + 30, cx - 34, y + 62, gold)
    fill_polygon([(cx - 68, y + 62), (cx - 28, y + 62), (cx - 48, y + 80)],
                 (190, 150, 50))
    # Right pan.
    draw_line(cx + 48, y + 30, cx + 34, y + 62, gold)
    draw_line(cx + 48, y + 30, cx + 62, y + 62, gold)
    fill_polygon([(cx + 28, y + 62), (cx + 68, y + 62), (cx + 48, y + 80)],
                 (190, 150, 50))


def motif_satellite(x, y, accent):
    cx = x + 150
    cy = y + 66
    # Orbit ring.
    circle_outline(cx, cy, 52, (70, 110, 180))
    # Solar panels with grid lines.
    fill_rect(cx - 58, cy - 6, 40, 12, (60, 110, 200))
    fill_rect(cx + 18, cy - 6, 40, 12, (60, 110, 200))
    for gx in range(cx - 50, cx - 16, 8):
        draw_line(gx, cy - 6, gx, cy + 6, (30, 60, 120))
    for gx in range(cx + 26, cx + 60, 8):
        draw_line(gx, cy - 6, gx, cy + 6, (30, 60, 120))
    # Body, antenna, dish.
    fill_rect(cx - 14, cy - 10, 28, 20, (150, 170, 200))
    fill_rect(cx - 6, cy - 3, 12, 6, (220, 230, 240))
    draw_line(cx, cy - 10, cx + 10, cy - 28, (200, 210, 225))
    circle_outline(cx + 12, cy - 30, 5, (200, 210, 225))
    # Small red warning dot: the reported threat.
    fill_circle(cx + 34, cy - 30, 4, (224, 70, 60))


def motif_hormuz(x, y, accent):
    # Two warships facing each other across a narrow channel. No fire,
    # no smoke, no explosions: the verified fact is record attacks logged.
    sea = (60, 120, 180)
    for wy in (y + 58, y + 64, y + 70):
        draw_line(x + 16, wy, x + 284, wy, sea)
    # Left ship, bow pointing right.
    fill_polygon([(x + 24, y + 52), (x + 96, y + 52), (x + 88, y + 64),
                  (x + 32, y + 64)], (90, 100, 115))
    fill_rect(x + 44, y + 40, 26, 12, (110, 120, 135))
    draw_line(x + 57, y + 40, x + 57, y + 30, (140, 150, 165))
    # Right ship, bow pointing left.
    fill_polygon([(x + 204, y + 52), (x + 276, y + 52), (x + 268, y + 64),
                  (x + 212, y + 64)], (90, 100, 115))
    fill_rect(x + 230, y + 40, 26, 12, (110, 120, 135))
    draw_line(x + 243, y + 40, x + 243, y + 30, (140, 150, 165))
    # Narrow channel marker between the bows.
    for yy in range(y + 52, y + 71, 4):
        fill_rect(x + 149, yy, 2, 2, (220, 200, 120))
    # Calm water reflection under the gap.
    draw_line(x + 130, y + 68, x + 170, y + 68, (120, 170, 220))


def motif_cop31(x, y, accent):
    # Divided conference hall: split table with a crack, smokestacks.
    # Motif kept compact (ends by y+66) so it never touches the headline.
    stack = (120, 125, 135)
    fill_rect(x + 40, y + 16, 16, 44, stack)
    fill_rect(x + 64, y + 10, 16, 50, stack)
    fill_rect(x + 38, y + 12, 20, 4, (150, 155, 165))
    fill_rect(x + 62, y + 6, 20, 4, (150, 155, 165))
    # Drifting smoke, translucent grey puffs.
    fill_circle_blend(x + 48, y + 4, 7, (170, 175, 185), 90)
    fill_circle_blend(x + 56, y - 2, 5, (170, 175, 185), 70)
    fill_circle_blend(x + 72, y + 0, 6, (170, 175, 185), 80)
    # Split table: two halves, a gap, a zigzag crack between them.
    table = (150, 110, 70)
    fill_rect(x + 150, y + 44, 52, 10, table)
    fill_rect(x + 216, y + 44, 52, 10, table)
    fill_rect(x + 154, y + 54, 6, 10, table)
    fill_rect(x + 192, y + 54, 6, 10, table)
    fill_rect(x + 220, y + 54, 6, 10, table)
    fill_rect(x + 258, y + 54, 6, 10, table)
    crack = (224, 96, 64)
    draw_line(x + 206, y + 42, x + 210, y + 48, crack)
    draw_line(x + 210, y + 48, x + 205, y + 54, crack)
    draw_line(x + 205, y + 54, x + 210, y + 60, crack)


def motif_libya_ship(x, y, accent):
    # Freighter under investigation: hull, bridge, masts, calm waves.
    # No explosion, no fire, no smoke: the 24 Nov story is the UN probe
    # and Tripoli's demand, not the 22 Nov blast.
    cx = x + 158
    hull = (150, 160, 175)
    fill_polygon([(cx - 62, y + 22), (cx + 62, y + 22), (cx + 52, y + 34),
                  (cx - 52, y + 34)], hull)
    fill_rect(cx - 28, y + 10, 30, 12, (170, 180, 195))
    fill_rect(cx + 2, y + 14, 14, 4, (220, 230, 240))
    draw_line(cx - 44, y + 22, cx - 44, y + 6, (180, 190, 205))
    draw_line(cx + 44, y + 22, cx + 44, y + 6, (180, 190, 205))
    fill_polygon([(cx - 44, y + 6), (cx - 30, y + 10), (cx - 44, y + 14)],
                 (216, 178, 60))
    sea = (60, 120, 180)
    for wy in (y + 38, y + 42):
        draw_line(x + 24, wy, x + 292, wy, sea)


def motif_gaza_ruins(x, y, accent):
    # Two damaged buildings with broken, uneven tops; rubble at the
    # base. No people, no gore: the humanitarian cost, not the violence.
    cx = x + 158
    wall = (150, 140, 125)
    dark = (105, 98, 85)
    fill_polygon([(cx - 70, y + 42), (cx - 70, y + 16), (cx - 58, y + 10),
                  (cx - 48, y + 18), (cx - 38, y + 12), (cx - 30, y + 20),
                  (cx - 30, y + 42)], wall)
    fill_polygon([(cx - 12, y + 42), (cx - 12, y + 22), (cx - 2, y + 14),
                  (cx + 8, y + 20), (cx + 18, y + 12), (cx + 28, y + 18),
                  (cx + 28, y + 42)], dark)
    # Dark empty windows.
    fill_rect(cx - 62, y + 24, 8, 8, (30, 28, 24))
    fill_rect(cx - 44, y + 28, 8, 8, (30, 28, 24))
    fill_rect(cx - 4, y + 28, 8, 8, (30, 28, 24))
    fill_rect(cx + 14, y + 30, 8, 8, (30, 28, 24))
    # Rubble dots along the base.
    for rx in (-66, -52, -20, 4, 24):
        fill_rect(cx + rx, y + 40, 4, 2, (120, 110, 95))


def motif_nvidia_chip(x, y, accent):
    # Square chip with pins on all sides, plus a rising market arrow.
    cx = x + 158
    cy = y + 26
    pin = (110, 170, 255)
    for dy in (-10, 0, 10):
        fill_rect(cx - 32, cy + dy - 2, 8, 4, pin)
        fill_rect(cx + 24, cy + dy - 2, 8, 4, pin)
    for dx in (-16, -5, 6, 17):
        fill_rect(cx + dx - 2, cy - 20, 4, 5, pin)
        fill_rect(cx + dx - 2, cy + 15, 4, 4, pin)
    fill_rect(cx - 24, cy - 15, 48, 30, (30, 60, 110))
    fill_rect(cx - 18, cy - 9, 36, 18, (16, 32, 64))
    fill_rect(cx - 9, cy - 4, 18, 8, (120, 200, 120))
    # Rising arrow, top right of the chip.
    arrow = (96, 205, 150)
    draw_line(cx + 40, y + 36, cx + 54, y + 20, arrow)
    draw_line(cx + 54, y + 20, cx + 46, y + 20, arrow)
    draw_line(cx + 54, y + 20, cx + 54, y + 28, arrow)


def motif_grok_circuit(x, y, accent):
    # Circuit head: round outline, nodes and traces inside, traces
    # leaving left and right.
    cx = x + 158
    cy = y + 24
    circle_outline(cx, cy, 18, (170, 120, 220))
    nodes = [(cx - 8, cy - 6), (cx + 8, cy - 8), (cx, cy + 4),
             (cx - 10, cy + 8), (cx + 10, cy + 8)]
    trace = (140, 100, 180)
    draw_line(cx - 8, cy - 6, cx + 8, cy - 8, trace)
    draw_line(cx + 8, cy - 8, cx, cy + 4, trace)
    draw_line(cx, cy + 4, cx - 10, cy + 8, trace)
    draw_line(cx, cy + 4, cx + 10, cy + 8, trace)
    for nx, ny in nodes:
        fill_circle(nx, ny, 3, (200, 160, 240))
    draw_line(cx - 18, cy, cx - 30, cy - 6, trace)
    draw_line(cx + 18, cy, cx + 30, cy - 6, trace)
    fill_circle(cx - 30, cy - 6, 2, (200, 160, 240))
    fill_circle(cx + 30, cy - 6, 2, (200, 160, 240))


def motif_drone(x, y, accent):
    # Quadcopter surveillance drone with a dotted flight path below.
    cx = x + 158
    cy = y + 22
    arm = (170, 180, 195)
    fill_rect(cx - 10, cy - 5, 20, 10, (170, 180, 195))
    fill_rect(cx - 4, cy + 5, 8, 4, (120, 200, 120))
    draw_line(cx - 10, cy - 2, cx - 34, cy - 10, arm)
    draw_line(cx + 10, cy - 2, cx + 34, cy - 10, arm)
    draw_line(cx - 10, cy + 2, cx - 34, cy + 10, arm)
    draw_line(cx + 10, cy + 2, cx + 34, cy + 10, arm)
    for rx, ry in ((-34, -12), (34, -12), (-34, 12), (34, 12)):
        draw_line(cx + rx - 10, cy + ry, cx + rx + 10, cy + ry,
                  (200, 210, 225))
        fill_circle(cx + rx, cy + ry, 2, (200, 210, 225))
    # Dotted surveillance path below the drone.
    for dx in range(-50, 56, 10):
        fill_rect(cx + dx, y + 40, 3, 2, (120, 170, 220))


def draw_panels():
    gold = (216, 178, 60)
    blue = (110, 170, 255)
    red = (224, 96, 64)
    green = (96, 205, 150)

    # --- HERO: ICJ, left top (largest panel) ---
    draw_panel(16, 158, 300, 312, gold)
    motif_scales(16, 158 + 16, gold)
    draw_text_center(166, 158 + 140, "MYANMAR GUILTY", 3, WHITE)
    draw_text_center(166, 158 + 166, "OF GENOCIDE", 3, WHITE)
    draw_text_center(166, 158 + 210, "ICJ RULES 9-6", 2, GOLD)
    draw_text_center(166, 158 + 232, "FIRST STATE FOUND", 2, SOFT)
    draw_text_center(166, 158 + 254, "RESPONSIBLE", 2, SOFT)

    # --- NATO / SPACE, right top ---
    draw_panel(708, 158, 300, 312, blue)
    motif_satellite(708, 158 + 16, blue)
    draw_text_center(858, 158 + 140, "NUCLEAR-ARMED", 3, WHITE)
    draw_text_center(858, 158 + 166, "SATELLITE?", 3, WHITE)
    draw_text_center(858, 158 + 210, "NATO VOWS TO", 2, (150, 195, 255))
    draw_text_center(858, 158 + 232, "DEFEND SPACE", 2, SOFT)

    # --- HORMUZ, left bottom ---
    draw_panel(16, 490, 300, 212, red)
    motif_hormuz(16, 490 + 8, red)
    draw_text_center(166, 490 + 84, "RECORD IRANIAN", 3, WHITE)
    draw_text_center(166, 490 + 110, "ATTACKS", 3, WHITE)
    draw_text_center(166, 490 + 148, "STRAIT OF HORMUZ", 2, (255, 170, 150))

    # --- COP31, right bottom ---
    draw_panel(708, 490, 300, 212, green)
    motif_cop31(708, 490 + 12, green)
    draw_text_center(858, 490 + 84, "COP31 ENDS", 3, WHITE)
    draw_text_center(858, 490 + 110, "WITHOUT A DEAL", 3, WHITE)
    draw_text_center(858, 490 + 148, "FOSSIL FUELS AND", 2, (170, 235, 200))
    draw_text_center(858, 490 + 170, "FINANCE DIVIDE SUMMIT", 2, SOFT)

    draw_bottom_panels()
    draw_source_strip()


def draw_bottom_panels():
    # Row 1: three panels, y 708..838 (130 tall). Row 2: two panels plus
    # the secondary-list block, y 846..976. Headlines scale 3 (two
    # lines), sub-captions scale 2; motifs stay above y+44 so nothing
    # touches the headline at y+46.
    rust = (200, 170, 90)
    ember = (224, 120, 90)
    mint = (96, 205, 150)
    violet = (170, 120, 220)
    sand = (220, 200, 120)

    # --- LIBYA, row 1 left ---
    draw_panel(16, 708, 316, 130, rust)
    motif_libya_ship(16, 708, rust)
    draw_text_center(174, 708 + 46, "UKRAINE CONFIRMS", 3, WHITE)
    draw_text_center(174, 708 + 72, "SHIP STRIKES", 3, WHITE)
    draw_text_center(174, 708 + 98, "UN PROBE DEMANDED", 2, SOFT)
    draw_text_center(174, 708 + 114, "TRIPOLI WANTS ANSWERS", 2, SOFT)

    # --- GAZA, row 1 centre ---
    draw_panel(354, 708, 316, 130, ember)
    motif_gaza_ruins(354, 708, ember)
    draw_text_center(512, 708 + 46, "AIRSTRIKES KILL", 3, WHITE)
    draw_text_center(512, 708 + 72, "AT LEAST SIX", 3, WHITE)
    draw_text_center(512, 708 + 98, "UN: 77 YEARS OF", 2, SOFT)
    draw_text_center(512, 708 + 114, "DEVELOPMENT UNDONE", 2, SOFT)

    # --- NVIDIA, row 1 right ---
    draw_panel(692, 708, 316, 130, mint)
    motif_nvidia_chip(692, 708, mint)
    draw_text_center(850, 708 + 46, "HUANG REINSTATED", 3, WHITE)
    draw_text_center(850, 708 + 72, "AS CEO", 3, WHITE)
    draw_text_center(850, 708 + 98, "MARKET SURGES", 2, (170, 235, 200))

    # --- AI / GROK, row 2 left ---
    draw_panel(16, 846, 316, 130, violet)
    motif_grok_circuit(16, 846, violet)
    draw_text_center(174, 846 + 46, "STATES SUE OVER", 3, WHITE)
    draw_text_center(174, 846 + 72, "GROK IMAGES", 3, WHITE)
    draw_text_center(174, 846 + 98, "OREGON AND VIRGINIA", 2, SOFT)
    draw_text_center(174, 846 + 114, "SUE SPACEX", 2, SOFT)

    # --- EL CHAPO ISIDRO, row 2 centre ---
    draw_panel(354, 846, 316, 130, sand)
    motif_drone(354, 846, sand)
    draw_text_center(512, 846 + 46, "EL CHAPO ISIDRO", 3, WHITE)
    draw_text_center(512, 846 + 72, "CAPTURED", 3, WHITE)
    draw_text_center(512, 846 + 98, "US DRONE HELPED", 2, SOFT)
    draw_text_center(512, 846 + 114, "SINALOA RAID", 2, SOFT)

    # --- SECONDARY LIST, row 2 right ---
    draw_panel(692, 846, 316, 130, GOLD)
    draw_text_center(850, 846 + 10, "ALSO ON 24 NOVEMBER 2026", 2, GOLD)
    items = [
        "DRC: FIGHTING TRAPS EBOLA RESPONDERS",
        "NEPAL M5.6 QUAKE",
        "US LOSES MEASLES ELIMINATION STATUS",
        "GERMANY GETS UKRAINE DELTA ACCESS",
        "SOLOMON ISLANDS ENDS CHINA PACT",
        "SANOFI BUYS INSMED FOR $41B",
        "TIANGONG HOSTS FIRST FOREIGN ASTRONAUT",
        "47,000 WOMEN KILLED BY PARTNERS IN 2025",
    ]
    for i, item in enumerate(items):
        draw_text(692 + 10, 846 + 32 + i * 11, item, 1, SOFT)


def draw_source_strip():
    draw_text_center(512, 992,
                     "SOURCES: BBC - REUTERS - AL JAZEERA - AP - THE HINDU - USGS",
                     1, SOFT)


# ---------------------------------------------------------------- masthead


def draw_masthead():
    draw_text_center(512, 16, "THE WORLD AT A CROSSROADS", 5, WHITE)
    draw_text_center(512, 64, "24 NOVEMBER 2026", 4, GOLD)
    # Gold rule under the masthead with small diamonds at the ends.
    fill_rect(48, 106, 928, 2, GOLD)
    fill_polygon([(40, 107), (48, 99), (56, 107), (48, 115)], GOLD)
    fill_polygon([(968, 107), (976, 99), (984, 107), (976, 115)], GOLD)


# ---------------------------------------------------------------- PNG encoder


def png_chunk(typ, data):
    out = struct.pack(">I", len(data)) + typ + data
    out += struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
    return out


def write_png(path):
    raw = b"".join(
        b"\x00" + bytes(buf[y * W * 3:(y + 1) * W * 3]) for y in range(H)
    )
    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + png_chunk(b"IHDR", ihdr)
           + png_chunk(b"IDAT", zlib.compress(raw, 9))
           + png_chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


# ---------------------------------------------------------------- main


def main():
    draw_background()
    # Small satellite silhouettes in open sky (outside panels).
    draw_satellite_silhouette(105, 132, 1, (90, 105, 130))
    draw_satellite_silhouette(930, 128, 1, (90, 105, 130))
    draw_satellite_silhouette(70, 760, 1, (80, 95, 120))
    draw_satellite_silhouette(950, 760, 1, (80, 95, 120))
    # Skyline band dropped: the bottom zone is now full of panels, and
    # the band would collide with them and the source strip.
    draw_earth()
    draw_panels()
    draw_masthead()
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "entry.png")
    size = write_png(path)
    print("wrote %s (%d bytes)" % (path, size))


if __name__ == "__main__":
    main()
