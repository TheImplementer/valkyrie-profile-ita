"""The title-screen menu (New Game, Continue, Configuration, Sound Mode, Prologue, Opening Movie,
Seraphic Gate): pictures of text, not table text. This module redraws them with other labels.

Where they are (2026-11-13):
* File 7 (directory word 7) is one SLZ type-2 block: a 4-bit TIM, 256 x 256 at VRAM (896, 256)
  with 3 CLUTs at (16, 504). It holds each label twice: thin anti-aliased text (CLUT 0x7E01) and a
  heavier blurred "glow" drawn behind it (CLUT 0x7E02, colour 0x505050), plus the copyright line
  in two pieces.
* File 1 (directory word 1) is the title code, one SLZ type-2 block loaded at 0x8002F824.
  func_80037860 draws the menu: for item t3 = 0-6 a switch (jump tables at 0x8005103C and
  0x8005105C) sets each sprite's size (lui + ori), sheet position (ori / addiu) and x (addiu,
  added to -8, so the thin labels are right-aligned at x 440) as immediates. The glow sprite is
  placed at the thin label's x, y (+-1 for some items); items 2, 3 and 6 have a second glow piece
  at a fixed x offset. Prologue's sheet position is `sh $zero` (0, 0), so its label stays at (0, 0).

The labels are rendered with a serif font through tools/render.swift (CoreText, macOS), thin in
Times New Roman 26 pt and the glow from Times New Roman Bold, blurred; then packed into the sheet
around the copyright pieces, and the immediates are patched.

Edit file: '# Valkyrie Profile title menu' header, then '@@ N' (item 0-6) and the label.
"""
import math
import os
import re
import struct
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
HEADER = '# Valkyrie Profile title menu'
ITEMS = ['New Game', 'Continue', 'Configuration', 'Sound Mode', 'Prologue', 'Opening Movie',
         'Seraphic Gate']
SHEET_WORD, CODE_WORD = 7, 1
CODE_BASE = 0x8002F824
TIM_PIX = 8 + 108 + 12                  # pixel data offset in the decompressed TIM
COPYRIGHT = [(0, 109, 256, 14), (108, 237, 112, 13)]     # kept where they are

# per item: thin (wh lui, wh ori, uv instruction or None for Prologue's (0, 0), x addiu, x mode)
THIN = [
    (0x80037bc4, 0x80037bc8, 0x80037bd0, 0x80037bdc, 'right'),
    (0x80037be8, 0x80037bec, 0x80037bf4, 0x80037c00, 'right'),
    (0x80037c0c, 0x80037c10, 0x80037c18, 0x80037c24, 'right'),
    (0x80037c30, 0x80037c34, 0x80037c3c, 0x80037c48, 'right'),
    (0x80037c54, 0x80037c5c, None, 0x80037c68, 'right'),
    (0x80037c74, 0x80037c78, 0x80037c80, 0x80037c8c, 'right'),
    (0x80037c98, 0x80037c9c, 0x80037cb0, 0x80037ca0, 'abs'),   # Seraphic Gate: x is absolute (64)
]
# glow piece 1: (wh lui, wh ori, uv, dx, dy) - placed at the thin label's x + dx, y + dy
GLOW = [
    (0x80037dd4, 0x80037dd8, 0x80037de0, 0, 0),
    (0x80037de4, 0x80037de8, 0x80037df0, 0, 0),
    (0x80037df4, 0x80037e04, 0x80037df8, -1, -1),
    (0x80037e1c, 0x80037e2c, 0x80037e20, -1, -1),
    (0x80037e44, 0x80037e54, 0x80037e48, 1, 1),
    (0x80037e74, 0x80037e78, 0x80037e80, 0, 0),
    (0x80037e84, 0x80037e88, 0x80037e8c, 0, 0),
]
# glow piece 2 (items 2, 3, 6): (wh lui, wh ori, uv, x offset addiu) - at piece 1's x + offset
GLOW2 = {2: (0x80037f4c, 0x80037f6c, 0x80037f74, 0x80037f84),
         3: (0x80037f88, 0x80037f8c, 0x80037f94, 0x80037fa4),
         6: (0x80037f60, 0x80037fa8, 0x80037fb0, 0x80037fbc)}
RIGHT_EDGE = 448                        # thin x = -8 + addiu: right edge 440
THIN_FONT = ('Times New Roman', 25, 0.9, 10)        # font, size, tracking, word gap
BOLD_FONT = ('Times New Roman Bold', 24, 0.9, 9)
GLOW_PAD = 4


# ---------------------------------------------------------------- rendering

def _renderer():
    src = os.path.join(HERE, 'tools', 'render.swift'); exe = os.path.join(HERE, 'tools', 'render')
    if not os.path.exists(exe) or os.path.getmtime(exe) < os.path.getmtime(src):
        subprocess.run(['swiftc', '-O', src, '-o', exe], check=True)
    return exe


def _render(font, size, text, tracking):
    out = os.path.join(HERE, 'tools', '_render.pgm')
    subprocess.run([_renderer(), font, str(size), text, out, str(tracking), '0'], check=True)
    lines = open(out).read().split('\n')
    w, h = map(int, lines[1].split())
    vals = [int(v) for l in lines[4:] for v in l.split()]
    os.remove(out)
    return [vals[y * w:(y + 1) * w] for y in range(h)]


def _text(spec, text):
    """Grey image (0-255) of text, words joined with the spec's gap; all words share a canvas
    height and baseline."""
    font, size, tr, gap = spec
    parts = [_render(font, size, w, tr) for w in text.split(' ')]
    h = max(len(p) for p in parts); out = [[] for _ in range(h)]
    for i, p in enumerate(parts):
        xs = [x for x in range(len(p[0])) if any(r[x] for r in p)] or [0]
        for y in range(h):
            out[y] += p[y][min(xs):max(xs) + 1] + ([0] * gap if i < len(parts) - 1 else [])
    return out


def _blur(img, sigma):
    r = int(math.ceil(sigma * 2.5)); k = [math.exp(-(i * i) / (2 * sigma * sigma)) for i in range(-r, r + 1)]
    s = sum(k); k = [v / s for v in k]; h = len(img); w = len(img[0])
    tmp = [[sum(img[y][min(w - 1, max(0, x + i - r))] * k[i] for i in range(len(k))) for x in range(w)] for y in range(h)]
    return [[sum(tmp[min(h - 1, max(0, y + i - r))][x] * k[i] for i in range(len(k))) for x in range(w)] for y in range(h)]


def _quant(img):
    return [[0 if v < 8 else max(1, min(15, int(round(15 * v / 255)))) for v in r] for r in img]


def _ink(img):
    ys = [y for y, r in enumerate(img) if any(r)]
    xs = [x for x in range(len(img[0])) if any(r[x] for r in img)]
    return min(xs), min(ys), max(xs), max(ys)


def label_images(text, dx=0, dy=0):
    """(thin, glow): 4-bit index images. The thin one is cropped to its ink; the glow one starts
    so that, placed at the thin label's position + (dx, dy), its text lines up with the thin."""
    t = _text(THIN_FONT, text)
    x0, y0, x1, y1 = _ink(t)
    thin = _quant([r[x0:x1 + 1] for r in t[y0:y1 + 1]])
    b = _text(BOLD_FONT, text)
    bx0, by0, bx1, by1 = _ink(b)
    core = [r[bx0:bx1 + 1] for r in b[by0:by1 + 1]]
    m = GLOW_PAD; w = len(core[0])
    canvas = [[0] * (w + 2 * m) for _ in range(m)] + [[0] * m + r + [0] * m for r in core] + \
             [[0] * (w + 2 * m) for _ in range(m)]
    soft = _blur(canvas, 0.8); wide = _blur(canvas, 2.0)
    g = _quant([[min(255, max(a, c * 1.4)) for a, c in zip(ra, rc)] for ra, rc in zip(soft, wide)])
    c0, r0 = max(0, m + dx), max(0, m + dy)
    glow = [r[c0:] for r in g[r0:]]
    while glow and not any(glow[-1]):                       # drop empty rows / columns at the
        glow.pop()                                          # bottom and right (the top-left
    while glow[0] and not any(r[-1] for r in glow):         # corner fixes the alignment)
        glow = [r[:-1] for r in glow]
    return thin, glow


# ---------------------------------------------------------------- packing

class Sheet:
    def __init__(self, tim):
        self.tim = bytearray(tim)
        self.used = [[False] * 256 for _ in range(256)]

    def px(self, x, y):
        v = self.tim[TIM_PIX + y * 128 + x // 2]
        return (v >> 4) if x & 1 else (v & 15)

    def put(self, x, y, c):
        i = TIM_PIX + y * 128 + x // 2
        self.tim[i] = (self.tim[i] & 0x0F) | (c << 4) if x & 1 else (self.tim[i] & 0xF0) | c

    def reserve(self, u, v, w, h):
        for y in range(v, min(256, v + h + 1)):
            for x in range(u, min(256, u + w + 1)):
                self.used[y][x] = True

    def clear_except(self, keep):
        for y in range(256):
            for x in range(256):
                if not any(u <= x < u + w and v <= y < v + h for u, v, w, h in keep):
                    self.put(x, y, 0)

    def fits(self, u, v, w, h):
        if u + w > 256 or v + h > 256:
            return False
        return not any(any(self.used[y][u:u + w]) for y in range(v, v + h))

    def place(self, img, at=None):
        h, w = len(img), len(img[0])
        if at is None:
            at = next(((u, v) for v in range(256 - h + 1) for u in range(256 - w + 1) if self.fits(u, v, w, h)), None)
            if at is None:
                return None
        u, v = at
        for y in range(h):
            for x in range(w):
                self.put(u + x, v + y, img[y][x])
        self.reserve(u, v, w, h)
        return u, v


# ---------------------------------------------------------------- code patching

def _set_imm(code, addr, value):
    off = addr - CODE_BASE
    word = struct.unpack_from('<I', code, off)[0]
    struct.pack_into('<I', code, off, (word & 0xFFFF0000) | (value & 0xFFFF))


def _pack(tim, imgs, key):
    """(sheet, pieces) with the images placed in the order key gives, or None if one does not fit.
    Prologue's thin label goes first, at (0, 0)."""
    sheet = Sheet(tim)
    sheet.clear_except(COPYRIGHT)
    for u, v, w, h in COPYRIGHT:
        sheet.reserve(u, v, w, h)
    items = []                          # (kind, item, part, image)
    for i in range(7):
        items.append(('thin', i, 0, imgs[i][0]))
        g = imgs[i][1]
        if i in GLOW2:
            half = len(g[0]) // 2
            items += [('glow', i, 0, [r[:half] for r in g]), ('glow', i, 1, [r[half:] for r in g])]
        else:
            items.append(('glow', i, 0, g))
    first = [it for it in items if it[:2] == ('thin', 4)]
    rest = sorted([it for it in items if it[:2] != ('thin', 4)], key=lambda it: key(it[3]))
    pieces = {}
    for kind, i, part, img in first + rest:
        loc = sheet.place(img, (0, 0) if (kind, i) == ('thin', 4) else None)
        if loc is None:
            return None
        pieces.setdefault((kind, i), {})[part] = (loc, len(img[0]), len(img))
    return sheet, {k: [v[p] for p in sorted(v)] for k, v in pieces.items()}


PACK_ORDERS = [lambda im: (-len(im), -len(im[0])),             # tallest first
               lambda im: (-len(im[0]), -len(im)),             # widest first
               lambda im: -len(im) * len(im[0])]               # largest first


def build(tim, code, labels, log=print):
    """(tim, code) with the labels drawn and the sprites patched. labels: {item: text}; items not
    given keep their English text (re-rendered, so the whole menu matches)."""
    code = bytearray(code)
    imgs = {}
    for i, name in enumerate(ITEMS):
        text = labels.get(i, name)
        imgs[i] = label_images(text, GLOW[i][3], GLOW[i][4])
    packed = next((r for r in (_pack(tim, imgs, k) for k in PACK_ORDERS) if r), None)
    if packed is None:
        total = sum(len(labels.get(i, n).replace(' ', '')) for i, n in enumerate(ITEMS))
        raise ValueError(f'title menu: the labels do not fit on the 256 x 256 sheet ({total} letters; '
                         f'shorten some)')
    sheet, pieces = packed
    for i in GLOW2:                     # piece 2's x offset is piece 1's width
        p = pieces[('glow', i)]
        pieces[('glow', i)] = [p[0], p[1] + (p[0][1],)]
    for i in range(7):
        (u, v), w, h = pieces[('thin', i)][0]
        lui, ori, uv, xa, mode = THIN[i]
        _set_imm(code, lui, h); _set_imm(code, ori, w)
        if uv is not None:
            _set_imm(code, uv, v << 8 | u)
        if mode == 'right':
            _set_imm(code, xa, RIGHT_EDGE - w)
        else:
            _set_imm(code, xa, 224 - w)     # Seraphic Gate: keep its right edge (64 + 160)
        g = pieces[('glow', i)]
        (u, v), w, h = g[0][:3]
        lui, ori, uv, _, _ = GLOW[i]
        _set_imm(code, lui, h); _set_imm(code, ori, w); _set_imm(code, uv, v << 8 | u)
        if i in GLOW2:
            (u, v), w, h, off = g[1]
            lui, ori, uv, xa = GLOW2[i]
            _set_imm(code, lui, h); _set_imm(code, ori, w); _set_imm(code, uv, v << 8 | u); _set_imm(code, xa, off)
        log(f'  title menu item {i}: {labels.get(i, ITEMS[i])!r}: thin {pieces[("thin", i)][0][1]} px wide')
    return bytes(sheet.tim), bytes(code)


# ---------------------------------------------------------------- edit files and disc files

def is_title_edit_file(path):
    try:
        with open(path, encoding='utf-8') as f:
            return f.readline().startswith(HEADER)
    except OSError:
        return False


def read_edit_file(path):
    recs = {}; cur = None
    for line in open(path, encoding='utf-8').read().split('\n'):
        m = re.fullmatch(r'@@ (\d+)(?: .*)?', line)
        if m:
            cur = int(m.group(1)); continue
        if cur is not None and line and not line.startswith('#'):
            recs[cur] = line.strip()
    return recs


def edit_file_text(labels=None):
    labels = labels or {}
    lines = [f'{HEADER} (pictures in file 7, drawn by file 1): one label per item.',
             '# The labels are re-rendered in Times New Roman (macOS fonts), as is any item left out.',
             '# Their total length is limited by the 256 x 256 sheet (English 73 letters; 79 fit).', '']
    for i, name in enumerate(ITEMS):
        lines += [f'@@ {i} {name}', labels.get(i, name)]
    return '\n'.join(lines) + '\n'


class TitleFile:
    """A one-block SLZ file (words 1 and 7), a unit for the build."""

    def __init__(self, ed, arc, dirw, word):
        self.ed = ed; self.word = word
        self.first = ed.vsn.sector(dirw[word]); self.end = ed.vsn.file_end(dirw, word)
        raw = arc[self.first * ed.SECTOR:self.end * ed.SECTOR]
        self.ents = ed.read_container(raw)
        self.data = ed.unpack(*self.ents[0])
        self.used = 16 + len(self.ents[0][1])
        self.label = f'title file {word}'

    def pack(self, data):
        ent = [self.ents[0][0], self.ed.vslz.compress(data, self.ents[0][0]), len(data)]
        return self.ed.write_container([ent]), (self.end - self.first) * self.ed.SECTOR


def build_files(ed, arc, dirw, labels, log=print):
    """[(TitleFile, packed bytes, room)] for files 7 and 1."""
    sheet = TitleFile(ed, arc, dirw, SHEET_WORD); code = TitleFile(ed, arc, dirw, CODE_WORD)
    tim, prog = build(sheet.data, code.data, labels, log)
    out = []
    for f, data in ((sheet, tim), (code, prog)):
        packed, room = f.pack(data)
        if len(packed) > room:
            raise ValueError(f'{f.label}: needs {len(packed)} bytes, its sectors hold {room}')
        log(f'  {f.label}: {f.used} -> {len(packed)} bytes of {room}')
        out.append((f, packed, room))
    return out
