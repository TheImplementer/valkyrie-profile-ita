#!/usr/bin/env python3
"""Valkyrie Profile text editor: edit story dialogue and menu text, and write a patched copy of the cue/bin.

Usage:
    python3 vp_text_editor.py export DISC.cue --scene 0x16 [-o scene_016.txt]
    python3 vp_text_editor.py export DISC.cue --menu 2176 [-o menu_2176.txt]
    python3 vp_text_editor.py export DISC.cue --battle 1806.1 [-o battle_1806.1.txt]
    python3 vp_text_editor.py export DISC.cue --camp-tabs [-o camp_tabs.txt]
    python3 vp_text_editor.py menus  DISC.cue
    python3 vp_text_editor.py find   DISC.cue "How nostalgic"
    python3 vp_text_editor.py build  DISC.cue EDIT.txt [EDIT.txt ...] -o OUT.cue [--no-link]
    python3 vp_text_editor.py check  DISC.cue [--scene 0x16 ...] [--menus]

Menu text (title screen, camp menu and sub-menus, item / skill / equipment names and descriptions,
save / load messages, world-map place names) is in 13 string tables, not in the scenes: see
vp_menu_text.py for the format. `export --menu` writes one table as an edit file ("@@ 0x.." = message
id); `build` takes scene and menu edit files together. A menu edit also changes the other tables that
hold the same message id with the same text (an item name is in several sub-menus) unless --no-link.

Battle text (the battle menu, skill and attack names, character and monster names, item names and
help lines in battle, Lenneth's battle hints and boss lines) is in 4 more string tables in the battle
files, with glyph numbers into their own fonts: see vp_battle_text.py. `export --battle 1806.1` and
the like write them as edit files of the same kind; `menus` lists all 17 tables. Battle item names
are linked with the camp-menu tables the same way.

The camp menu's Topic and Sort tabs are boxes of a fixed width in the camp menu program (file 2174),
drawn from a layout record: `export --camp-tabs` writes their widths, and `build` widens a box for a
longer label (vp_camp_tabs.py).

`export` writes a scene's text records to an editable file; change the text of any record and pass
the file to `build`, which writes OUT.cue + OUT.bin: a copy of the image with the edited scenes
re-packed. The original image is only read. Records you delete from the file, or leave as they
were, are kept byte for byte. `check` proves the round trip on the disc: every record parses back
to itself, every scene block and container rebuilds, and re-compressed packages still fit.

The whole path, all of it from docs/VP_DIALOGUE_LOAD_PATH.md:

  cue/bin --ISO 9660--> VALKYRIE.BIN (Mode 2 Form 1 sectors; vp_disc.py reseals EDC/ECC)
  archive header (13 sectors, LFSR-encrypted) --> directory word 3610 + scene id --> package
  package = u32 count, u32 header size, (type, size) pairs, parts; part type 4 = text container
  text container = SLZ entry 0 (type 1): scene block, SLZ entry 1 (type 2): scene font
  scene block = 7-word header, event script, u16 text index, records (hdr[3] count, hdr[4] bytes)
  record = glyph codes into the scene's own font + 2-byte commands, ended by 0x00

The package is read as the sectors between its directory word and the next one, so a rebuilt
package must fit there. vp_slz.py compresses better than the game's own tool, so a re-packed
container usually shrinks even with some text added; `build` reports the room left.

Two rules learned by running edited discs in TinyPSX:
  * a font's glyph count must stay even (the renderer reads bitmaps, which start at 8 + n, as
    halfwords); added glyphs are padded with a blank one;
  * the message box size is pushed by the script, not taken from the text: a longer line wraps at
    the box edge and extra lines scroll the box. `build` reports this per edited record, and
    `build --fit-boxes` rewrites the width/height pushes so the box fits the new text.

Edit file syntax (one record per "@@ N" line, N = record number in the scene):
  plain characters      glyphs, looked up in the scene's font by bitmap (vp_glyph_names.json); a
                        character the font lacks is added to the font from another scene's glyph
  a line break          command 0 (new line in the box)
  <page>                command 1 (wait for a button, new page); a line break right after it is
                        only layout and is ignored
  <N> / <N:hex>         any other command N with its argument bytes, e.g. <17:7800> = auto-advance
                        after 0x78 frames, <12:1809> = voice clip 0x918, <4:02> colour
  {xx}                  a raw glyph code (hex), for a glyph without a known character
"""
import argparse
import collections
import difflib
import importlib.util
import json
import os
import re
import struct
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


vst = _load('vp_scene_text')
vsn = _load('vp_scenes')
vslz = _load('vp_slz')
vdisc = _load('vp_disc')
vtitle = _load('vp_title_menu')
vmenu = _load('vp_menu_text')
vbattle = _load('vp_battle_text')
vwm = _load('vp_worldmap_text')
vtabs = _load('vp_camp_tabs')

SECTOR = 2048
ARG_BYTES = vst.ARG_BYTES
COMMAND_NAMES = {0: 'line break', 1: 'page', 3: 'typing delay', 4: 'colour', 5: 'centre line', 6: 'queue portrait',
                 7: 'width scale', 8: 'height scale', 12: 'voice clip', 14: 'insert value',
                 17: 'auto-advance frames', 18: 'start voice clip', 19: 'show portrait (0100 left, 0000 right)'}
GLYPH_DB = os.path.join(HERE, 'vp_glyph_db.json')


def align4(n):
    return (n + 3) & ~3


# ---------------------------------------------------------------- fonts

class Font:
    """u32 n, u32 12, n width bytes, n x 24-byte bitmaps; glyph code c = index c - 1."""

    def __init__(self, raw, names):
        self.n, rows = struct.unpack_from('<2I', raw)
        assert rows == 12 and len(raw) == 8 + 25 * self.n, 'not a scene font'
        self.widths = list(raw[8:8 + self.n])
        self.bitmaps = [raw[8 + self.n + 24 * i: 8 + self.n + 24 * (i + 1)] for i in range(self.n)]
        self.names = names
        self.added = []

    def char(self, code):
        if 1 <= code <= self.n:
            return self.names.get(self.bitmaps[code - 1].hex())
        return None

    def codes(self, ch):
        return [i + 1 for i, b in enumerate(self.bitmaps) if self.names.get(b.hex()) == ch]

    def add(self, bitmap, width):
        self.bitmaps.append(bitmap); self.widths.append(width); self.n += 1
        self.added.append(self.n)
        return self.n

    def raw(self):
        return struct.pack('<2I', self.n, 12) + bytes(self.widths) + b''.join(self.bitmaps)


# ---------------------------------------------------------------- records <-> text

def tokens(rec, font):
    """A record's bytes (without the 0x00 end) as (text, bytes) tokens."""
    out = []; i = 0
    while i < len(rec):
        b = rec[i]
        if b & 0x80:
            v = (b & 0x7F) | (rec[i + 1] << 7)
            if v < 0x4000:
                ch = font.char(v)
                out.append((ch if ch and ch not in '<>{}\\' else f'{{{v:02x}}}', rec[i:i + 2])); i += 2
                continue
            cmd = v - 0x4000; k = ARG_BYTES.get(cmd, 0)
            raw = rec[i:i + 2 + k]; args = rec[i + 2:i + 2 + k]; i += 2 + k
            text = '\n' if cmd == 0 else '<page>' if cmd == 1 else f'<{cmd}' + (f':{args.hex()}>' if k else '>')
            out.append((text, raw))
        else:
            ch = font.char(b)
            out.append((ch if ch and ch not in '<>{}\\' else f'{{{b:02x}}}', rec[i:i + 1])); i += 1
    return out


def render(toks):
    return ''.join(t + '\n' if t == '<page>' else t for t, _ in toks)


TOKEN = re.compile(r'<page>\n?|<(\d+)(?::([0-9a-fA-F]*))?>|\{([0-9a-fA-F]+)\}|\n|.', re.S)


def parse(text):
    """Edited text -> token texts (the same strings tokens() makes)."""
    out = []
    for m in TOKEN.finditer(text):
        s = m.group(0)
        if s.startswith('<page>'):
            out.append('<page>')
        elif m.group(1) is not None:
            cmd = int(m.group(1)); args = m.group(2) or ''
            if cmd in (0, 1):
                out.append('\n' if cmd == 0 else '<page>'); continue
            k = ARG_BYTES.get(cmd, 0)
            if len(args) != 2 * k:
                raise ValueError(f'command <{cmd}> takes {k} argument byte(s), got {s!r}')
            out.append(f'<{cmd}:{args.lower()}>' if k else f'<{cmd}>')
        elif m.group(3) is not None:
            out.append(f'{{{int(m.group(3), 16):02x}}}')
        else:
            if s in '<>{}\\':
                raise ValueError(f'stray {s!r} (commands are <N> or <N:hex>, raw glyphs {{xx}})')
            out.append(s)
    return out


def _glyph_bytes(code):
    return bytes([code]) if code < 0x80 else bytes([0x80 | (code & 0x7F), code >> 7])


def encode(new_toks, old_toks, font, prefer, db):
    """Bytes for an edited record. Runs of tokens the edit left alone keep their original bytes
    (so glyph variants, e.g. the second set of capitals, stay as they were); new characters use
    the scene's most-used code for that character, or a glyph added to the font."""
    old_text = [t for t, _ in old_toks]
    sm = difflib.SequenceMatcher(None, old_text, new_toks, autojunk=False)
    out = bytearray()
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == 'equal':
            out += b''.join(raw for _, raw in old_toks[a0:a1]); continue
        for t in new_toks[b0:b1]:
            out += encode_token(t, font, prefer, db)
    return bytes(out)


def encode_token(t, font, prefer, db):
    if t == '\n':
        return b'\x80\x80'                       # 0x4000 + 0
    if t == '<page>':
        return b'\x81\x80'
    m = re.fullmatch(r'<(\d+)(?::([0-9a-f]*))?>', t)
    if m:
        v = 0x4000 + int(m.group(1))
        return bytes([0x80 | (v & 0x7F), v >> 7]) + bytes.fromhex(m.group(2) or '')
    m = re.fullmatch(r'\{([0-9a-f]+)\}', t)
    if m:
        return _glyph_bytes(int(m.group(1), 16))
    if t in prefer:
        return _glyph_bytes(prefer[t])
    codes = font.codes(t)
    if not codes:
        if t not in db:
            raise ValueError(f'no glyph for {t!r} in any scene font')
        bitmap, width = db[t]
        code = font.add(bytes.fromhex(bitmap), width)
        if not getattr(font, 'quiet', False):
            print(f'    font: added {t!r} as glyph {code:#x}', file=sys.stderr)
        codes = [code]
    prefer[t] = codes[0]
    return _glyph_bytes(codes[0])


# ---------------------------------------------------------------- scene block

class Block:
    def __init__(self, raw):
        self.hdr = list(struct.unpack_from('<7I', raw))
        index = 0x1C + self.hdr[0]; count = self.hdr[3]; text = index + 2 * count
        self.script = bytearray(raw[:index])          # header + event script
        offs = struct.unpack_from(f'<{count}H', raw, index)
        self.records = []
        for o in offs:
            j = text + o
            # a record ends at a 0x00 in glyph position (command arguments may be 0x00)
            i = j
            while raw[i] != 0:
                if raw[i] & 0x80:
                    v = (raw[i] & 0x7F) | (raw[i + 1] << 7)
                    i += 2 + (ARG_BYTES.get(v - 0x4000, 0) if v >= 0x4000 else 0)
                else:
                    i += 1
            self.records.append(raw[j:i])
        self.tail = raw[text + self.hdr[4]:]          # anything after the records (always empty)
        self.raw = raw

    def build(self):
        offs = []; text = bytearray()
        for r in self.records:
            offs.append(len(text)); text += r + b'\0'
        if len(text) > 0xFFFF:
            raise ValueError('text over 64 KB: the index offsets are u16')
        hdr = list(self.hdr); hdr[4] = len(text)
        return struct.pack('<7I', *hdr) + self.script[0x1C:] + struct.pack(f'<{len(offs)}H', *offs) + bytes(text) + self.tail


# ---------------------------------------------------------------- message boxes

# Library message wrappers: the script pushes x, y, width, height, record and a sixth value (for
# 0x3E2: frames to wait after the box is dismissed) and calls one.
# The box size comes from these pushes, not from the text: over the 3,417 literal calls on the disc
# the width is at least the widest line (font widths, in pixels) in 99%, and the height is exactly
# 14 pixels per line (per page) in 91%. Seen live in TinyPSX: a line wider than the box wraps at the
# box edge, and more lines than the box holds scroll it up a line at a time (the game relies on that
# in the other 9%). So `build` measures each edited record against its calls, and --fit-boxes grows
# the pushed width and height so the edit shows the way the original line did.
BOX_FUNCS = {0x3E2: 'func_03e2', 0x33C: 'func_033c', 0x3F5: 'func_03f5', 0x408: 'func_0408'}
LINE_PX = 14
SCREEN_W = 320


def measure(rec, font):
    """(widest line in pixels, height in pixels of the tallest page) of a record, measured the way
    the layout pass (0x80052C3C) does: a glyph advances by its width byte times the width scale
    (command 7), and a line is 12 x the largest height scale on it (command 8) + 2 pixels tall."""
    width = line = 0; page = 0; tallest = 0; lh = 0; hs = vs = 1; i = 0

    def end_line():
        nonlocal width, line, page, lh
        width = max(width, line); page += lh or 12 * vs + 2; line = 0; lh = 0
    while i < len(rec):
        b = rec[i]
        if b & 0x80:
            v = (b & 0x7F) | rec[i + 1] << 7
            if v >= 0x4000:
                c = v - 0x4000; k = ARG_BYTES.get(c, 0)
                if c == 0:
                    end_line()
                elif c == 1:
                    end_line(); tallest = max(tallest, page); page = 0
                elif c == 7:
                    hs = rec[i + 2] or 1
                elif c == 8:
                    vs = rec[i + 2] or 1
                i += 2 + k; continue
            code = v; i += 2
        else:
            code = b; i += 1
        line += font.widths[code - 1] * hs if 0 < code <= font.n else 0
        lh = max(lh, 12 * vs + 2)
    end_line()
    return width, max(tallest, page)


def box_calls(script):
    """record -> [(word index of the width push, of the height push, [x, y, w, h, record, time])]"""
    W = struct.unpack_from(f'<{(len(script) - 0x1C) // 4}I', script, 0x1C)
    starts = vsn.vsc.sweep(W); out = collections.defaultdict(list)
    for n, i in enumerate(starts):
        if W[i] >> 24 != 0x14 or (W[i] & 0x7FFFFF) not in BOX_FUNCS or n < 6:
            continue
        prev = starts[n - 6:n]
        if all(W[j] >> 24 == 0x12 for j in prev):
            vals = [W[j] & 0x7FFFFF for j in prev]
            out[vals[4]].append((prev[2], prev[3], vals, W[i] & 0x7FFFFF))
    return out


def fitted(w, h, px, ph):
    """The box size --fit-boxes gives text of px x ph pixels. The wrap test is strict (a line fits
    when its width is LESS than the box width: of 3,417 calls none is exactly as wide as its widest
    line, 45 are one pixel wider), so the width gets a small margin."""
    return (px + 6 if w <= px else w), (ph if h < ph else h)


def set_push(script, word, value):
    struct.pack_into('<I', script, 0x1C + 4 * word, 0x12 << 24 | value)


def fit_boxes(scene, k, fit):
    """Check record k's boxes; with fit, grow the pushed width/height. Returns report lines."""
    px, ph = measure(scene.block.records[k], scene.font)
    calls = box_calls(scene.block.script).get(k, [])
    if not calls:
        return [f'record {k}: {px} x {ph} px; no literal box call found, size not checked']
    notes = []
    for iw, ih, (x, y, w, h, _, _), func in calls:
        need_w, need_h = fitted(w, h, px, ph)
        if (need_w, need_h) == (w, h):
            notes.append(f'record {k}: {px} x {ph} px fits its box {w} x {h} ({BOX_FUNCS[func]} at word {iw + 4:#x})')
            continue
        if fit:
            set_push(scene.block.script, iw, need_w); set_push(scene.block.script, ih, need_h)
            notes.append(f'record {k}: box {w} x {h} -> {need_w} x {need_h} for {px} x {ph} px'
                         f' ({BOX_FUNCS[func]} at word {iw + 4:#x})'
                         + (f'; note: x {x} places the box next to an actor, so whether it stays on the screen'
                            f' can only be checked in the game' if 8 <= x <= 15 else
                            f'; WARNING: x {x} + width {need_w} runs off the {SCREEN_W}-pixel screen' if x + need_w > SCREEN_W - 16 else ''))
        else:
            notes.append(f'record {k}: note: {px} x {ph} px is bigger than its box {w} x {h}'
                         f' ({BOX_FUNCS[func]} at word {iw + 4:#x}): the game will '
                         + ' and '.join(x for x, c in (('wrap it at the box edge', w <= px), ('scroll the box', h < ph)) if c)
                         + '; --fit-boxes grows the box instead')
    return notes


# ---------------------------------------------------------------- containers and packages

def read_container(part):
    """[(kind, compressed payload, uncompressed size)] and the bytes after the last entry."""
    ents = []; at = 0
    while part[at:at + 3] == b'SLZ':
        kind = part[at + 3]; clen, ulen, step = struct.unpack_from('<3I', part, at + 4)
        ents.append([kind, part[at + 16:at + 16 + clen], ulen])
        if step == 0:
            break
        at += step
    return ents


def unpack(kind, payload, ulen):
    return payload[:ulen] if kind == 0 else vst.slz1(payload, ulen) if kind == 1 else vst.slz2(payload, ulen)


def write_container(ents):
    out = bytearray()
    for k, (kind, payload, ulen) in enumerate(ents):
        last = k == len(ents) - 1
        size = align4(16 + len(payload))
        out += b'SLZ' + bytes([kind]) + struct.pack('<3I', len(payload), ulen, 0 if last else size)
        out += payload + bytes(size - 16 - len(payload))
    return bytes(out)


class Package:
    def __init__(self, arc, dirw, word):
        self.word = word
        self.first = vsn.sector(dirw[word]); self.end = vsn.file_end(dirw, word)
        start = self.first * SECTOR
        count, hsize = struct.unpack_from('<2I', arc, start)
        assert hsize == 8 + 8 * count
        self.parts = []; at = start + hsize
        for i in range(count):
            kind, size = struct.unpack_from('<2I', arc, start + 8 + 8 * i)
            self.parts.append([kind, arc[at:at + size]]); at += size
        self.used = at - start

    def text_part(self):
        for i, (kind, data) in enumerate(self.parts):
            if kind == 4 and vst.is_scene(data, 0):
                return i
        return None

    def build(self):
        out = struct.pack('<2I', len(self.parts), 8 + 8 * len(self.parts))
        out += b''.join(struct.pack('<2I', k, len(d)) for k, d in self.parts)
        out += b''.join(d for _, d in self.parts)
        room = (self.end - self.first) * SECTOR
        return out, room


# ---------------------------------------------------------------- the scene being edited

class Scene:
    def __init__(self, arc, dirw, scene, names):
        self.id = scene; self.word = vsn.SCENE_BASE + scene
        self.pkg = Package(arc, dirw, self.word)
        self.pi = self.pkg.text_part()
        if self.pi is None:
            raise SystemExit(f'scene {scene:#x} has no text container')
        self.ents = read_container(self.pkg.parts[self.pi][1])
        self.block = Block(unpack(*self.ents[0]))
        self.font = Font(unpack(*self.ents[1]), names)
        self.orig_records = list(self.block.records)
        self.orig_script = bytes(self.block.script)
        self.orig_font_n = self.font.n

    def tokens(self, k):
        return tokens(self.orig_records[k], self.font)

    def text(self, k):
        return render(self.tokens(k))

    def preferred_codes(self):
        """The code each character uses most in this scene's records."""
        use = collections.defaultdict(collections.Counter)
        for r in self.orig_records:
            for t, raw in tokens(r, self.font):
                if len(t) == 1 and t != '\n':
                    code = raw[0] if len(raw) == 1 else (raw[0] & 0x7F) | raw[1] << 7
                    use[t][code] += 1
        return {ch: c.most_common(1)[0][0] for ch, c in use.items()}

    def preview(self, k, text, db):
        """What `build` would write for record k with this text, without changing the scene:
        (record bytes, {added glyph code: (bitmap hex, width)}). Raises ValueError like build."""
        font = Font(self.font.raw(), self.font.names)
        old = self.tokens(k); new = parse(text)
        if new == [t for t, _ in old]:
            return self.orig_records[k], {}
        rec = encode(new, old, font, self.preferred_codes(), db)
        return rec, {c: (font.bitmaps[c - 1].hex(), font.widths[c - 1]) for c in font.added}

    def repack(self, recompress_all=False):
        """New package bytes and the room in its sectors."""
        ents = [list(e) for e in self.ents]
        block_changed = self.block.records != self.orig_records or self.block.script != self.orig_script
        font_changed = self.font.n != self.orig_font_n
        if block_changed or recompress_all:
            raw = self.block.build(); ents[0] = [1, vslz.compress(raw, 1), len(raw)]
        if font_changed and self.font.n % 2:
            # The bitmaps start at 8 + n and the renderer reads them as halfwords (lhu at
            # 0x80052500): an odd n puts them at odd addresses and the game dies on an address
            # error. Every one of the 1,118 fonts on the disc has an even n; pad with a blank glyph.
            self.font.add(bytes(24), 0)
        if font_changed or recompress_all:
            raw = self.font.raw(); ents[1] = [ents[1][0], vslz.compress(raw, ents[1][0]), len(raw)]
        self.pkg.parts[self.pi][1] = write_container(ents)
        return self.pkg.build()


# ---------------------------------------------------------------- glyph database

def accented(db):
    """character -> (bitmap hex, width) of the accented letters drawn from db's letters (vp_accents)."""
    acc = _load('vp_accents').compose({k: (bytes.fromhex(h), w) for k, (h, w) in db.items()})
    return {c: (bm.hex(), w) for c, (bm, w) in acc.items()}


def glyph_db(arc, dirw, names):
    """character -> (bitmap hex, width) of its most common bitmap across all scene fonts, plus
    the accented letters."""
    if not os.path.exists(GLYPH_DB):
        _build_glyph_db(arc, dirw, names)
    db = {k: tuple(v) for k, v in json.load(open(GLYPH_DB, encoding='utf-8')).items()}
    for c, v in accented(db).items():
        db.setdefault(c, v)
        names.setdefault(v[0], c)       # so an added accented letter reads back as itself
    return db


def _build_glyph_db(arc, dirw, names):
    """Write GLYPH_DB: each character's most common bitmap and width across all scene fonts."""
    print('building the glyph database from every scene font (once)...', file=sys.stderr)
    seen = collections.Counter()        # (bitmap, width): a blank bitmap comes with several widths
    for k in range(vsn.SCENE_COUNT):
        try:
            pkg = Package(arc, dirw, vsn.SCENE_BASE + k)
        except (AssertionError, struct.error):
            continue
        pi = pkg.text_part()
        if pi is None:
            continue
        ents = read_container(pkg.parts[pi][1])
        f = Font(unpack(*ents[1]), names)
        seen.update((b.hex(), w) for b, w in zip(f.bitmaps, f.widths))
    best = {}
    for (h, w), n in seen.most_common():
        ch = names.get(h)
        if ch and ch not in best:
            best[ch] = (h, w)
    json.dump(best, open(GLYPH_DB, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)


# ---------------------------------------------------------------- commands

def open_disc(cue):
    disc = vdisc.Disc(cue)
    arc = disc.archive()
    names = vst.glyph_names()
    if os.path.exists(GLYPH_DB):        # so an accented letter an edit added reads back as itself
        for c, (h, _) in accented(json.load(open(GLYPH_DB, encoding='utf-8'))).items():
            names.setdefault(h, c)
    return disc, arc, vsn.directory(arc), names


def place_of(scene):
    try:
        m = json.load(open(os.path.join(HERE, 'vp_scene_map.json'), encoding='utf-8'))
        return ' | '.join(m[scene].get('place') or []) or '-'
    except (OSError, IndexError, KeyError):
        return '-'


EDIT_HELP = ['# Edit the text under any "@@ N" line; delete records you do not change (or leave them).',
             '# A line break is command 0; <page> is command 1. Other commands: <N> / <N:hex args>:',
             '#   ' + ', '.join(f'{k} {v}' for k, v in COMMAND_NAMES.items() if k > 1),
             '# {xx} is a raw glyph code. Records 0-13 are the shared system strings.']


def edit_file_text(scene, place, recs, note=''):
    """An edit file (the format `export` writes and `build` reads) for {record: text}."""
    lines = [f'# Valkyrie Profile scene {scene:#x} ({place}){note}'] + EDIT_HELP + ['']
    for k in sorted(recs):
        lines.append(f'@@ {k}'); lines.append(recs[k])
    return '\n'.join(lines) + '\n'


def cmd_export(a):
    disc, arc, dirw, names = open_disc(a.disc)
    if a.title:
        out = vtitle.edit_file_text()
        if a.output:
            open(a.output, 'w', encoding='utf-8').write(out); print(f'wrote {a.output}')
        else:
            sys.stdout.write(out)
        return
    if a.menu is not None:
        if a.menu not in vmenu.MENU_FILES:
            raise SystemExit(f'--menu must be one of {", ".join(map(str, vmenu.MENU_FILES))} (see `menus`)')
        out = vmenu.edit_file_text(vmenu.MenuText(arc, dirw, a.menu))
        if a.output:
            open(a.output, 'w', encoding='utf-8').write(out); print(f'wrote {a.output}')
        else:
            sys.stdout.write(out)
        return
    if a.battle is not None:
        if a.battle not in vbattle.BATTLE_TABLES:
            raise SystemExit(f'--battle must be one of {", ".join(vbattle.BATTLE_TABLES)} (see `menus`)')
        out = vbattle.edit_file_text(vbattle.battle_tables(arc, dirw)[a.battle])
        if a.output:
            open(a.output, 'w', encoding='utf-8').write(out); print(f'wrote {a.output}')
        else:
            sys.stdout.write(out)
        return
    if a.system:
        out = system_edit_file_text(Scene(arc, dirw, 0, names))
        if a.output:
            open(a.output, 'w', encoding='utf-8').write(out); print(f'wrote {a.output}')
        else:
            sys.stdout.write(out)
        return
    if a.camp_tabs:
        f = vtitle.TitleFile(types.SimpleNamespace(**globals()), arc, dirw, vtabs.WORD)
        t = menu_tables(arc, dirw)[2179]
        out = vtabs.edit_file_text(f.data, {m: re.sub(r'\{[^}]*\}', '', t.text(m)) for m in t.ids()})
        if a.output:
            open(a.output, 'w', encoding='utf-8').write(out); print(f'wrote {a.output}')
        else:
            sys.stdout.write(out)
        return
    if a.worldmap:
        out = vwm.edit_file_text(vwm.WorldMapText(types.SimpleNamespace(**globals()), arc, dirw, names))
        if a.output:
            open(a.output, 'w', encoding='utf-8').write(out); print(f'wrote {a.output}')
        else:
            sys.stdout.write(out)
        return
    if a.scene is None:
        raise SystemExit('export needs --scene, --menu, --battle, --worldmap, --system, --title or --camp-tabs')
    s = Scene(arc, dirw, a.scene, names)
    out = edit_file_text(a.scene, place_of(a.scene), {k: s.text(k) for k in range(len(s.orig_records))},
                         f': {len(s.orig_records)} records, directory word {s.word}, '
                         f'package sectors {s.pkg.first}..{s.pkg.end - 1} of VALKYRIE.BIN')
    if a.output:
        open(a.output, 'w', encoding='utf-8').write(out); print(f'wrote {a.output}')
    else:
        sys.stdout.write(out)


def read_edit_file(path):
    scene = None; recs = {}; cur = None; body = []
    for line in open(path, encoding='utf-8').read().split('\n'):
        if cur is None and line.startswith('#'):
            m = re.match(r'# Valkyrie Profile scene (0x[0-9a-f]+)', line)
            if m:
                scene = int(m.group(1), 16)
            continue
        m = re.fullmatch(r'@@ (\d+)', line)
        if m:
            if cur is not None:
                recs[cur] = '\n'.join(body)
            cur = int(m.group(1)); body = []
        elif cur is not None:
            body.append(line)
    if cur is not None:
        recs[cur] = '\n'.join(body)
    # the line break that ends the file belongs to no record
    if cur is not None and recs[cur].endswith('\n'):
        recs[cur] = recs[cur][:-1]
    if scene is None:
        raise SystemExit(f'{path}: no "# Valkyrie Profile scene 0x.." header line')
    return scene, recs


SYSTEM_HEADER = '# Valkyrie Profile system messages'
SYSTEM_RECORDS = 14


def is_system_edit_file(path):
    try:
        with open(path, encoding='utf-8') as f:
            return f.readline().startswith(SYSTEM_HEADER)
    except (OSError, UnicodeDecodeError):
        return False


def read_system_edit_file(path):
    """{record: text} of a system-messages edit file (records 0-13, the same in every scene)."""
    recs = {}; cur = None; body = []
    for line in open(path, encoding='utf-8').read().split('\n'):
        if cur is None and line.startswith('#'):
            continue
        m = re.fullmatch(r'@@ (\d+)', line)
        if m:
            if cur is not None:
                recs[cur] = '\n'.join(body)
            cur = int(m.group(1)); body = []
            if cur >= SYSTEM_RECORDS:
                raise SystemExit(f'{path}: record {cur}: the system messages are records 0-{SYSTEM_RECORDS - 1}')
        elif cur is not None:
            body.append(line)
    if cur is not None:
        recs[cur] = '\n'.join(body)
        if recs[cur].endswith('\n'):
            recs[cur] = recs[cur][:-1]
    return recs


def system_edit_file_text(s):
    """The system-messages edit file `export --system` writes, from scene s's records 0-13."""
    lines = [f'{SYSTEM_HEADER}: records 0-{SYSTEM_RECORDS - 1}, the same in every scene\'s text block',
             '# `build` writes each edited message into every scene that has the original (1,118 on disc 1).',
             '# A scene edit file that also lists one of these records wins for its scene.',
             '# The engine draws them in boxes of a fixed size (none grows): a line must be narrower than',
             '#   2, 7: 280 px   3: 236 x 5 lines   4: 172 x 4   5: 224 x 3   6: 204 x 3   8: 240',
             '#   9, 10: about 130 (centred in 192, beside the D-pad arrow)   11: 140 x 3   13: 96',
             '#   (a <14:..> number needs about 35 px more)',
             '# Same syntax as a scene edit file.', '']
    for k in range(min(SYSTEM_RECORDS, len(s.orig_records))):
        lines += [f'@@ {k}', s.text(k)]
    return '\n'.join(lines) + '\n'


def with_system(arc, dirw, names, edits, system):
    """edits plus an entry for every scene whose records 0-13 are the usual system messages, so
    that the system edits reach every scene. A scene edit file's own records win."""
    if not system:
        return edits, set()
    mine = collections.defaultdict(list)
    for origin, scene, recs in edits:
        mine[scene].append((origin, recs))
    ref = None; out = []; quiet = set()
    for k in range(vsn.SCENE_COUNT):
        try:
            s = Scene(arc, dirw, k, names)
        except (SystemExit, AssertionError, struct.error):
            continue
        head = s.orig_records[:SYSTEM_RECORDS]
        ref = ref or head
        recs = dict(system) if head == ref else {}
        origins = [o for o, _ in mine.get(k, [])]
        for _, r in mine.get(k, []):
            recs.update(r)
        if recs:
            out.append((', '.join(origins) or 'system messages', k, recs))
            if not origins:
                quiet.add(k)
    for scene in mine:                   # an edit file for a scene with no text: let build report it
        if not any(sc == scene for _, sc, _ in out):
            out += [(o, scene, r) for o, r in mine[scene]]
    return out, quiet


class EditFiles:
    """Edit files sorted by kind, read the way `build`, the release script and the progress
    report read them."""

    def __init__(self, paths):
        self.edits = []; self.table_edits = []; self.title = None; self.worldmap = {}; self.system = {}
        self.camp_tabs = {}
        for path in paths:
            if vtitle.is_title_edit_file(path):
                self.title = vtitle.read_edit_file(path)
            elif vwm.is_edit_file(path):
                self.worldmap.update(vwm.read_edit_file(path))
            elif vtabs.is_edit_file(path):
                self.camp_tabs.update(vtabs.read_edit_file(path))
            elif is_system_edit_file(path):
                self.system.update(read_system_edit_file(path))
            elif vmenu.is_menu_edit_file(path):
                word, recs = vmenu.read_edit_file(path)
                self.table_edits.append((path, str(word), recs, vmenu.no_link_file(path)))
            elif vbattle.is_battle_edit_file(path):
                key, recs = vbattle.read_edit_file(path)
                self.table_edits.append((path, key, recs, vmenu.no_link_file(path)))
            else:
                scene, recs = read_edit_file(path)
                self.edits.append((path, scene, recs))

    def build_args(self):
        return dict(table_edits=self.table_edits, title=self.title, worldmap=self.worldmap, system=self.system,
                    camp_tabs=self.camp_tabs)


def apply_edits(s, recs, db_get, origin=''):
    """Encode {record: edited text} into scene s. Returns the changed record numbers."""
    prefer = s.preferred_codes(); changed = []
    for k, text in sorted(recs.items()):
        if not 0 <= k < len(s.orig_records):
            raise ValueError(f'{origin}scene {s.id:#x} has no record {k}')
        old = s.tokens(k)
        try:
            new = parse(text)
        except ValueError as e:
            raise ValueError(f'{origin}record {k}: {e}')
        if new == [t for t, _ in old]:
            continue
        try:
            s.block.records[k] = encode(new, old, s.font, prefer, db_get())
        except ValueError as e:
            raise ValueError(f'{origin}record {k}: {e}')
        changed.append(k)
    return changed


def _patch_sectors(arc, first, end, pkg, room, patches):
    pkg = pkg + bytes(room - len(pkg))
    for i in range(end - first):
        sec = pkg[i * SECTOR:(i + 1) * SECTOR]
        k = first + i
        if sec != arc[k * SECTOR:(k + 1) * SECTOR]:
            patches[k] = sec


def menu_tables(arc, dirw):
    """The menu tables, sharing one accent-slot registry (seeded from the slots already drawn)."""
    out = {w: vmenu.MenuText(arc, dirw, w) for w in vmenu.MENU_FILES}
    registry = {}
    for t in out.values():
        for c, b in t.slot_of.items():
            registry.setdefault(c, b)
    for t in out.values():
        t.registry = registry
    return out


def text_tables(arc, dirw):
    """Every non-dialogue string table: {'2175': MenuText, ..., '1806.1': BattleText, ...}."""
    out = {str(w): t for w, t in menu_tables(arc, dirw).items()}
    out.update(vbattle.battle_tables(arc, dirw))
    return out


def table_title(t):
    return f'{t.kind} table {t.key} ({t.name})'


def build_tables(arc, dirw, table_edits, link, patches, log=print):
    """table_edits: [(origin label, table key, {message id: text}[, no_link])], keys as in
    text_tables. An entry with no_link set changes only its own table, and is applied after the
    others so it wins over a linked copy. Adds sector patches; returns the number of tables changed."""
    if not table_edits:
        return 0
    tables = text_tables(arc, dirw); links = collections.defaultdict(list)
    ordered = sorted(table_edits, key=lambda e: bool(e[3:] and e[3]))
    for origin, key, recs, *own in ordered:
        done, notes = vmenu.apply_edits(tables, key, recs, link and not (own and own[0]), f'{origin}: ')
        for k, mid, n in done:
            if k != key:
                links[k].append(mid)
        for n in notes:
            log(f'  note: {n}')
    for mid, old, new, px0, px1, ks in vmenu.wider(tables):
        log(f'  note: message {mid:#x} "{new}" is {px1} px wide, was {px0} ("{old}"; tables '
            f'{", ".join(ks)}): menu and battle text is drawn at fixed places, so it may run into its neighbour')
    changed = [t for t in tables.values() if t.changed()]
    units = []
    t6 = tables.get('6')
    if t6 is not None and t6.registry:
        # the italic title font (title package parts 50 / 51) gets every accent the menus use
        t6.italic = {c: b + 0x1F for c, b in t6.registry.items()}
        if t6 not in changed:
            units.append(t6)
        for w in vmenu._load('vp_accents').ITALIC_COPIES:
            if w != 6:
                units.append(vmenu.ItalicFont(arc, dirw, w, t6.italic))
        log('italic title font (packages 6 and 2173): ' + ', '.join(
            f'{c} as {chr(b + 0x1F)!r}' for c, b in sorted(t6.registry.items(), key=lambda kv: kv[1])))
    for t in changed:
        ids = t.changed()
        shown = ', '.join(f'{m:#x}' for m in ids[:12]) + (f' and {len(ids) - 12} more' if len(ids) > 12 else '')
        linked = f' ({len(links[t.key])} linked from another table)' if links[t.key] else ''
        grown = ''
        if t.kind == 'battle' and t.added:
            grown = f'; font {len(t.widths)} -> {len(t.glyphs()[0])} glyphs (added {"".join(t.added)})'
        elif t.kind == 'menu' and t.added:
            grown = '; ' + t.glyph_note()
        log(f'{table_title(t)}: messages {shown} changed{linked}{grown}')
        t.write()
        units += [u for u in t.units if u not in units]
    for u in units:
        pkg, room = u.repack()
        if len(pkg) > room:
            pkg, room = u.repack(recompress_all=True)
        if len(pkg) > room:
            raise ValueError(f'{u.label}: the package needs {len(pkg)} bytes, its sectors hold {room} '
                             f'({len(pkg) - room} too many)')
        log(f'  {u.label}: package {u.used} -> {len(pkg)} bytes of {room}')
        _patch_sectors(arc, u.first, u.end, pkg, room, patches)
    return len(changed), (dict(t6.italic) if t6 is not None and t6.registry else {})


def build_image(disc, arc, dirw, names, edits, output, fit=False, log=print, table_edits=(), link=True,
                title=None, worldmap=None, system=None, camp_tabs=None):
    """edits: [(origin label, scene id, {record: text})]; table_edits: [(origin label, menu or battle
    table key ('2175', '1806.1'), {message id: text})]; system: {record 0-13: text}, written into
    every scene; camp_tabs: {tab label message id: box width}. Writes output (.cue + .bin)."""
    db = []
    db_get = lambda: db[0] if db else db.append(glyph_db(arc, dirw, names)) or db[0]
    patches = {}; total = 0
    edits, quiet = with_system(arc, dirw, names, edits, system)
    sys_stats = collections.Counter(); tightest = None
    for origin, scene, recs in edits:
        s = Scene(arc, dirw, scene, names)
        s.font.quiet = scene in quiet          # a scene only the system messages change: no per-glyph log
        changed = apply_edits(s, recs, db_get, f'{origin}: ')
        if not changed:
            if scene not in quiet:
                log(f'{origin}: scene {scene:#x}: no changes')
            continue
        notes = [n for k in changed for n in fit_boxes(s, k, fit) if k >= SYSTEM_RECORDS or not system]
        pkg, room = s.repack()
        if len(pkg) > room:
            pkg, room = s.repack(recompress_all=True)
        if len(pkg) > room:
            raise ValueError(f'{origin}: scene {scene:#x}: the package needs {len(pkg)} bytes, its sectors hold {room}')
        if scene in quiet:
            sys_stats['scenes'] += 1
            if tightest is None or room - len(pkg) < tightest[0]:
                tightest = (room - len(pkg), scene)
        else:
            log(f'{origin}: scene {scene:#x}: records {", ".join(map(str, changed))} changed; '
                f'package {s.pkg.used} -> {len(pkg)} bytes of {room}'
                + (f'; font {s.orig_font_n} -> {s.font.n} glyphs' if s.font.n != s.orig_font_n else ''))
        for n in notes:
            log('    ' + n)
        _patch_sectors(arc, s.pkg.first, s.pkg.end, pkg, room, patches)
        total += 1
    if system:
        log(f'system messages: records {", ".join(map(str, sorted(system)))} written into every scene '
            f'({sys_stats["scenes"]} with no other edits'
            + (f'; the least room left is {tightest[0]} bytes, scene {tightest[1]:#x}' if tightest else '') + ')')
    menus, accents = build_tables(arc, dirw, table_edits, link, patches, log)
    if worldmap:
        done = vwm.build(types.SimpleNamespace(**globals()), arc, dirw, names, worldmap, db_get, log)
        if done:
            wm, packed, room = done
            _patch_sectors(arc, wm.first, wm.end, packed, room, patches)
    if title:
        log('title menu (files 7 and 1):')
        for f, packed, room in vtitle.build_files(types.SimpleNamespace(**globals()), arc, dirw, title, log):
            _patch_sectors(arc, f.first, f.end, packed, room, patches)
    if camp_tabs or accents:
        log(f'camp menu program (file {vtabs.WORD}):')
        done = vtabs.build_file(types.SimpleNamespace(**globals()), arc, dirw, camp_tabs or {}, log, accents)
        if done:
            f, packed, room = done
            _patch_sectors(arc, f.first, f.end, packed, room, patches)
    if not patches:
        raise ValueError('nothing to write: no record differs from the disc')
    n = disc.write_copy(output, patches)
    log(f'wrote {output} and {os.path.splitext(output)[0]}.bin: {n} sectors changed in {total} scene(s)'
        + (f' and {menus} menu / battle table(s)' if menus else ''))
    return n


def cmd_build(a):
    disc, arc, dirw, names = open_disc(a.disc)
    f = EditFiles(a.edits)
    try:
        build_image(disc, arc, dirw, names, f.edits, a.output, a.fit_boxes, link=not a.no_link, **f.build_args())
    except ValueError as e:
        raise SystemExit(str(e))


def cmd_find(a):
    disc, arc, dirw, names = open_disc(a.disc)
    needle = a.text.lower()
    for k in range(vsn.SCENE_COUNT):
        try:
            s = Scene(arc, dirw, k, names)
        except (SystemExit, AssertionError, struct.error):
            continue
        for r in range(len(s.orig_records)):
            t = s.text(r)
            plain = re.sub(r'<[^>]*>', '', t)
            if needle in plain.lower():
                print(f'scene {k:#x} record {r}: {plain.replace(chr(10), " / ")[:100]}')
    for k, t in text_tables(arc, dirw).items():
        for mid in t.ids():
            plain = re.sub(r'\{[^}]*\}', '', t.text(mid))
            if needle in plain.lower():
                print(f'{t.kind} {k} message {mid:#06x}: {plain.replace(chr(10), " / ")[:100]}')
    wm = vwm.WorldMapText(types.SimpleNamespace(**globals()), arc, dirw, names)
    for mid in wm.ids():
        plain = re.sub(r'<[^>]*>', '', wm.text(mid))
        if needle in plain.lower():
            print(f'world map message {mid:#06x}: {plain.replace(chr(10), " / ")[:100]}')


def cmd_menus(a):
    disc, arc, dirw, names = open_disc(a.disc)
    for w, t in menu_tables(arc, dirw).items():
        first = [re.sub(r'\{[^}]*\}', '', t.text(m)) for m in t.ids()[1:5]]
        pkg, room = t.pkg.build()
        print(f'{w:6d}  {vmenu.MENU_FILES[w]:50s} {len(t.ids()):5d} messages  {room - t.pkg.used:5d} bytes free '
              f'(more after re-compressing)  e.g. {" | ".join(first)}')
    for k, t in vbattle.battle_tables(arc, dirw).items():
        first = [re.sub(r'\{[^}]*\}', '', t.text(m)) for m in t.ids()[1:5]]
        pkg, room = t.file.build()
        print(f'{k:>6s}  {t.name:50s} {len(t.ids()):5d} messages  {room - t.file.used:5d} bytes free '
              f'(battle file {t.word}; font {len(t.widths)} glyphs)  e.g. {" | ".join(first)}')


def check_menus(arc, dirw, compress):
    """Every menu string renders to text that encodes back to the same bytes, every table rebuilds
    unchanged, and (with compress) every package still fits after re-compressing."""
    bad = 0; n = 0
    for w, t in menu_tables(arc, dirw).items():
        if t.build() != t.raw:
            print(f'menu table {w}: does not rebuild'); bad += 1
        for s in t.orig:
            n += 1
            if t.encode(t.decode(s), []) != s:
                print(f'menu table {w}: {s[:24].hex()} does not round-trip'); bad += 1
        if compress:
            pkg, room = t.repack(recompress_all=True)
            if len(pkg) > room:
                print(f'menu table {w}: re-packed package does not fit'); bad += 1
            else:
                print(f'menu table {w}: re-packed {t.pkg.used} -> {len(pkg)} bytes of {room}')
    print(f'menu strings: {n}, problems: {bad}')
    bad = 0; n = 0
    tabs = vbattle.battle_tables(arc, dirw)
    for k, t in tabs.items():
        if t.build() != t.raw or t.build_font() != t.font_raw:
            print(f'battle table {k}: does not rebuild'); bad += 1
        for s in t.orig:
            n += 1; added = []
            if t.encode(t.decode(s), added) != s or added:
                print(f'battle table {k}: {s[:24].hex()} does not round-trip'); bad += 1
    if compress:
        for f in {id(u): u for t in tabs.values() for u in t.units}.values():
            pkg, room = f.repack(recompress_all=True)
            print(f'{f.label}: re-packed {f.used} -> {len(pkg)} bytes of {room}' + ('' if len(pkg) <= room else ': DOES NOT FIT'))
            bad += len(pkg) > room
    print(f'battle strings: {n}, problems: {bad}')


def cmd_check(a):
    disc, arc, dirw, names = open_disc(a.disc)
    if a.menus:
        return check_menus(arc, dirw, a.compress)
    scenes = a.scene or range(vsn.SCENE_COUNT)
    stats = collections.Counter(); worst = None
    for k in scenes:
        try:
            s = Scene(arc, dirw, k, names)
        except (SystemExit, AssertionError, struct.error):
            stats['no text'] += 1; continue
        # 1. the records are the whole text area, in index order, and rebuild the block exactly
        if s.block.build() != s.block.raw:
            print(f'scene {k:#x}: block does not rebuild'); stats['block differs'] += 1; continue
        # 2. every record renders to text that parses back to the same tokens
        bad = [r for r in range(len(s.orig_records)) if parse(s.text(r)) != [t for t, _ in s.tokens(r)]]
        if bad:
            print(f'scene {k:#x}: records {bad[:8]} do not round-trip'); stats['text differs'] += 1
        # 3. a fresh encode of each record (no original bytes reused) decodes to the same text
        prefer = s.preferred_codes()
        for r in range(len(s.orig_records)):
            toks = [t for t, _ in s.tokens(r)]
            fresh = b''.join(encode_token(t, s.font, dict(prefer), {}) for t in toks)
            if [t for t, _ in tokens(fresh, s.font)] != toks:
                print(f'scene {k:#x} record {r}: fresh encode differs'); stats['encode differs'] += 1
            elif fresh != s.orig_records[r]:
                stats['records using a second glyph variant'] += 1
        # 4. re-compress the whole container: it decodes back and the package still fits
        if a.compress:
            pkg, room = s.repack(recompress_all=True)
            ents = read_container(s.pkg.parts[s.pi][1])
            assert unpack(*ents[0]) == s.block.raw and unpack(*ents[1]) == s.font.raw()
            gain = s.pkg.used - len(pkg)
            stats['packages'] += 1
            if len(pkg) > room:
                print(f'scene {k:#x}: re-packed package does not fit'); stats['too big'] += 1
            if worst is None or gain < worst[0]:
                worst = (gain, k)
        stats['scenes'] += 1; stats['records'] += len(s.orig_records)
    for key, v in stats.items():
        print(f'{key}: {v}')
    if worst:
        print(f'smallest saving from re-compressing: {worst[0]} bytes (scene {worst[1]:#x})')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    num = lambda s: int(s, 0)
    p = sub.add_parser('export'); p.add_argument('disc'); p.add_argument('--scene', type=num)
    p.add_argument('--menu', type=int, help='a menu table (directory word; `menus` lists them)')
    p.add_argument('--battle', help='a battle table (1500.7, 1500.9, 1806.1, 1988.2; `menus` lists them)')
    p.add_argument('--title', action='store_true', help='the title-screen menu labels (pictures, re-rendered)')
    p.add_argument('--worldmap', action='store_true', help="the world map's own text (Freya's lessons, region notes)")
    p.add_argument('--system', action='store_true', help='the system messages (records 0-13, the same in every scene)')
    p.add_argument('--camp-tabs', action='store_true', help="the camp menu's Topic and Sort tabs (box widths)")
    p.add_argument('-o', '--output')
    p = sub.add_parser('menus'); p.add_argument('disc')
    p = sub.add_parser('find'); p.add_argument('disc'); p.add_argument('text')
    p = sub.add_parser('build'); p.add_argument('disc'); p.add_argument('edits', nargs='+')
    p.add_argument('-o', '--output', required=True)
    p.add_argument('--fit-boxes', action='store_true',
                   help="grow the script's message box (width/height pushes) where an edited record outgrows it")
    p.add_argument('--no-link', action='store_true',
                   help='change only the menu table named in each menu edit file, not its copies in other tables')
    p = sub.add_parser('check'); p.add_argument('disc'); p.add_argument('--scene', type=num, action='append')
    p.add_argument('--compress', action='store_true', help='also re-compress every container (slow)')
    p.add_argument('--menus', action='store_true', help='check the 13 menu and 4 battle text tables instead of the scenes')
    a = ap.parse_args()
    {'export': cmd_export, 'menus': cmd_menus, 'find': cmd_find, 'build': cmd_build, 'check': cmd_check}[a.cmd](a)


if __name__ == '__main__':
    main()
