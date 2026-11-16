#!/usr/bin/env python3
"""Valkyrie Profile menu and other non-dialogue text: read, edit and re-pack the 13 string tables.

Story dialogue lives in the scene packages (vp_text_editor.py), battle text in four tables of the
battle files (vp_battle_text.py). Everything else the player reads in
English - the title screen, the camp menu and its sub-menus (skill, item and equipment names and
descriptions, status labels, help lines, save/load messages) and the world map's place names - is in
13 string tables, one per menu module, found by scanning every non-scene file in VALKYRIE.BIN:

  directory word   package                                text
  6                title package (52 parts)               part 46 (type 4), 305 strings
  2175-2185        camp menu modules: part 0 = code       part 1 (type 4)
                   overlay (type 18), part 2 = glyph
                   width table (type 7)
  4734             world map graphics and text            part 2 (type 4), 41 strings

Each table is a type-4 part holding one SLZ entry (SLZ1 or SLZ2) that decompresses to:

  u32 text_offset                    = 4 + 8 * n
  n x (u32 message id, u32 offset)   offset relative to text_offset
  strings, each ended by 0x00

Message ids are per module: 0x64 is "New Game" in the title table and a place name on the world map,
but the camp-menu modules share most of theirs (an item name has the same id in every module that
shows it). Two tables list the ids 0x1A8E/0x1A8F ("[" / "]") twice, and the ids are not sorted
everywhere, so the table order is kept exactly. No two entries share bytes and none points into the
middle of a string.

String bytes (the menu font, one glyph per byte; the width table in each package's type-7 part):
  0x01-0x5F   ASCII 0x20-0x7E: the character is byte + 0x1F ("Skill" = 34 4C 4A 4D 4D)
  0x80        new line
  0x81 a b    a 3-byte command (a is 0x80 in all 568 uses; b = 1, 2, 6, 7; most labels such as
              "LV" / "DME" start with 81 80 07)
  0x60-0x7F,  extra glyphs (icons, rules, the "less than" strips of the skill help; their width
  0x82-0x85   entries differ between modules) - kept as raw codes

The menu font is the package's type-7 part (the world map: the text part's second SLZ entry), in the
scene-font layout: u32 n glyphs (100; 112 on the title screen), u32 12 rows, n width bytes, n 24-byte
bitmaps, glyph b at index b - 1. Menu items are drawn at fixed places (tested: "Use an Item" runs
into "Divine Item", "Evaluation" into its value), so `build` warns when an edit makes a string wider.

The game copies a table into a heap block of its decompressed size (seen in a camp-menu RAM dump: the
2175 table at 0x80102BC4 inside a block of 1,256 + 16 bytes), so a longer table gets a longer block.
What limits growth is the package's sectors on the disc, as for the scenes: re-compressing with
vp_slz gives 92 bytes (Configure, 2184) to 2.6 KB (2180) of room before any edit.

Edit-file syntax (the same idea as the dialogue editor's):
  @@ 0x0bc3           the message id the text below belongs to
  plain characters    printable ASCII
  a line break        0x80
  {xx xx ...}         raw bytes in hex: commands and extra glyphs, e.g. {81 80 07}; '{' and '}'
                      themselves are {5c} and {5e}
"""
import importlib.util
import os
import re
import struct

HERE = os.path.dirname(os.path.abspath(__file__))

MENU_FILES = {
    6: 'title screen and New Game menu',
    2175: 'camp menu: top level',
    2176: 'camp menu: Skill',
    2177: 'camp menu: Transfer',
    2178: 'camp menu: Ability',
    2179: 'camp menu: Use Item',
    2180: 'camp menu: Divine Item',
    2181: 'camp menu: Status',
    2182: 'camp menu: Party',
    2183: 'camp menu: Equipment',
    2184: 'camp menu: Configure',
    2185: 'camp menu: Data (save / load)',
    4734: 'world map',
}

HEADER_RE = re.compile(r'# Valkyrie Profile menu text (\d+)')


_ACC = []


def _accents():
    """accented character -> (bitmap, width), drawn from the menu font's typeface (vp_accents)."""
    if not _ACC:
        acc = _load('vp_accents')
        ed = editor()
        base = {k: (bytes.fromhex(h), w) for k, (h, w) in
                __import__('json').load(open(ed.GLYPH_DB, encoding='utf-8')).items()}
        _ACC.append(acc.compose(base))
    return _ACC[0]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


_ED = []


def editor():
    """vp_text_editor (packages, SLZ containers, the compressor), loaded once."""
    if not _ED:
        _ED.append(_load('vp_text_editor'))
    return _ED[0]


# Glyph slots an accented letter may take over in a menu font. Each of these characters appears in
# only one string per table (a list of the whole character set), so they can be redrawn.
ACCENT_SLOTS = (0x5C, 0x5E, 0x3D, 0x3F, 0x40, 0x41, 0x5D, 0x21, 0x1D, 0x1F)    # { } \ ^ _ ` | @ < >


def decode(raw, slots=None):
    """String bytes -> edit text. slots: byte -> character of a redrawn glyph slot."""
    out = []; group = []; slots = slots or {}

    def flush():
        if group:
            out.append('{' + ' '.join(f'{b:02x}' for b in group) + '}'); group.clear()
    i = 0
    while i < len(raw):
        b = raw[i]
        if b == 0x81:
            flush(); group.extend(raw[i:i + 3]); flush(); i += 3; continue
        if b == 0x80:
            flush(); out.append('\n')
        elif b in slots:
            flush(); out.append(slots[b])
        elif 0x01 <= b <= 0x5F and b not in (0x5C, 0x5E):
            flush(); out.append(chr(b + 0x1F))
        else:
            group.append(b)
        i += 1
    flush()
    return ''.join(out)


def encode(text, slots=None):
    """Edit text -> string bytes. slots: character -> byte of a redrawn glyph slot. Raises
    ValueError for what the menu font cannot show."""
    out = bytearray(); i = 0; slots = slots or {}
    while i < len(text):
        c = text[i]
        if c == '{':
            j = text.find('}', i)
            if j < 0:
                raise ValueError(f'unclosed "{{" at {text[i:i + 12]!r}')
            try:
                vals = bytes(int(h, 16) for h in text[i + 1:j].split())
            except ValueError:
                raise ValueError(f'bad raw codes {text[i:j + 1]!r} (hex bytes separated by spaces)')
            if not vals or 0 in vals:
                raise ValueError(f'{text[i:j + 1]!r}: empty, or contains 00 (the string terminator)')
            out += vals; i = j + 1; continue
        if c == '}':
            raise ValueError('a "}" outside a raw code; write {5e} for the character')
        if c == '\n':
            out.append(0x80)
        elif c in slots:
            out.append(slots[c])
        elif 0x20 <= ord(c) <= 0x7E:
            out.append(ord(c) - 0x1F)
        else:
            raise ValueError(f'{c!r} is not in the menu font (printable ASCII and accented letters only)')
        i += 1
    return bytes(out)


class MenuText:
    """One string table, with edits applied to a copy; repack() gives the new package.

    The battle tables (vp_battle_text.BattleText) have the same interface: key, kind, name, ids,
    index, text, decode, encode, set_text, width, font, orig / strings / pairs, changed, units
    (what to re-pack) and write (put the edits into those units)."""

    kind = 'menu'

    def __init__(self, arc, dirw, word):
        self.ed = editor()
        self.word = word
        self.key = str(word)
        self.name = MENU_FILES.get(word, '?')
        self.pkg = self.ed.Package(arc, dirw, word)
        self.units = [self]
        self.label = f'menu table {word}'
        self.first, self.end = self.pkg.first, self.pkg.end
        parts = [i for i, (kind, _) in enumerate(self.pkg.parts) if kind == 4]
        if len(parts) != 1:
            raise ValueError(f'directory word {word}: expected one text part, found {len(parts)}')
        self.pi = parts[0]
        self.ents = self.ed.read_container(self.pkg.parts[self.pi][1])
        if len(self.ents) != 1 and word != 4734:
            raise ValueError(f'directory word {word}: {len(self.ents)} SLZ entries')
        self.raw = self.ed.unpack(*self.ents[0])
        to = struct.unpack_from('<I', self.raw, 0)[0]
        if to < 12 or (to - 4) % 8 or to > len(self.raw):
            raise ValueError(f'directory word {word}: not a string table')
        self.text_offset = to
        self.pairs = [list(struct.unpack_from('<2I', self.raw, 4 + 8 * i)) for i in range((to - 4) // 8)]
        self.orig = [self._string(o) for _, o in self.pairs]
        self.strings = list(self.orig)
        self.orig_parts = [bytes(d) for _, d in self.pkg.parts]
        self.font_at = None             # ('ent', i) or ('part', i): where the font is
        self.font = self._font()
        self.added = []                 # accented letters drawn over ACCENT_SLOTS, in order
        self.slot_of = {}               # character -> slot byte of an accent already in the font
        # character -> slot byte, shared by all the menu tables of a build (menu_tables), so a
        # letter has the same slot everywhere: the italic title font is one for all menus
        self.registry = {}
        self.italic = None              # table 6 only: {character: slot ASCII} to draw in the
                                        # italic title font (vp_accents.italic_patch)
        if self.font:
            acc = {bm: c for c, (bm, _) in _accents().items()}
            for b in ACCENT_SLOTS:
                c = acc.get(self.font[1][b - 1])
                if c:
                    self.slot_of[c] = b

    def _font(self):
        """The package's menu font: (widths, [24-byte bitmaps]), or None."""
        raws = [(('ent', i), self.ed.unpack(*e)) for i, e in enumerate(self.ents) if i]
        for i, (kind, data) in enumerate(self.pkg.parts):
            if kind == 7 and data[:3] == b'SLZ':
                raws.append((('part', i), self.ed.unpack(*self.ed.read_container(data)[0])))
        for at, r in raws:
            n, rows = struct.unpack_from('<2I', r, 0)
            if rows == 12 and len(r) >= 8 + n * 25:
                self.font_at = at; self.font_raw = r
                return list(r[8:8 + n]), [r[8 + n + 24 * i:8 + n + 24 * (i + 1)] for i in range(n)]
        return None

    def _slots(self, added=None):
        """character -> slot byte for the accents in the font plus those in added. A new letter
        gets the slot the shared registry gives it, or else the first slot nobody uses (the
        registry is only written by set_text). Raises ValueError when no slot is left."""
        added = self.added if added is None else added
        out = dict(self.slot_of)
        for c in added:
            if c in out:
                continue
            if c in self.registry:
                out[c] = self.registry[c]; continue
            taken = set(self.registry.values()) | set(out.values())
            free = [b for b in ACCENT_SLOTS if b not in taken]
            if not free:
                raise ValueError(f'no free glyph slot for {c!r}: the menus hold at most '
                                 f'{len(ACCENT_SLOTS)} different accented letters')
            out[c] = free[0]
        return out

    def glyphs(self, added=None):
        """(widths, bitmaps) with the accents in added drawn over their slots."""
        widths, bitmaps = list(self.font[0]), list(self.font[1])
        acc = _accents()
        for c, b in self._slots(added).items():
            bitmaps[b - 1], widths[b - 1] = acc[c]
        return widths, bitmaps

    def font_bytes(self):
        """The font part's decompressed bytes with the added accents."""
        widths, bitmaps = self.glyphs()
        n = len(widths)
        return self.font_raw[:8] + bytes(widths) + b''.join(bitmaps) + self.font_raw[8 + 25 * n:]

    def width(self, s, added=None):
        """Pixel width of the widest line of string bytes s (sum of glyph widths)."""
        if not self.font:
            return 0
        widths = self.glyphs(added)[0]; best = cur = 0; i = 0
        while i < len(s):
            b = s[i]
            if b == 0x81:
                i += 3; continue
            if b == 0x80:
                best = max(best, cur); cur = 0
            elif 1 <= b <= len(widths):
                cur += widths[b - 1]
            i += 1
        return max(best, cur)

    def _string(self, off):
        a = self.text_offset + off
        return self.raw[a:self.raw.index(b'\0', a)]

    def ids(self):
        """Message ids in table order, each once."""
        seen = []; s = set()
        for i, _ in self.pairs:
            if i not in s:
                s.add(i); seen.append(i)
        return seen

    def text(self, mid):
        return self.decode(self.orig[self.index(mid)])

    def index(self, mid):
        for k, (i, _) in enumerate(self.pairs):
            if i == mid:
                return k
        raise KeyError(mid)

    def set(self, mid, new, only_if=None):
        """Give every entry with id mid (and, if only_if is given, that original text) the bytes
        `new`. Returns how many entries changed."""
        n = 0
        for k, (i, _) in enumerate(self.pairs):
            if i == mid and (only_if is None or self.orig[k] == only_if) and self.strings[k] != new:
                self.strings[k] = new; n += 1
        return n

    def decode(self, raw):
        return decode(raw, {b: c for c, b in self._slots().items()})

    def encode(self, text, added=None):
        """Edit text -> bytes. An accented letter not yet in the font takes the next free slot,
        appended to added (default: this table's own list)."""
        added = self.added if added is None else added
        if self.font:
            acc = _accents()
            for c in dict.fromkeys(text):
                if c in acc and c not in self.slot_of and c not in added:
                    added.append(c)
        return encode(text, self._slots(added))

    def set_text(self, mid, text, only_if=None):
        """set() with edit text; only_if is the original text. Returns entries changed (0 when
        the table has no message mid)."""
        if only_if is not None:
            try:
                only_if = encode(only_if, self._slots())
            except ValueError:
                return 0                # no string of this table can have that text
            if not any(i == mid and s == only_if for (i, _), s in zip(self.pairs, self.orig)):
                return 0
        trial = list(self.added)
        new = self.encode(text, trial)
        self.added[:] = trial
        for c, b in self._slots().items():
            self.registry.setdefault(c, b)
        return self.set(mid, new, only_if)

    @property
    def used(self):
        return self.pkg.used

    def write(self):
        pass                            # repack() reads the strings itself

    def changed(self):
        return sorted({self.pairs[k][0] for k in range(len(self.pairs)) if self.strings[k] != self.orig[k]})

    def glyph_note(self):
        """'' or what the build changes in the font."""
        if not self.added:
            return ''
        return 'font: ' + ', '.join(f'{c} over {chr(b + 0x1F)!r}' for c, b in self._slots().items()
                                     if c in self.added)

    def build(self):
        """The new table. Unchanged strings keep their bytes and offsets; a changed one is written
        over the old one when it fits there (the rest of the old bytes are zeroed), otherwise
        appended after the last string. The table is padded to 4 bytes as on the disc."""
        if self.strings == self.orig:
            return self.raw
        out = bytearray(self.raw)
        end = max(self.text_offset + o + len(s) + 1 for (_, o), s in zip(self.pairs, self.orig))
        del out[end:]
        for k, ((mid, off), old, new) in enumerate(zip(self.pairs, self.orig, self.strings)):
            if new == old:
                continue
            a = self.text_offset + off
            if len(new) <= len(old):
                out[a:a + len(old)] = new + bytes(len(old) - len(new))
            else:
                off = len(out) - self.text_offset
                out += new + b'\0'
            struct.pack_into('<2I', out, 4 + 8 * k, mid, off)
        out += bytes(-len(out) % 4)
        return bytes(out)

    def repack(self, recompress_all=False):
        """(package bytes, room in its sectors)."""
        ents = [list(e) for e in self.ents]
        raw = self.build()
        if raw != self.raw or recompress_all:
            ents[0] = [ents[0][0], self.ed.vslz.compress(raw, ents[0][0]), len(raw)]
        if self.added:
            fb = self.font_bytes(); where, i = self.font_at
            if where == 'ent':
                ents[i] = [ents[i][0], self.ed.vslz.compress(fb, ents[i][0]), len(fb)]
            else:
                sub = [list(x) for x in self.ed.read_container(self.pkg.parts[i][1])]
                sub[0] = [sub[0][0], self.ed.vslz.compress(fb, sub[0][0]), len(fb)]
                self.pkg.parts[i][1] = self.ed.write_container(sub)
        if self.italic:
            _patch_italic(self.ed, self.pkg, self.orig_parts, self.word, self.italic)
        if recompress_all:
            for e in ents[1:]:
                r = self.ed.unpack(*e); e[1] = self.ed.vslz.compress(r, e[0])
            for i, (kind, data) in enumerate(self.pkg.parts):
                if i != self.pi and data[:3] == b'SLZ':
                    sub = [list(x) for x in self.ed.read_container(data)]
                    for x in sub:
                        r = self.ed.unpack(*x); x[1] = self.ed.vslz.compress(r, x[0])
                    self.pkg.parts[i][1] = self.ed.write_container(sub)
        self.pkg.parts[self.pi][1] = self.ed.write_container(ents)
        return self.pkg.build()


def _patch_italic(ed, pkg, orig_parts, word, codes):
    """Draw the accented letters codes ({character: slot ASCII}) into the italic title font copy
    in package pkg (directory word word)."""
    acc = _load('vp_accents')
    si, ti = acc.ITALIC_COPIES[word]
    sub = [list(x) for x in ed.read_container(orig_parts[si])]
    tim = ed.unpack(*sub[0])
    tim, table, _ = acc.italic_patch(tim, orig_parts[ti], codes)
    sub[0] = [sub[0][0], ed.vslz.compress(tim, sub[0][0]), len(tim)]
    pkg.parts[si][1] = ed.write_container(sub)
    pkg.parts[ti][1] = table


class ItalicFont:
    """A package that holds a copy of the italic title font and no menu table (2173); a unit for
    build_tables like MenuText."""

    def __init__(self, arc, dirw, word, codes):
        self.ed = editor()
        self.word = word
        self.pkg = self.ed.Package(arc, dirw, word)
        self.orig_parts = [bytes(d) for _, d in self.pkg.parts]
        self.codes = codes
        self.label = f'italic title font {word}'
        self.first, self.end = self.pkg.first, self.pkg.end

    @property
    def used(self):
        return self.pkg.used

    def repack(self, recompress_all=False):
        _patch_italic(self.ed, self.pkg, self.orig_parts, self.word, self.codes)
        if recompress_all:
            for i, (kind, data) in enumerate(self.pkg.parts):
                if data[:3] == b'SLZ':
                    sub = [list(x) for x in self.ed.read_container(data)]
                    for x in sub:
                        r = self.ed.unpack(*x); x[1] = self.ed.vslz.compress(r, x[0])
                    self.pkg.parts[i][1] = self.ed.write_container(sub)
        return self.pkg.build()


EDIT_HELP = ['# Edit the text under any "@@ 0x.." line (a message id); delete the entries you do not change.',
             '# A line break is a new line (0x80). {xx ...} are raw bytes: {81 80 07} and the like are',
             '# commands, {60}-{7f} and {82}-{85} extra glyphs; { and } themselves are {5c} and {5e}.',
             '# Printable ASCII is in the menu font; accented letters (à è é ì ò ù È ...) are drawn over',
             '# the { } \\ ^ _ ` | @ < > glyphs, up to 10 per table. `build` also changes every other menu table',
             '# (and the battle item table 1806.1) that has the same id with the same original text',
             '# (an item name is in several), unless --no-link is given. A "# no-link" line up here',
             '# keeps this file\'s edits to this table, and they win over a linked copy.']


def edit_file_text(mt, entries=None):
    ids = mt.ids() if entries is None else list(entries)
    lines = [f'# Valkyrie Profile menu text {mt.word} ({MENU_FILES.get(mt.word, "?")}): {len(mt.ids())} '
             f'messages, package sectors {mt.pkg.first}..{mt.pkg.end - 1} of VALKYRIE.BIN'] + EDIT_HELP + ['']
    for mid in ids:
        lines.append(f'@@ {mid:#06x}')
        lines.append(mt.text(mid) if entries is None else entries[mid])
    return '\n'.join(lines) + '\n'


def is_menu_edit_file(path):
    with open(path, encoding='utf-8') as f:
        for line in f:
            if HEADER_RE.match(line):
                return True
            if not line.startswith('#'):
                return False
    return False


def read_records(path, header_re, keyfn):
    """(key from the header line, or None; {message id: text}) of an edit file."""
    key = None; recs = {}; cur = None; body = []
    for line in open(path, encoding='utf-8').read().split('\n'):
        if cur is None and line.startswith('#'):
            m = header_re.match(line)
            if m:
                key = keyfn(m)
            continue
        m = re.fullmatch(r'@@ (0x[0-9a-fA-F]+|\d+)', line)
        if m:
            if cur is not None:
                recs[cur] = '\n'.join(body)
            cur = int(m.group(1), 0); body = []
        elif cur is not None:
            body.append(line)
    if cur is not None:
        recs[cur] = '\n'.join(body)
        if recs[cur].endswith('\n'):
            recs[cur] = recs[cur][:-1]
    return key, recs


def no_link_file(path):
    """True when the edit file's header has a "# no-link" line: its edits change only its own
    table (for a string that must differ from its copies, such as a title in another font)."""
    for line in open(path, encoding='utf-8'):
        if line.startswith('@@'):
            return False
        if line.strip() == '# no-link':
            return True
    return False


def read_edit_file(path):
    """(directory word, {message id: text})."""
    word, recs = read_records(path, HEADER_RE, lambda m: int(m.group(1)))
    if word is None:
        raise SystemExit(f'{path}: no "# Valkyrie Profile menu text N" header line')
    if word not in MENU_FILES:
        raise SystemExit(f'{path}: {word} is not one of the menu tables ({", ".join(map(str, MENU_FILES))})')
    return word, recs


def wider(tables):
    """[(id, old text, new text, old px, new px, [table keys])] for changed strings that got wider.
    tables: {key: MenuText or BattleText}."""
    out = {}
    for key, t in tables.items():
        for k, (mid, _) in enumerate(t.pairs):
            old, new = t.orig[k], t.strings[k]
            if old != new and t.width(new) > t.width(old):
                ot, nt = t.decode(old), t.decode(new)
                out.setdefault((mid, ot, nt), [mid, ot, nt, t.width(old), t.width(new), []])
                out[(mid, ot, nt)][5].append(key)
    return list(out.values())


def apply_edits(tables, key, recs, link=True, origin=''):
    """Apply {id: text} to tables[key] and, with link, to every other table that has the id with the
    same original text (an item name is in several camp-menu tables and the battle item table).
    tables: {key: MenuText or BattleText}. Returns ([(key, id, entries changed)], [notes])."""
    t = tables[key]; done = []; notes = []
    for mid, text in sorted(recs.items()):
        try:
            k = t.index(mid)
        except KeyError:
            raise ValueError(f'{origin}{t.kind} table {key} has no message {mid:#x}')
        old = t.decode(t.orig[k])
        try:
            n = t.set_text(mid, text)
        except ValueError as e:
            raise ValueError(f'{origin}message {mid:#x}: {e}')
        if t.strings[k] == t.orig[k]:
            continue
        if n:
            done.append((key, mid, n))
        if not link:
            continue
        for k2, other in tables.items():
            if k2 == key:
                continue
            if other.kind != t.kind and ('{' in text or '{' in old):
                # raw codes mean different glyphs in the menu and battle fonts
                try:
                    if other.text(mid) == old:
                        notes.append(f'{other.kind} table {k2} also has message {mid:#x} "{old}"; not changed '
                                     f'there because the text has raw codes {{..}}')
                except KeyError:
                    pass
                continue
            try:
                n = other.set_text(mid, text, only_if=old)
            except ValueError as e:
                notes.append(f'{other.kind} table {k2} also has message {mid:#x} "{old}"; not changed there: {e}')
                continue
            if n:
                done.append((k2, mid, n))
    return done, notes
