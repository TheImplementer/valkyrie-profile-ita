"""The Configure screen's hint boxes ("[pad] or [pad] to pick"), fitted to their translated text.

The Configure program is part 0 of package 2184, the package that also holds its string table. It
draws each hint at the top right of the screen from a list of 20-byte layout records, the list
ending in a record whose first byte is 0xFF:

    +0  type (1 text, 5 button icon, 7 box), then 08 00 00
    +4  u32: the text's message id, or the icon number
    +12 s16 x, s16 y
    +16 u16 width, u16 height (a box)

Every hint box ends at x 456, left of the scrollbar. A button icon inside a hint does not belong to
the text: it is drawn at its own x, over a run of blank glyphs ({61} in this table) that the English
leaves for it. So a translation moves the gap but not the icon, and a longer one runs out of the
box. `fit` puts each icon back over its gap (centred, when its English place would run past the gap:
the English "Press desired button [pad] to end" hides its "to" that way) and sizes the box to the
text, MARGIN pixels after its last glyph: wider to the left when the text is longer (as the English
does for its longer hints, which cover the screen title's labels), narrower when it is shorter,
moving the whole hint with it. The right edge stays at 456. The build does this whenever table 2184
changes; a hint whose text is unchanged keeps its English layout.

A glyph is drawn 2 pixels wider than its font width (checked on screen against "imposta" and
"per scegliere").
"""
import struct

WORD = 2184                 # the package: Configure's program (part 0) and its string table
PROGRAM_PART = 0
REC = 20
TEXT, ICON, BOX = 1, 5, 7
GAP = 2                     # pixels between glyphs
MARGIN = 6                  # tx + text width + MARGIN = the box's right edge: 8 px of space on screen, as on the left
MIN_X = 8
ICON_W = {28: 30, 29: 30}   # the pad icons, measured on screen; other icons keep their English place


def _blank_w(table, blank):
    widths = table.glyphs()[0]
    return widths[blank - 1] + GAP


def lists(prog):
    """[[record offsets]] for each list that ends in a box and holds one text record."""
    out = []
    for off in range(0, len(prog) - 2 * REC, 4):
        if prog[off] != BOX or prog[off + 1:off + 4] != b'\x08\0\0' or prog[off + REC] != 0xFF:
            continue
        start = off
        while start >= REC and prog[start - REC] in (TEXT, ICON) and prog[start - REC + 1:start - REC + 4] == b'\x08\0\0':
            start -= REC
        recs = list(range(start, off + REC, REC))
        if sum(prog[r] == TEXT for r in recs) == 1:
            out.append(recs)
    return out


def _glyph_xs(table, raw):
    """[(x of each glyph from the text's start, byte)] and the drawn width."""
    widths = table.glyphs()[0]; out = []; x = 0; i = 0
    while i < len(raw):
        b = raw[i]
        if b == 0x81:
            i += 3; continue
        if b == 0x80:
            break                   # the hints are one line
        out.append((x, b))
        x += (widths[b - 1] if 1 <= b <= len(widths) else 0) + GAP
        i += 1
    return out, x


def _runs(glyphs, blank):
    """[(x of the run's first glyph, x after its last)] for each run of blank glyphs."""
    out = []
    for k, (x, b) in enumerate(glyphs):
        if b != blank:
            continue
        if k and glyphs[k - 1][1] == blank:
            out[-1] = (out[-1][0], x)
        else:
            out.append((x, x))
    return out


def fit(prog, table, blank=0x61):
    """prog with every changed hint fitted; returns (new prog, [notes])."""
    prog = bytearray(prog); notes = []
    for recs in lists(prog):
        t = next(r for r in recs if prog[r] == TEXT)
        mid, = struct.unpack_from('<I', prog, t + 4)
        try:
            k = table.index(mid)
        except KeyError:
            continue
        en, it = table.orig[k], table.strings[k]
        if en == it:
            continue
        tx, = struct.unpack_from('<h', prog, t + 12)
        g_en, w_en = _glyph_xs(table, en); g_it, w_it = _glyph_xs(table, it)
        r_en, r_it = _runs(g_en, blank), _runs(g_it, blank)
        moved = []
        for r in recs:
            if prog[r] != ICON:
                continue
            x, = struct.unpack_from('<h', prog, r + 12)
            # the gap under the icon in the English
            n = next((j for j, (a, b) in enumerate(r_en) if tx + a - 4 <= x <= tx + b + 4), None)
            if n is None:
                continue                # an icon outside the text
            if n >= len(r_it):
                notes.append(f'hint {mid:#x}: the translation has {len(r_it)} gaps for icons, the English {len(r_en)}')
                continue
            offset = x - (tx + r_en[n][0])
            gap = r_it[n][1] - r_it[n][0] + _blank_w(table, blank)
            w = ICON_W.get(struct.unpack_from('<I', prog, r + 4)[0])
            if w and offset + w > gap:
                # the English icon overlaps the next word ("Press desired button [pad] (to) end"):
                # centre it in the gap instead
                offset = max(0, (gap - w) // 2)
                if w > gap:
                    notes.append(f'hint {mid:#x}: icon {w} px wide in a gap of {gap}; add a blank to the text')
            nx = tx + r_it[n][0] + offset
            if nx != x:
                struct.pack_into('<h', prog, r + 12, nx); moved.append((x, nx))
        b = recs[-1]
        bx, = struct.unpack_from('<h', prog, b + 12); bw, = struct.unpack_from('<H', prog, b + 16)
        need = tx + w_it + MARGIN - (bx + bw)        # > 0 widen, < 0 narrow
        if need:
            if bx - need < MIN_X:
                raise ValueError(f'Configure hint {mid:#x}: the text needs a box {bw + need} px wide, '
                                 f'more than the screen has ({bx + bw - MIN_X})')
            for r in recs:
                x, = struct.unpack_from('<h', prog, r + 12)
                struct.pack_into('<h', prog, r + 12, x - need)
            struct.pack_into('<H', prog, b + 16, bw + need)
        if moved or need:
            notes.append(f'hint {mid:#x}: '
                         + (f'box {bw} -> {bw + need} px wide' if need else f'box {bw} px wide')
                         + (f'; icon x ' + ', '.join(f'{a} -> {c}' for a, c in moved) if moved else ''))
    return bytes(prog), notes


def patch(ed, table, log=print):
    """Fit the hints in table's package (a vp_menu_text.MenuText of package 2184) to its strings."""
    pkg = table.pkg
    ents = [list(e) for e in ed.read_container(pkg.parts[PROGRAM_PART][1])]
    prog = ed.unpack(*ents[0])
    new, notes = fit(prog, table)
    for n in notes:
        log(f'  Configure {n}')
    if new != prog:
        ents[0] = [ents[0][0], ed.vslz.compress(new, ents[0][0]), len(new)]
        pkg.parts[PROGRAM_PART][1] = ed.write_container(ents)
