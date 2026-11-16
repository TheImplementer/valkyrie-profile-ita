"""Accented letters for translations (Italian, French, Spanish, ...), drawn from the game's own glyphs.

The English release has no accented letters. These are built from a base letter of the same font
(the scene, menu and battle fonts share one typeface) plus a 2-pixel accent:
* lower case: the x-height starts at row 4, so the accent goes in rows 1-2 (the i dot's row);
* capitals start at row 1, so the letter is shortened by 2 rows and the accent goes on top.

compose(base) takes character -> (24-byte bitmap, width) for the plain letters and returns the
same for every accented letter it can make. Bitmaps are 12 rows of little-endian u16, bit 15 =
leftmost pixel.
"""

GRAVE, ACUTE, CIRC, DIAER = 'grave', 'acute', 'circ', 'diaer'
# accented character -> (base letter, accent)
LETTERS = {
    'à': ('a', GRAVE), 'è': ('e', GRAVE), 'ì': ('i', GRAVE), 'ò': ('o', GRAVE),
    'ù': ('u', GRAVE), 'á': ('a', ACUTE), 'é': ('e', ACUTE), 'í': ('i', ACUTE),
    'ó': ('o', ACUTE), 'ú': ('u', ACUTE), 'â': ('a', CIRC), 'ê': ('e', CIRC),
    'î': ('i', CIRC), 'ô': ('o', CIRC), 'û': ('u', CIRC), 'ä': ('a', DIAER),
    'ë': ('e', DIAER), 'ï': ('i', DIAER), 'ö': ('o', DIAER), 'ü': ('u', DIAER),
    'À': ('A', GRAVE), 'È': ('E', GRAVE), 'É': ('E', ACUTE), 'Ì': ('I', GRAVE),
    'Ò': ('O', GRAVE), 'Ù': ('U', GRAVE), 'Á': ('A', ACUTE), 'Í': ('I', ACUTE),
    'Ó': ('O', ACUTE), 'Ú': ('U', ACUTE),
}


def _rows(bitmap):
    return [int.from_bytes(bitmap[2 * i:2 * i + 2], 'little') for i in range(12)]


def _bitmap(rows):
    return b''.join(r.to_bytes(2, 'little') for r in rows)


def _accent(kind, cx):
    """(row offset, column) pixels of an accent centred on column cx, in a 2-row band."""
    return {GRAVE: [(0, cx - 1), (1, cx)], ACUTE: [(0, cx + 1), (1, cx)],
            CIRC: [(0, cx), (1, cx - 1), (1, cx + 1)], DIAER: [(1, cx - 1), (1, cx + 1)]}[kind]


def make(bitmap, kind, lower):
    rows = _rows(bitmap)
    if lower:                            # drop the i dot (anything above the x-height)
        rows = [0 if i < 4 else r for i, r in enumerate(rows)]
    used = [i for i, r in enumerate(rows) if r]
    if not used or (lower and used[0] < 3):
        return None
    cols = [x for r in rows for x in range(16) if r >> (15 - x) & 1]
    if kind == DIAER and len(set(cols)) == 1:
        cx = cols[0]                     # i: two dots need room either side
    else:
        cx = (min(cols) + max(cols) + 1) // 2
    cx = max(1, cx)
    top = used[0]
    if lower:                            # lower case: accent in rows 1-2
        band = 1
    else:                                # capital: squash the letter 2 rows down from the top
        body = rows[top:used[-1] + 1]
        n = len(body)
        drop = {n // 2 - 1, n // 2 + 1} if n >= 7 else set()
        body = [r for i, r in enumerate(body) if i not in drop]
        rows = [0] * 12
        start = used[-1] + 1 - len(body)
        rows[start:used[-1] + 1] = body
        band = start - 3 if start >= 3 else 0
    for dy, x in _accent(kind, cx):
        rows[band + dy] |= 1 << (15 - x)
    return _bitmap(rows)


def compose(base):
    """character -> (bitmap, width) for the accented letters whose base letter is in base."""
    out = {}
    for ch, (b, kind) in LETTERS.items():
        if b in base:
            bm, w = base[b]
            nb = make(bytes(bm) if not isinstance(bm, str) else bytes.fromhex(bm), kind, b.islower())
            if nb:
                out[ch] = (nb, w)
    return out


def show(bitmap, width):
    return '\n'.join(''.join('#' if r >> (15 - x) & 1 else '.' for x in range(width))
                     for r in _rows(bitmap))


if __name__ == '__main__':
    import json, os
    db = json.load(open(os.path.join(os.path.dirname(__file__), 'vp_glyph_db.json'), encoding='utf-8'))
    acc = compose({k: (bytes.fromhex(h), w) for k, (h, w) in db.items()})
    for group in ('àèéìòù', 'ÀÈÉÌÒÙ',
                  'âêîôû', 'äëïöü'):
        art = [show(*acc[c]).split('\n') for c in group]
        print('  '.join(c.ljust(len(a[0])) for c, a in zip(group, art)))
        for r in range(12):
            print('  '.join(a[r] for a in art))
        print()


# ---------------------------------------------------------------- the italic title font
#
# Menu titles ("Nuova partita", the camp sub-menu names) are drawn with a separate italic font,
# of which the disc has two identical copies (ITALIC_COPIES). In the title package (directory
# word 6) part 50 (type 19) is an SLZ-packed 4-bit TIM, a 256 x 128 sheet with no CLUT that the
# game puts at VRAM (896, 256); part 51 (type 20, raw) is the character table, 96 entries of 10 bytes for ASCII 0x20-0x7F:
#   w, h (sprite size), dx, dy (s16 offsets from the pen), advance, u, v (on the sheet), 0.
# A character with w == 0 has no glyph (drawn as a 12-pixel gap). The title code turns each menu
# byte into ASCII (+0x1F) and looks it up here, so a menu byte that shows an accented letter needs
# an entry for its slot's ASCII code. The glyphs are shaded: 1, 3, 4, 5 are the dark body,
# 7, 9, 0xB, 0xC the lighter shadow.

# (sheet part, table part) of each copy: the title package, and 2173 (the camp menu's, which the
# game loads for the camp menu; both copies are identical on the disc)
ITALIC_COPIES = {6: (50, 51), 2173: (39, 40)}
ITALIC_FREE_V = 0x48                    # the sheet is empty from this row down

_IT_ACCENT = {                          # 3-row accents, '.' = transparent; centre column 1
    GRAVE: ['35.', '.15', '..c'],
    ACUTE: ['..5', '.15', '3c.'],
    CIRC: ['.5.', '3.3', '...'],
    DIAER: ['...', '5.5', '...'],
}


def _tim_px(tim, x, y):
    v = tim[0x14 + y * 128 + x // 2]
    return (v >> 4) if x & 1 else (v & 15)


def _tim_put(tim, x, y, c):
    i = 0x14 + y * 128 + x // 2
    tim[i] = (tim[i] & 0x0F) | (c << 4) if x & 1 else (tim[i] & 0xF0) | c


def italic_entry(table, code):
    e = table[(code - 0x20) * 10:(code - 0x20) * 10 + 10]
    return dict(w=e[0], h=e[1], dx=int.from_bytes(e[2:4], 'little', signed=True),
                dy=int.from_bytes(e[4:6], 'little', signed=True), adv=e[6], u=e[7], v=e[8])


def _italic_glyph(tim, table, ch):
    """(rows of palette indices, dx, dy, advance) of the accented letter ch, drawn from its base
    letter's italic glyph, or None."""
    base, kind = LETTERS[ch]
    e = italic_entry(table, ord(base))
    if not e['w']:
        return None
    rows = [[_tim_px(tim, e['u'] + x, e['v'] + y) for x in range(e['w'])] for y in range(e['h'])]
    lower = base.islower()
    body_top = 3 if lower else 0                    # line row where the letter's body starts
    keep = [(e['dy'] + r, row) for r, row in enumerate(rows) if e['dy'] + r >= body_top]
    first = next((row for _, row in keep if any(row)), None)
    if first is None:
        return None
    cols = [x for x, c in enumerate(first) if c]
    cx = (min(cols) + max(cols)) // 2 + 1           # the slant puts the top to the right
    acc = _IT_ACCENT[kind]
    top = body_top - 4                              # accent rows top..top+2, a gap, then the body
    w = e['w']
    grid = {}
    for r, row in enumerate(acc):
        for x, ch_ in enumerate(row):
            if ch_ != '.':
                grid[(top + r, cx - 1 + x)] = int(ch_, 16)
    for lr, row in keep:
        for x, c in enumerate(row):
            if c:
                grid[(lr, x)] = c
    w = max(w, max(x for _, x in grid) + 1)
    w += -w % 4
    bottom = max(lr for lr, _ in grid)
    out = [[grid.get((lr, x), 0) for x in range(w)] for lr in range(top, bottom + 1)]
    return out, e['dx'], top, e['adv']


def italic_patch(tim, table, codes):
    """Draw the accented letters into the italic sheet and give them table entries.
    codes: {accented character: ASCII code of its slot}. Returns (tim, table) as bytes, and the
    characters it could not draw."""
    tim = bytearray(tim); table = bytearray(table); skipped = []
    u, v, line_h = 0, ITALIC_FREE_V, 0
    for ch, code in sorted(codes.items(), key=lambda kv: kv[1]):
        g = _italic_glyph(tim, table, ch) if ch in LETTERS else None
        if g is None:
            skipped.append(ch); continue
        rows, dx, dy, adv = g
        h, w = len(rows), len(rows[0])
        if u + w > 256:
            u, v, line_h = 0, v + line_h + 1, 0
        if v + h > 128:
            skipped.append(ch); continue
        for y, row in enumerate(rows):
            for x, c in enumerate(row):
                _tim_put(tim, u + x, v + y, c)
        table[(code - 0x20) * 10:(code - 0x20) * 10 + 10] = bytes([w, h]) + dx.to_bytes(2, 'little', signed=True) + \
            dy.to_bytes(2, 'little', signed=True) + bytes([adv, u, v, 0])
        u += w; line_h = max(line_h, h)
    return bytes(tim), bytes(table), skipped


# ---------------------------------------------------------------- the small italic font
#
# The camp menu's screens draw some labels (the Status screen's stat names and their explanations,
# the "Nome oggetto" column headings) with a small bold italic font in the same table format as the
# title font: the table, 96 entries of 10 bytes, is in the camp menu program (file 2174, one SLZ
# block loaded at 0x8002F824) at SMALL_TABLE_OFFSET; the glyphs are on a 256 x 256 4-bit sheet
# shared with the camp menu's icons and buttons, the TIM in part 0 of package 2173, which the game
# puts at VRAM (832, 256). Lower-case letters are 5 rows tall from line row 2, capitals 7 rows from
# row 0; colour 1 is the body, 4, 6 and 8 its edge, b and d the shadow. The sheet's only free room is
# a strip of SMALL_FREE (u, v, width, height), empty with a 3-pixel margin and never drawn over while
# the camp menu runs; an accented letter is drawn there and its slot's table entry pointed at it.

SMALL_SHEET = (2173, 0)                 # (package, part) of the sheet's TIM
SMALL_PROGRAM = 2174
SMALL_TABLE_OFFSET = 0x1E0D0
SMALL_FREE = (51, 33, 108, 12)

_SMALL_ACCENT = {                       # 2-row accents, '.' = transparent; centre column 1
    GRAVE: ['61.', '.1b'],
    ACUTE: ['.16', '1b.'],
    CIRC: ['.1.', '1b1'],
    DIAER: ['...', '1.1'],
}


def _small_tim(tim):
    """(offset of the pixel rows, bytes per row) in the sheet's TIM (flags 8: with a CLUT)."""
    clen = int.from_bytes(tim[8:12], 'little')
    off = 8 + clen
    w = int.from_bytes(tim[off + 8:off + 10], 'little')
    return off + 12, w * 2


def _small_glyph(tim, table, ch):
    """(rows of colour indices, dx, dy, advance) of the accented letter ch for the small font, from
    its base letter's glyph, or None."""
    base, kind = LETTERS[ch]
    e = italic_entry(table, ord(base))
    if not e['w']:
        return None
    po, pitch = _small_tim(tim)
    px = lambda x, y: (tim[po + y * pitch + x // 2] >> 4) if x & 1 else (tim[po + y * pitch + x // 2] & 15)
    rows = [[px(e['u'] + x, e['v'] + y) for x in range(e['w'])] for y in range(e['h'])]
    lower = base.islower()
    body_top = 2 if lower else 0
    keep = [(e['dy'] + r, row) for r, row in enumerate(rows) if e['dy'] + r >= body_top]   # drops the i's dot
    first = next((row for _, row in keep if any(row)), None)
    if first is None:
        return None
    cols = [x for x, c in enumerate(first) if c]
    cx = (min(cols) + max(cols)) // 2 + 1           # the slant puts the top to the right
    top = body_top - 2 if lower else body_top - 3   # capitals: a gap row between accent and letter
    grid = {}
    for r, row in enumerate(_SMALL_ACCENT[kind]):
        for x, c in enumerate(row):
            if c != '.':
                grid[(top + r, cx - 1 + x)] = int(c, 16)
    for lr, row in keep:
        for x, c in enumerate(row):
            if c:
                grid[(lr, x)] = c
    w = max(e['w'], max(x for _, x in grid) + 1)
    w += -w % 4
    bottom = max(lr for lr, _ in grid)
    return [[grid.get((lr, x), 0) for x in range(w)] for lr in range(top, bottom + 1)], e['dx'], top, e['adv']


def small_italic_patch(tim, table, codes):
    """Draw the accented letters codes ({character: slot ASCII}) into the free strip of the small
    italic font's sheet and point their slots' entries at them. Returns (tim, table, skipped)."""
    tim = bytearray(tim); table = bytearray(table); skipped = []
    po, pitch = _small_tim(tim)
    fu, fv, fw, fh = SMALL_FREE
    u = fu
    for ch, code in sorted(codes.items(), key=lambda kv: kv[1]):
        g = _small_glyph(tim, table, ch) if ch in LETTERS else None
        if g is None:
            skipped.append(ch); continue
        rows, dx, dy, adv = g
        h, w = len(rows), len(rows[0])
        if u + w > fu + fw or h > fh:
            skipped.append(ch); continue
        for y, row in enumerate(rows):
            for x, c in enumerate(row):
                i = po + (fv + y) * pitch + (u + x) // 2
                tim[i] = (tim[i] & 0x0F) | (c << 4) if (u + x) & 1 else (tim[i] & 0xF0) | c
        table[(code - 0x20) * 10:(code - 0x20) * 10 + 10] = bytes([w, h]) + dx.to_bytes(2, 'little', signed=True) + \
            dy.to_bytes(2, 'little', signed=True) + bytes([adv, u, fv, 0])
        u += w
    return bytes(tim), bytes(table), skipped
