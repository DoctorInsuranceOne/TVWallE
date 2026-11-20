#!/usr/bin/env python3
"""Rebuilds the splash images from branding/DIO_logo.png (Python standard
library only). The results are committed, so the kiosk needs no image tools.

    python3 tools/make-branding.py

branding/DIO_logo.png        (source, not changed here)
    The Doctor Insurance One wordmark, 1329x512, OPAQUE on white: a copy of
    HuphSign/public/DIO_logo.png. It has an alpha channel but every pixel is
    opaque, and the brand CDN has no transparent, larger or vector version.
branding/logo.png            (made here)
    The same wordmark with the white made transparent, for a black screen.
    The wordmark has exactly two colours (orange and blue), so each pixel is
    unmixed into "one brand colour + how much of it": solid letters stay fully
    opaque and full-strength, and only the soft edges get partial alpha. (A
    generic white-to-alpha would leave the orange partly see-through.)
    Plymouth and the Firefox start page scale it to 40% of the screen width.
branding/splash-3840x2160.png  (made here)
    logo.png at 40% of the width (1536 px), centred on an opaque black 16:9
    canvas: sway's background while Firefox starts. sway scales the canvas to
    the screen ("fit"), so the logo lands where Plymouth drew it.
"""

import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
BRANDING = os.path.join(HERE, "..", "branding")
SOURCE = os.path.join(BRANDING, "DIO_logo.png")
LOGO_OUT = os.path.join(BRANDING, "logo.png")
SPLASH_OUT = os.path.join(BRANDING, "splash-3840x2160.png")

CANVAS_W, CANVAS_H = 3840, 2160
LOGO_SHARE = 0.40  # of the screen width, the same in Plymouth and the start page
WHITE = (255, 255, 255)


# --- PNG in and out (8-bit, non-interlaced) -----------------------------------

def read_png(path):
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    pos, idat = 8, b""
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
    if depth != 8 or interlace != 0 or ctype not in (2, 6):
        raise ValueError("only 8-bit, non-interlaced RGB/RGBA PNGs are supported")
    bpp = 4 if ctype == 6 else 3
    raw = zlib.decompress(idat)
    stride = width * bpp
    pixels, prev, i = [], bytearray(stride), 0
    for _ in range(height):
        kind, line = raw[i], bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if kind == 1:
                line[x] = (line[x] + a) & 255
            elif kind == 2:
                line[x] = (line[x] + b) & 255
            elif kind == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        pixels.append([tuple(line[x * bpp:x * bpp + 3]) for x in range(width)])
        prev = line
    return width, height, pixels


def write_png(path, width, height, rows, channels):
    """rows: bytes per row (RGB or RGBA)."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + row for row in rows)
    ctype = 6 if channels == 4 else 2
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(chunk(b"IEND", b""))


# --- White to transparent, by brand colour -------------------------------------

def brand_colours(pixels):
    """The two solid colours of the wordmark: the median of strongly coloured
    pixels, split by which of red and blue dominates."""
    groups = {"orange": [], "blue": []}
    for row in pixels:
        for r, g, b in row:
            if max(r, g, b) - min(r, g, b) > 120:
                groups["orange" if r > b else "blue"].append((r, g, b))
    colours = {}
    for name, found in groups.items():
        if not found:
            raise ValueError(f"no {name} pixels found in the logo")
        colours[name] = tuple(sorted(p[i] for p in found)[len(found) // 2] for i in range(3))
    return colours


def unmix(pixel, colours):
    """pixel = alpha * colour + (1 - alpha) * white, for the brand colour that
    fits best. Returns (colour, alpha 0..1)."""
    best = None
    for colour in colours:
        d = [WHITE[i] - colour[i] for i in range(3)]
        e = [WHITE[i] - pixel[i] for i in range(3)]
        alpha = sum(d[i] * e[i] for i in range(3)) / sum(v * v for v in d)
        alpha = min(1.0, max(0.0, alpha))
        error = sum((WHITE[i] - alpha * d[i] - pixel[i]) ** 2 for i in range(3))
        if best is None or error < best[2]:
            best = (colour, alpha, error)
    return best[0], best[1]


def make_logo():
    width, height, pixels = read_png(SOURCE)
    colours = brand_colours(pixels)
    print(f"brand colours: orange {colours['orange']}, blue {colours['blue']}")
    rgba = []  # rows of (r, g, b, a) floats, a in 0..1
    rows_out = []
    for row in pixels:
        out_row, out_bytes = [], bytearray()
        for p in row:
            colour, alpha = unmix(p, colours.values())
            if alpha < 0.02:  # paper noise
                colour, alpha = (0, 0, 0), 0.0
            out_row.append((colour[0], colour[1], colour[2], alpha))
            out_bytes += bytes((colour[0], colour[1], colour[2], round(alpha * 255)))
        rgba.append(out_row)
        rows_out.append(bytes(out_bytes))
    write_png(LOGO_OUT, width, height, rows_out, 4)
    print(f"{LOGO_OUT}: {width}x{height}, transparent background")
    return width, height, rgba


# --- The splash canvas ------------------------------------------------------------

def make_splash(width, height, rgba):
    logo_w = round(CANVAS_W * LOGO_SHARE)
    logo_h = round(height * logo_w / width)
    left = (CANVAS_W - logo_w) // 2
    top = (CANVAS_H - logo_h) // 2

    # Premultiplied by alpha: over black, the result IS the premultiplied colour.
    pre = [[(r * a, g * a, b * a) for r, g, b, a in row] for row in rgba]

    black_row = bytes(CANVAS_W * 3)
    rows = [black_row] * CANVAS_H
    sx_scale, sy_scale = width / logo_w, height / logo_h
    for ty in range(logo_h):
        sy = (ty + 0.5) * sy_scale - 0.5
        y0 = min(max(int(sy), 0), height - 1)
        y1 = min(y0 + 1, height - 1)
        fy = min(max(sy - y0, 0.0), 1.0)
        line = bytearray(black_row)
        for tx in range(logo_w):
            sx = (tx + 0.5) * sx_scale - 0.5
            x0 = min(max(int(sx), 0), width - 1)
            x1 = min(x0 + 1, width - 1)
            fx = min(max(sx - x0, 0.0), 1.0)
            p00, p01, p10, p11 = pre[y0][x0], pre[y0][x1], pre[y1][x0], pre[y1][x1]
            at = (left + tx) * 3
            for i in range(3):
                top_mix = p00[i] + (p01[i] - p00[i]) * fx
                bottom_mix = p10[i] + (p11[i] - p10[i]) * fx
                line[at + i] = max(0, min(255, round(top_mix + (bottom_mix - top_mix) * fy)))
        rows[top + ty] = bytes(line)
    write_png(SPLASH_OUT, CANVAS_W, CANVAS_H, rows, 3)
    print(f"{SPLASH_OUT}: {CANVAS_W}x{CANVAS_H}, logo {logo_w}x{logo_h} at ({left},{top})")


if __name__ == "__main__":
    make_splash(*make_logo())
