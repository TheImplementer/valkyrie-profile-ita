#!/usr/bin/env python3
"""Decode Valkyrie Profile scene text straight from VALKYRIE.BIN.

Usage:
    python3 vp_scene_text.py VALKYRIE.BIN OFFSET [--raw]      one scene container
    python3 vp_scene_text.py VALKYRIE.BIN --all [--raw]       every scene container on the disc

OFFSET is the byte offset of a scene-text SLZ container (e.g. 0x13441A40, the Valhalla /
"How nostalgic" scene). Everything here was traced live in TinyPSX and checked against the
game's own RAM (see VP_DIALOGUE_LOAD_PATH.md):

  container  = SLZ entry 0 (type 1): the scene block; SLZ entry 1 (type 2): the scene's font
  scene block: 7 x u32 header (offsets from +0x1C)
      hdr[0] text index offset (= event-script size)   hdr[2] scene main function (script word)
      hdr[3] record count   hdr[4] text bytes   hdr[5], hdr[6] library entry points (words)
    event script (first 0x2944 bytes: a library shared by every scene), then
    text index: hdr[3] x u16 offsets from the end of the index, then the records.
  record: glyph codes 0x01-0x7F, 2-byte codes 0x80+ (value - 0x4000 = command + ARG_BYTES; a value
          below 0x4000 is glyph 128+), 0x00 ends it.
  font: u32 glyph count n, u32 12 (rows), n width bytes, n x 12 rows x u16 bitmaps (MSB = left).

Glyph codes mean different characters in different scenes: a scene's font holds only the glyphs it
needs, in its own order. So each code is looked up by its BITMAP in vp_glyph_names.json (158 distinct
bitmaps cover all 1,117 scene fonts).
"""
import argparse
import json
import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))


def glyph_names(path=None):
    """The glyph bitmap -> character table (vp_glyph_names.json, keyed by bitmap hash)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('vp_glyph_names', os.path.join(HERE, 'vp_glyph_names.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.load(path or mod.PATH)

# Argument bytes per command, read from the game's renderer dispatch (0x8004C484, table 0x8007DC6C).
# 0 newline, 1 new page, 3 typing delay (frames per character), 4 colour, 5 centre line, 6 NPC id (u16), 7/8 width/height scale,
# 12 voice clip cue (u16, calls 0x8005A74C), 14 insert value, 17 auto-advance frames (u16),
# 18 start the next queued voice clip, 19 show the queued portrait (u16: 0100 left, 0000 right, ffff default side).
ARG_BYTES = {3: 1, 4: 1, 6: 2, 7: 1, 8: 1, 12: 2, 14: 1, 17: 2, 19: 2}
QUIET = {3, 4, 7, 8, 12, 17, 18}   # layout/effect commands hidden unless --raw


def slz1(p, ulen):
    """SLZ type 1 (EXE 0x800123CC): LZ, offset 0 ends the stream."""
    out = bytearray(); o = 0; flags = 0; left = 0
    while o < len(p) and len(out) < ulen:
        if left == 0:
            flags = p[o]; o += 1; left = 8
        bit = flags & 1; flags >>= 1; left -= 1
        if bit:
            out.append(p[o]); o += 1
            continue
        b1, b2 = p[o], p[o + 1]; o += 2
        off = ((b2 & 0xF) << 8) | b1
        if off == 0:
            break
        s = len(out) - off
        for i in range((b2 >> 4) + 3):
            out.append(out[s + i] if s + i >= 0 else 0)
    return bytes(out[:ulen])


def slz2(p, ulen):
    """SLZ type 2 (EXE 0x80012874): LZ plus a run-length fill for length code 15, no end
    marker (it stops after ulen bytes). Fill: offset < 256 -> next byte, offset + 19 times;
    otherwise low byte, (offset >> 8) + 3 times."""
    out = bytearray(); o = 0; flags = 0; left = 0
    while len(out) < ulen:
        if left == 0:
            flags = p[o]; o += 1; left = 8
        bit = flags & 1; flags >>= 1; left -= 1
        if bit:
            out.append(p[o]); o += 1
            continue
        b0, b1 = p[o], p[o + 1]
        off = b0 | ((b1 & 0xF) << 8); code = b1 >> 4
        if code < 15:
            s = len(out) - off
            for i in range(code + 3):
                out.append(out[s + i] if s + i >= 0 else 0)
            o += 2
        elif off < 256:
            out += bytes([p[o + 2]]) * (off + 19); o += 3
        else:
            out += bytes([off & 0xFF]) * ((off >> 8) + 3); o += 2
    return bytes(out[:ulen])


def entries(data, at):
    res = []
    while data[at:at + 3] == b'SLZ':
        kind = data[at + 3]
        clen, ulen, step = struct.unpack_from('<3I', data, at + 4)
        p = data[at + 16:at + 16 + clen]
        res.append(p[:ulen] if kind == 0 else slz1(p, ulen) if kind == 1 else slz2(p, ulen))
        if step == 0:
            break
        at += step
    return res


def is_scene(data, at):
    """A type-1 entry followed by a font of 8 + 25n bytes (almost always SLZ type 2; scene 0x19E's
    is type 1, and scene 0's debug menu has 352 glyphs)."""
    if data[at:at + 4] != b'SLZ\x01':
        return False
    step = struct.unpack_from('<I', data, at + 12)[0]
    j = at + step
    if not 0 < step < 0x40000 or data[j:j + 4] not in (b'SLZ\x01', b'SLZ\x02'):
        return False
    flen = struct.unpack_from('<I', data, j + 8)[0]
    return (flen - 8) % 25 == 0 and 0 < (flen - 8) // 25 < 1024


def decode_scene(data, at, glyphs, raw=False):
    blk, font = entries(data, at)[:2]
    n = struct.unpack_from('<I', font)[0]
    cmap = {c: glyphs.get(font[8 + n + (c - 1) * 24: 8 + n + c * 24].hex(), f'[{c:02x}]') for c in range(1, n + 1)}
    hdr = struct.unpack_from('<7I', blk)
    index = 0x1C + hdr[0]; count = hdr[3]; text = index + 2 * count
    if count == 0 or text > len(blk):
        raise ValueError(f'not a scene block: header {[hex(x) for x in hdr]}')
    records = []
    for o in struct.unpack_from(f'<{count}H', blk, index):
        i = text + o; out = []
        while i < len(blk):
            b = blk[i]; i += 1
            if b == 0:
                break
            if b & 0x80:
                cmd = ((b & 0x7F) | (blk[i] << 7)) - 0x4000; i += 1
                if cmd < 0:                  # a 2-byte code below 0x4000 is glyph 128+ (scene 0 only)
                    out.append(cmap.get(cmd + 0x4000, f'[{cmd + 0x4000:02x}]'))
                    continue
                k = ARG_BYTES.get(cmd, 0); args = blk[i:i + k]; i += k
                if cmd == 0:
                    out.append('\n')
                elif cmd == 1:
                    out.append('\n[page]\n')
                elif raw or cmd not in QUIET:
                    out.append(f'<{cmd}{":" + args.hex() if k else ""}>')
            else:
                out.append(cmap.get(b, f'[{b:02x}]'))
        records.append(''.join(out))
    return hdr, records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('archive')
    ap.add_argument('offset', nargs='?', type=lambda s: int(s, 0))
    ap.add_argument('--all', action='store_true', help='decode every scene container on the disc')
    ap.add_argument('--raw', action='store_true', help='also show layout/effect commands')
    ap.add_argument('--glyphs', default=os.path.join(HERE, 'vp_glyph_names.json'))
    a = ap.parse_args()
    glyphs = glyph_names(a.glyphs)
    data = open(a.archive, 'rb').read()
    if a.all:
        offsets = []; i = 0
        while (i := data.find(b'SLZ\x01', i)) >= 0:
            if is_scene(data, i):
                offsets.append(i)
            i += 4
    elif a.offset is not None:
        offsets = [a.offset]
    else:
        ap.error('give OFFSET or --all')
    for at in offsets:
        try:
            hdr, recs = decode_scene(data, at, glyphs, a.raw)
        except (ValueError, struct.error, IndexError) as e:
            raise SystemExit(f'{at:#x}: {e}')
        print(f'=== scene container {at:#x} (VALKYRIE.BIN sector {at // 2048} + {at % 2048:#x}): {len(recs)} records')
        for k, r in enumerate(recs):
            if r.strip():
                print(f'--- {k}\n{r}')


if __name__ == '__main__':
    main()
