#!/usr/bin/env python3
"""Valkyrie Profile battle text: read, edit and re-pack the four battle string tables.

The battle screens' English text is not in the menu tables (vp_menu_text.py) but in four string
tables of the battle files, each paired with a font part in the same file
(docs/VP_DIALOGUE_LOAD_PATH.md, "Battle text"):

  key      file / part (flags)          font part   text
  1500.7   1500 part 7 (0x00300900)     6           battle menu, elements, skill and attack names
  1500.9   1500 part 9 (0x00300910)     8           character and monster names
  1806.1   1806 part 1 (0x00300920)     0           item, equipment and spell names and help lines
  1988.2   1988 part 2 (0x00300920)     1           battle dialogue: tactical hints and boss lines

File 1501 holds byte-identical copies of 1500's two tables and fonts (its parts 9/8 and 11/10);
`build` writes every edit of 1500.7 / 1500.9 to both files.

Battle files use the second package layout: u32 part count, u32 0, (u32 flags, u32 size) per part,
then the parts back to back. Every table and font part is one SLZ1 entry.

A table has the menu-table layout (u32 text_offset = 4 + 8n, n x (u32 id, u32 offset), strings
ended by 0x00; ids sorted; not padded), but a string byte is a glyph number into the file's own
font, numbered in order of first use as in the scene fonts, and 0x80 is a new line (used only by
the 1806 help lines). The font has the scene / menu font layout: u32 n, u32 12, n widths,
n x 24-byte bitmaps, glyph b = index b - 1, and an even count, made even on the disc with blank
glyphs of width 0 at the end. Its glyphs are the menu font's (same bitmaps, same widths), so each
glyph's character is found by matching bitmaps with the menu font; the few that do not match
(Japanese placeholders, a ditto mark, a curly quote) are written as raw codes {xx}, except four
battle-only glyphs written as characters: the ellipsis \u2026, the corner quote \u300c that starts many
dialogue lines, the middle dot \u00b7 ("ATK 100\u00b7ATK# 3") and the curly apostrophe \u2019.

An edit can use any printable ASCII character and those four: one the battle font lacks is added
(from the menu font, or the bitmaps above), over the blank padding glyphs, and the font is padded back to an even count. Glyph numbers
stop at 0x7F (0x80 is the new line), so a font holds at most 127 glyphs; 1806's has 112.

The game decompresses each part into its own heap block, sized by the part's length (a mid-battle
RAM dump: the 1500.7 table at 0x8010E738 in a block of 6,677 + 16 bytes rounded up, its font at
0x8010DEEC), so longer tables and fonts are fine for the heap. What limits growth is the file's
sectors on the disc: 516 bytes (1988) to 1,028 bytes (1501) before re-compressing.

Message ids: 1806 uses the camp menu's item ids (2001-4023) and 662 of its strings are also in
the camp-menu tables with the same id and text; `build` changes those copies too (and a camp-menu
edit changes the battle copy) unless --no-link.

Edit-file syntax (as for the menu tables):
  @@ 0x07d1           the message id the text below belongs to
  plain characters    printable ASCII
  a line break        0x80
  {xx}                a raw glyph number in hex, for a glyph without a character
"""
import importlib.util
import os
import re
import struct

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


vmenu = _load('vp_menu_text')

HEADER_RE = re.compile(r'# Valkyrie Profile battle text (\d+\.\d+)')

# key: (file, table part, font part, description, [(copy file, table part, font part)])
BATTLE_TABLES = {
    '1500.7': (1500, 7, 6, 'battle menu, elements, skill and attack names', [(1501, 9, 8)]),
    '1500.9': (1500, 9, 8, 'character and monster names', [(1501, 11, 10)]),
    '1806.1': (1806, 1, 0, 'items, equipment and spells: names and help lines', []),
    '1988.2': (1988, 2, 1, 'battle dialogue: tactical hints and boss lines', []),
}
NEWLINE = 0x80
# glyphs of the battle fonts that are not in the menu font's ASCII range, written as these characters
SPECIAL = {
    '\u2026': ('0000000000000000c0ccc0cc000000000000000000000000', 12),   # ellipsis (1500.7, 1988.2)
    '\u300c': ('007800400040004000400040004000400040000000000000', 6),    # corner quote: a line's start (1988.2)
    '\u00b7': ('000000000000000000600060000000000000000000000000', 5),    # middle dot (1806.1, 1988.2)
    '\u2019': ('006000200040000000000000000000000000000000000000', 6),    # curly apostrophe (1806.1)
}
MAX_GLYPHS = 0x7F
REF_MENUS = (2176, 2179, 2183, 6)       # menu tables whose fonts give the ASCII glyphs

_REF = []


def ascii_glyphs(arc, dirw):
    """(bitmap -> character, character -> (bitmap, width)) from the menu fonts (index i is
    character 0x20 + i)."""
    if not _REF:
        by_bitmap = {}; by_char = {}
        for w in REF_MENUS:
            font = vmenu.MenuText(arc, dirw, w).font
            if not font:
                continue
            widths, bitmaps = font
            for i in range(min(0x5F, len(bitmaps))):
                c = chr(0x20 + i)
                by_bitmap.setdefault(bitmaps[i], c); by_char.setdefault(c, (bitmaps[i], widths[i]))
        # accented letters; a menu font may have them drawn over { } \\ ^ ... (vp_menu_text), and
        # those slots are not the ASCII characters
        acc = vmenu._load('vp_accents').compose(by_char)
        drawn = {bm for bm, _ in acc.values()}
        by_bitmap = {bm: c for bm, c in by_bitmap.items() if bm not in drawn}
        by_char = {c: v for c, v in by_char.items() if v[0] not in drawn}
        for c, (h, wd) in SPECIAL.items():
            by_bitmap.setdefault(bytes.fromhex(h), c); by_char.setdefault(c, (bytes.fromhex(h), wd))
        for c, (bm, wd) in acc.items():
            by_bitmap[bm] = c; by_char[c] = (bm, wd)
        _REF.append((by_bitmap, by_char))
    return _REF[0]


class BattleFile:
    """A battle file (second package layout), its parts editable in place."""

    def __init__(self, arc, dirw, word):
        ed = vmenu.editor()
        self.word = word
        self.first = ed.vsn.sector(dirw[word]); self.end = ed.vsn.file_end(dirw, word)
        start = self.first * ed.SECTOR
        count, zero = struct.unpack_from('<2I', arc, start)
        if zero != 0 or not 0 < count < 64:
            raise ValueError(f'directory word {word}: not a battle file (count {count}, {zero:#x})')
        self.parts = []; at = start + 8 + 8 * count
        for i in range(count):
            flags, size = struct.unpack_from('<2I', arc, start + 8 + 8 * i)
            self.parts.append([flags, arc[at:at + size]]); at += size
        self.orig_parts = [d for _, d in self.parts]
        self.used = at - start
        self.label = f'battle file {word}'

    def build(self):
        out = struct.pack('<2I', len(self.parts), 0)
        out += b''.join(struct.pack('<2I', f, len(d)) for f, d in self.parts)
        out += b''.join(d for _, d in self.parts)
        return out, (self.end - self.first) * vmenu.editor().SECTOR

    def changed(self):
        return [i for i, (_, d) in enumerate(self.parts) if d != self.orig_parts[i]]

    def repack(self, recompress_all=False):
        """(file bytes, room in its sectors). With recompress_all every SLZ part that was not
        edited is re-compressed too, to make room."""
        if recompress_all:
            ed = vmenu.editor()
            for i, (_, data) in enumerate(self.parts):
                if data == self.orig_parts[i] and data[:3] == b'SLZ':
                    ents = [list(e) for e in ed.read_container(data)]
                    for e in ents:
                        if e[0]:
                            e[1] = ed.vslz.compress(ed.unpack(*e), e[0])
                    self.parts[i][1] = ed.write_container(ents)
        return self.build()


class BattleText:
    """One battle string table and its font, with edits applied to a copy."""

    kind = 'battle'

    def __init__(self, files, arc, dirw, key):
        self.ed = vmenu.editor()
        self.key = key
        word, ti, fi, self.name, copies = BATTLE_TABLES[key]
        self.word = word
        for w in [word] + [c[0] for c in copies]:
            if w not in files:
                files[w] = BattleFile(arc, dirw, w)
        self.file = files[word]; self.ti, self.fi = ti, fi
        self.pkg = self.file
        self.copies = []
        for w, cti, cfi in copies:
            f = files[w]
            if f.parts[cti][1] == self.file.parts[ti][1] and f.parts[cfi][1] == self.file.parts[fi][1]:
                self.copies.append((f, cti, cfi))
            else:
                raise ValueError(f'battle text {key}: file {w} parts {cti}/{cfi} are not a copy any more')
        self.units = [self.file] + [f for f, _, _ in self.copies]
        tents = self.ed.read_container(self.file.parts[ti][1])
        fents = self.ed.read_container(self.file.parts[fi][1])
        if len(tents) != 1 or len(fents) != 1:
            raise ValueError(f'battle text {key}: expected one SLZ entry per part')
        self.tkind, self.fkind = tents[0][0], fents[0][0]
        self.raw = self.ed.unpack(*tents[0]); self.font_raw = self.ed.unpack(*fents[0])
        to = struct.unpack_from('<I', self.raw, 0)[0]
        if to < 12 or (to - 4) % 8 or to > len(self.raw):
            raise ValueError(f'battle text {key}: not a string table')
        self.text_offset = to
        self.pairs = [list(struct.unpack_from('<2I', self.raw, 4 + 8 * i)) for i in range((to - 4) // 8)]
        self.orig = [self.raw[to + o:self.raw.index(b'\0', to + o)] for _, o in self.pairs]
        self.strings = list(self.orig)
        n, rows = struct.unpack_from('<2I', self.font_raw, 0)
        if rows != 12 or len(self.font_raw) != 8 + 25 * n:
            raise ValueError(f'battle text {key}: part {fi} is not a font')
        self.widths = list(self.font_raw[8:8 + n])
        self.bitmaps = [self.font_raw[8 + n + 24 * i:8 + n + 24 * (i + 1)] for i in range(n)]
        by_bitmap, self.ref = ascii_glyphs(arc, dirw)
        used = {b for s in self.orig for b in s}
        blank = bytes(24)
        # the blank, zero-width glyphs at the end that no string uses only pad the count to even
        keep = n
        while keep and self.bitmaps[keep - 1] == blank and self.widths[keep - 1] == 0 and keep not in used:
            keep -= 1
        self.keep = keep
        self.chars = {}                 # glyph number -> character
        for i in range(keep):
            c = by_bitmap.get(self.bitmaps[i])
            if c is not None and not (c == ' ' and self.widths[i] == 0):
                self.chars[i + 1] = c
        self.codes = {}                 # character -> glyph number (the first one)
        for g, c in sorted(self.chars.items()):
            self.codes.setdefault(c, g)
        self.added = []                 # characters added to the font, in order (glyphs keep+1...)

    # -- text
    def decode(self, raw):
        out = []; group = []

        def flush():
            if group:
                out.append('{' + ' '.join(f'{b:02x}' for b in group) + '}'); group.clear()
        for b in raw:
            c = self.chars.get(b)
            if c is None and 0 <= b - self.keep - 1 < len(self.added):
                c = self.added[b - self.keep - 1]      # a glyph this edit added
            if b == NEWLINE:
                flush(); out.append('\n')
            elif c is not None and c not in '{}':
                flush(); out.append(c)
            else:
                group.append(b)
        flush()
        return ''.join(out)

    def encode(self, text, added=None):
        """Edit text -> glyph numbers. A character the font lacks is appended to `added` (a list
        of characters, default this table's own) and gets the next free glyph number."""
        added = self.added if added is None else added
        out = bytearray(); i = 0
        n = self.keep + len(added)
        while i < len(text):
            c = text[i]
            if c == '{':
                j = text.find('}', i)
                if j < 0:
                    raise ValueError(f'unclosed "{{" at {text[i:i + 12]!r}')
                try:
                    vals = bytes(int(h, 16) for h in text[i + 1:j].split())
                except ValueError:
                    raise ValueError(f'bad raw codes {text[i:j + 1]!r} (hex glyph numbers separated by spaces)')
                for v in vals:
                    if not (1 <= v <= len(self.bitmaps) or v == NEWLINE):
                        raise ValueError(f'{text[i:j + 1]!r}: {v:02x} is not a glyph of this font (01-{len(self.bitmaps):02x})')
                out += vals; i = j + 1; continue
            if c == '}':
                raise ValueError('a "}" outside a raw code')
            if c == '\n':
                out.append(NEWLINE)
            elif c in self.codes:
                out.append(self.codes[c])
            elif c in added:
                out.append(self.keep + 1 + added.index(c))
            elif c in self.ref:
                if self.keep + len(added) + 1 > MAX_GLYPHS:
                    raise ValueError(f'{c!r}: the font is full ({MAX_GLYPHS} glyphs; 0x80 is the new line)')
                added.append(c); out.append(self.keep + len(added))
            else:
                raise ValueError(f'{c!r} is not in the battle font (printable ASCII, accented letters and {"".join(SPECIAL)} only)')
            i += 1
        return bytes(out)

    def glyphs(self, added=None):
        """(widths, bitmaps) of the font with `added` (default this table's) appended."""
        added = self.added if added is None else added
        widths = self.widths[:self.keep] + [self.ref[c][1] for c in added]
        bitmaps = self.bitmaps[:self.keep] + [self.ref[c][0] for c in added]
        if len(widths) < len(self.widths):         # never fewer glyphs than on the disc
            widths += self.widths[len(widths):]; bitmaps += self.bitmaps[len(bitmaps):]
        if len(widths) % 2:
            widths.append(0); bitmaps.append(bytes(24))
        return widths, bitmaps

    @property
    def font(self):
        return self.glyphs()

    def width(self, s, added=None):
        widths = self.glyphs(added)[0]; best = cur = 0
        for b in s:
            if b == NEWLINE:
                best = max(best, cur); cur = 0
            elif 1 <= b <= len(widths):
                cur += widths[b - 1]
        return max(best, cur)

    # -- the same interface as vp_menu_text.MenuText
    def ids(self):
        return [m for m, _ in self.pairs]

    def index(self, mid):
        for k, (i, _) in enumerate(self.pairs):
            if i == mid:
                return k
        raise KeyError(mid)

    def text(self, mid):
        return self.decode(self.orig[self.index(mid)])

    def set_text(self, mid, text, only_if=None):
        """Give the entry mid (if its original text is only_if, when given) the text. Returns
        how many entries changed."""
        try:
            k = self.index(mid)
        except KeyError:
            if only_if is None:
                raise
            return 0
        if only_if is not None and self.decode(self.orig[k]) != only_if:
            return 0
        trial = list(self.added)
        new = self.encode(text, trial)
        if new == self.strings[k]:
            return 0
        self.added[:] = trial; self.strings[k] = new
        return 1

    def changed(self):
        return [self.pairs[k][0] for k in range(len(self.pairs)) if self.strings[k] != self.orig[k]]

    def build(self):
        """The new table: the index, then every string again in table order (the order the disc
        stores them in), each ending with a 0 byte; the bytes before the first string are kept.
        As in vp_menu_text, writing them all again keeps the table as small as its text. No padding
        (the disc tables have none)."""
        if self.strings == self.orig:
            return self.raw
        first = min(o for _, o in self.pairs)
        out = bytearray(self.raw[:self.text_offset + first])
        for k, ((mid, _), s) in enumerate(zip(self.pairs, self.strings)):
            struct.pack_into('<2I', out, 4 + 8 * k, mid, len(out) - self.text_offset)
            out += s + b'\0'
        return bytes(out)

    def build_font(self):
        if not self.added:
            return self.font_raw
        widths, bitmaps = self.glyphs()
        return struct.pack('<2I', len(widths), 12) + bytes(widths) + b''.join(bitmaps)

    def write(self):
        """Put the re-compressed table (and font, if it grew) into the file and its copies."""
        ed = self.ed
        if self.strings == self.orig:
            return
        raw = self.build()
        tpart = ed.write_container([[self.tkind, ed.vslz.compress(raw, self.tkind), len(raw)]])
        fpart = None
        if self.added:
            fr = self.build_font()
            fpart = ed.write_container([[self.fkind, ed.vslz.compress(fr, self.fkind), len(fr)]])
        for f, ti, fi in [(self.file, self.ti, self.fi)] + self.copies:
            f.parts[ti][1] = tpart
            if fpart:
                f.parts[fi][1] = fpart


def battle_tables(arc, dirw):
    """{key: BattleText}; tables in the same file share one BattleFile."""
    files = {}
    return {k: BattleText(files, arc, dirw, k) for k in BATTLE_TABLES}


EDIT_HELP = ['# Edit the text under any "@@ 0x.." line (a message id); delete the entries you do not change.',
             '# A line break is a new line (0x80). {xx} is a raw glyph number in hex (a glyph without a',
             '# character). Printable ASCII, accented letters (\u00e0 \u00e8 \u00e9 \u00ec \u00f2 \u00f9 \u00c8 ...) and',
             '# \u2026 \u300c \u00b7 \u2019; a character the font lacks is added to it.',
             '# `build` also changes the camp-menu tables that have the same id with the same original',
             '# text (item names), unless --no-link.']


def edit_file_text(t, entries=None):
    ids = t.ids() if entries is None else list(entries)
    lines = [f'# Valkyrie Profile battle text {t.key} ({t.name}): {len(t.ids())} messages, file {t.word} '
             f'sectors {t.file.first}..{t.file.end - 1} of VALKYRIE.BIN'] + EDIT_HELP + ['']
    for mid in ids:
        lines.append(f'@@ {mid:#06x}')
        lines.append(t.text(mid) if entries is None else entries[mid])
    return '\n'.join(lines) + '\n'


def is_battle_edit_file(path):
    with open(path, encoding='utf-8') as f:
        for line in f:
            if HEADER_RE.match(line):
                return True
            if not line.startswith('#'):
                return False
    return False


def read_edit_file(path):
    """(table key, {message id: text})."""
    key, recs = vmenu.read_records(path, HEADER_RE, lambda m: m.group(1))
    if key is None:
        raise SystemExit(f'{path}: no "# Valkyrie Profile battle text N.N" header line')
    if key not in BATTLE_TABLES:
        raise SystemExit(f'{path}: {key} is not one of the battle tables ({", ".join(BATTLE_TABLES)})')
    return key, recs
