#!/usr/bin/env python3
"""Valkyrie Profile world-map text: read, edit and re-pack the world map's own string table.

The world map's English text is in neither the scene packages nor the 13 menu tables. It is in
directory word 4797, a file that is not a package but an offset table of 67 SLZ streams:

  u32 n (67), n x u32: bits 0-23 the stream's byte offset in the file, bits 24-31 a group number
  (0 for streams 0-37, then 1, 2, 3, 4, 5, 6 and 10); each stream is "SLZ" + kind, u32 compressed
  length, u32 unpacked length, u32 0, then the payload, the next one starting 4-aligned.

  stream 38 (group 1)  the font: the scene-font layout (u32 n = 76, u32 12, n widths, n x 24-byte
                       bitmaps, glyph c = index c - 1)
  stream 39 (group 2)  the strings: the menu-table layout (u32 text_offset = 4 + 8n, n x (u32 id,
                       u32 offset), strings ended by 0x00), 136 of them, encoded like scene records
                       (a glyph code per byte, 0x80 0x80 a line break, 0x81 0x80 <page>, other
                       commands <N> / <N:hex>) with stream 38 as their font

Found by searching a RAM dump taken during Freya's lesson in the sky (the first world-map visit)
for the lines' letter pattern, then every SLZ stream on the disc for those bytes. In that dump the
font is at 0x8009CEE4 and the table at 0x8009D668, each in a heap block of its unpacked size (the
16-byte block header before each holds the size), so a longer table or font gets a longer block.
What limits growth is the file's 189 sectors: the streams end at byte 386,557 of 387,072.

The strings: ids 0x01-0x0B world-map messages ("Press START button to begin spiritual
concentration.", "This Chapter is concluded."), 0x64-0x89 the region descriptions, 0xC8-0x10A a
list of character and place names, 0x12C-0x16A Freya's lessons (the sky, the first soul, the undead,
her warning).

Edit-file syntax (the scene editor's, with the menu tables' hex ids):
  @@ 0x012c           the message id the text below belongs to
  text                as in a scene edit file: a line break is a new line, <page>, <N> / <N:hex>
                      commands, {xx} a raw glyph code; a character the font lacks (accented
                      letters too) is added to it from the scene glyph database
"""
import importlib.util
import os
import re
import struct

HERE = os.path.dirname(os.path.abspath(__file__))

FILE_WORD = 4797
FONT_STREAM = 38
TABLE_STREAM = 39
HEADER = '# Valkyrie Profile world map text'
HEADER_RE = re.compile(re.escape(HEADER) + r'\b')


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


class StreamFile:
    """A file of SLZ streams behind an offset table (directory word 4797)."""

    def __init__(self, ed, arc, dirw, word=FILE_WORD):
        self.ed = ed
        self.word = word
        self.first = ed.vsn.sector(dirw[word]); self.end = ed.vsn.file_end(dirw, word)
        data = bytes(arc[self.first * ed.SECTOR:self.end * ed.SECTOR])
        n = struct.unpack_from('<I', data, 0)[0]
        if not 0 < n < 1024:
            raise ValueError(f'directory word {word}: not a stream file')
        words = struct.unpack_from(f'<{n}I', data, 4)
        self.groups = [w >> 24 for w in words]
        self.streams = []           # [kind, payload, unpacked length]
        for w in words:
            at = w & 0xFFFFFF
            if data[at:at + 3] != b'SLZ':
                raise ValueError(f'directory word {word}: no SLZ stream at byte {at}')
            kind = data[at + 3]; clen, ulen = struct.unpack_from('<2I', data, at + 4)
            self.streams.append([kind, data[at + 16:at + 16 + clen], ulen])
        last = words[-1] & 0xFFFFFF
        self.used = last + 16 + len(self.streams[-1][1])
        self.label = f'world map file {word}'

    def unpacked(self, i):
        return self.ed.unpack(*self.streams[i])

    def set_stream(self, i, raw):
        kind = self.streams[i][0]
        self.streams[i] = [kind, self.ed.vslz.compress(raw, kind) if kind else raw, len(raw)]

    def build(self):
        n = len(self.streams)
        out = bytearray(4 + 4 * n); at = len(out); words = []
        for (kind, payload, ulen), g in zip(self.streams, self.groups):
            at = self.ed.align4(at)
            out += bytes(at - len(out))
            words.append(g << 24 | at)
            out += b'SLZ' + bytes([kind]) + struct.pack('<3I', len(payload), ulen, 0) + payload
            at = len(out)
        struct.pack_into(f'<I{n}I', out, 0, n, *words)
        return bytes(out), (self.end - self.first) * self.ed.SECTOR


class WorldMapText:
    """The world map's string table and its font, with edits applied to copies."""

    def __init__(self, ed, arc, dirw, names):
        self.ed = ed
        self.file = StreamFile(ed, arc, dirw)
        self.first, self.end, self.label = self.file.first, self.file.end, self.file.label
        self.font = ed.Font(self.file.unpacked(FONT_STREAM), names)
        self.orig_font_n = self.font.n
        raw = self.file.unpacked(TABLE_STREAM)
        to = struct.unpack_from('<I', raw, 0)[0]
        if to < 12 or (to - 4) % 8 or to > len(raw):
            raise ValueError(f'{self.label} stream {TABLE_STREAM}: not a string table')
        self.text_offset = to
        self.pairs = [struct.unpack_from('<2I', raw, 4 + 8 * i) for i in range((to - 4) // 8)]
        self.orig = {}
        for mid, o in self.pairs:
            j = to + o; i = j
            while raw[i] != 0:
                if raw[i] & 0x80:
                    v = (raw[i] & 0x7F) | (raw[i + 1] << 7)
                    i += 2 + (ed.ARG_BYTES.get(v - 0x4000, 0) if v >= 0x4000 else 0)
                else:
                    i += 1
            self.orig[mid] = raw[j:i]
        self.records = dict(self.orig)
        self.raw = raw

    def ids(self):
        return [mid for mid, _ in self.pairs]

    def tokens(self, mid):
        return self.ed.tokens(self.orig[mid], self.font)

    def text(self, mid):
        return self.ed.render(self.tokens(mid))

    def preferred_codes(self):
        use = {}
        for mid in self.orig:
            for t, raw in self.tokens(mid):
                if len(t) == 1 and t != '\n':
                    code = raw[0] if len(raw) == 1 else (raw[0] & 0x7F) | raw[1] << 7
                    use.setdefault(t, {}).setdefault(code, 0)
                    use[t][code] += 1
        return {ch: max(c, key=c.get) for ch, c in use.items()}

    def apply(self, recs, db_get, origin=''):
        """Encode {message id: edited text}. Returns the changed ids."""
        ed = self.ed; prefer = self.preferred_codes(); changed = []
        for mid, text in sorted(recs.items()):
            if mid not in self.orig:
                raise ValueError(f'{origin}the world map table has no message {mid:#x}')
            old = self.tokens(mid)
            try:
                new = ed.parse(text)
            except ValueError as e:
                raise ValueError(f'{origin}message {mid:#x}: {e}')
            if new == [t for t, _ in old]:
                continue
            try:
                self.records[mid] = ed.encode(new, old, self.font, prefer, db_get())
            except ValueError as e:
                raise ValueError(f'{origin}message {mid:#x}: {e}')
            changed.append(mid)
        return changed

    def table_bytes(self):
        """The string table with the current records, in the original order and layout."""
        offs = []; text = bytearray()
        for mid, _ in self.pairs:
            offs.append(len(text)); text += self.records[mid] + b'\0'
        out = struct.pack('<I', self.text_offset)
        out += b''.join(struct.pack('<2I', mid, o) for (mid, _), o in zip(self.pairs, offs))
        return out + bytes(text)

    def repack(self):
        """New file bytes and the room in its sectors."""
        if self.records != self.orig:
            self.file.set_stream(TABLE_STREAM, self.table_bytes())
        if self.font.n != self.orig_font_n:
            if self.font.n % 2:
                # every font on the disc has an even glyph count: the renderer reads the bitmaps
                # (at 8 + n) as halfwords, so an odd n would put them at odd addresses
                self.font.add(bytes(24), 0)
            self.file.set_stream(FONT_STREAM, self.font.raw())
        return self.file.build()


def edit_file_text(wm, ids=None):
    lines = [f'{HEADER}: {len(wm.pairs)} messages, directory word {FILE_WORD} streams '
             f'{FONT_STREAM} (font) and {TABLE_STREAM} (strings), sectors {wm.first}..{wm.end - 1} of VALKYRIE.BIN',
             '# Edit the text under any "@@ 0x.." line (a message id); delete the entries you do not change.',
             '# The text is written as in a scene edit file: a line break is a new line, <page> a new box page,',
             '# <N> / <N:hex> the other commands (2 waits for a button), {xx} a raw glyph code. A character the',
             '# font lacks (an accented letter too) is added to it.', '']
    for mid in (ids if ids is not None else wm.ids()):
        lines.append(f'@@ {mid:#06x}'); lines.append(wm.text(mid))
    return '\n'.join(lines) + '\n'


def is_edit_file(path):
    try:
        with open(path, encoding='utf-8') as f:
            return bool(HEADER_RE.match(f.readline()))
    except (OSError, UnicodeDecodeError):
        return False


def read_edit_file(path):
    """{message id: text} from a world-map edit file."""
    recs = {}; cur = None; body = []
    for line in open(path, encoding='utf-8').read().split('\n'):
        if cur is None and line.startswith('#'):
            continue
        m = re.fullmatch(r'@@ (0x[0-9a-fA-F]+)', line)
        if m:
            if cur is not None:
                recs[cur] = '\n'.join(body)
            cur = int(m.group(1), 16); body = []
        elif cur is not None:
            body.append(line)
    if cur is not None:
        recs[cur] = '\n'.join(body)
        if recs[cur].endswith('\n'):
            recs[cur] = recs[cur][:-1]
    return recs


def build(ed, arc, dirw, names, recs, db_get, log=print, origin='world map'):
    """Apply {message id: text}; returns (WorldMapText, file bytes, room) or None if nothing changed."""
    wm = WorldMapText(ed, arc, dirw, names)
    changed = wm.apply(recs, db_get, f'{origin}: ')
    if not changed:
        log(f'{origin}: no changes'); return None
    packed, room = wm.repack()
    if len(packed) > room:
        raise ValueError(f'{wm.label}: the file needs {len(packed)} bytes, its sectors hold {room}')
    shown = ', '.join(f'{m:#x}' for m in changed[:12]) + (f' and {len(changed) - 12} more' if len(changed) > 12 else '')
    log(f'{origin}: messages {shown} changed; file {wm.file.used} -> {len(packed)} bytes of {room}'
        + (f'; font {wm.orig_font_n} -> {wm.font.n} glyphs' if wm.font.n != wm.orig_font_n else ''))
    return wm, packed, room
