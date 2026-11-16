#!/usr/bin/env python3
"""Which scene is which: scene ids -> package directory -> scene package -> text container.

Usage:
    python3 vp_scenes.py VALKYRIE.BIN                 table of every scene id
    python3 vp_scenes.py VALKYRIE.BIN --json OUT      the same as JSON
    python3 vp_scenes.py VALKYRIE.BIN --scene 0x16    one scene's package parts

How the game picks a scene (traced in TinyPSX, see docs/VP_DIALOGUE_LOAD_PATH.md):
  built-in 0x9E (select_scene) passes a scene id to 0x8006C270, which looks it up in a
  10-byte-per-scene table at 0x80080618 (field engine data, 1,118 ids: 0..0x45D). Field +0 of the
  entry is the directory word of the scene's package, and it is always 3610 + id; field +8 is a
  shared package (3605 or 3606) that is loaded too unless the 0x9E call names another one.
  The loader object (0x8006C49C) then reads the directory word at boot-decrypted header word
  3610 + id: a BCD position MM SS FF; the next word gives the end, so the package size.
"""
import argparse
import importlib.util
import json
import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


vst = _load('vp_scene_text')
vsc = _load('vp_script')

K0 = 0x64283921
HEADER_SECTORS = 13
SCENE_BASE = 3610          # directory word of scene 0
SCENE_COUNT = 0x45E        # entries in the scene table at 0x80080618
SECTOR = 2048


def directory(data):
    """The archive header, decrypted the way cd_subsystem_init (0x80011D38) does it: the sector-0
    LFSR, its key stream continued over all 13 header sectors."""
    words = struct.unpack_from(f'<{HEADER_SECTORS * SECTOR // 4}I', data, 0)
    key = K0; out = []
    for w in words:
        out.append(w ^ key)
        key = (~(key ^ (key << 1)) & 0xFFFFFFFF) ^ K0
    return out


def _bcd(x):
    return (x >> 4) * 10 + (x & 15)


def sector(word):
    """VALKYRIE.BIN sector of a directory word (BCD MM SS FF): the disc LBA is the MSF position
    minus the 150-frame pregap, and the archive starts at LBA 150."""
    msf = (_bcd(word & 0xFF) * 60 + _bcd(word >> 8 & 0xFF)) * 75 + _bcd(word >> 16 & 0xFF)
    return msf - 300


def file_end(dirw, idx):
    """End sector of the file at directory word idx: the start of the next word whose sector is
    later. Disc 2 has empty words (0, so before the file) after files 1 and 7; on disc 1 the next
    word is always the next file."""
    first = sector(dirw[idx]); n = idx + 1
    while n < len(dirw) and sector(dirw[n]) <= first:
        n += 1
    return sector(dirw[n]) if n < len(dirw) else first


def package(data, dirw, idx):
    """Parts of the package at directory word idx: [(type, byte offset in VALKYRIE.BIN, size)]."""
    start = sector(dirw[idx]) * SECTOR
    count, hsize = struct.unpack_from('<2I', data, start)
    if not 0 < count < 64 or hsize != 8 + 8 * count:
        return None
    parts = []; at = start + hsize
    for i in range(count):
        kind, size = struct.unpack_from('<2I', data, start + 8 + 8 * i)
        parts.append((kind, at, size)); at += size
    return parts


def scene_container(data, dirw, scene):
    parts = package(data, dirw, SCENE_BASE + scene) or []
    for kind, at, _ in parts:
        if kind == 4 and vst.is_scene(data, at):
            return at
    return None


def exits(W, starts):
    """Scene ids this script can switch to: 0x9E with an inline operand, or the stack form right
    after a literal push of the id (the handler pops the id first)."""
    out = []
    for n, i in enumerate(starts):
        w = W[i]
        if w >> 24 != 0x9E:
            continue
        if not w & 0x100000:
            out.append(w & 0xFFFF)
        elif n and W[starts[n - 1]] >> 24 == 0x12:
            out.append(W[starts[n - 1]] & 0x7FFFFF)
    return out


def captions(W, starts, records):
    """Place names: records shown by the caption built-ins 0xCB / 0x97 (inline form)."""
    out = []
    for i in starts:
        w = W[i]
        if w >> 24 in (0xCB, 0x97) and not w & 0x100000 and (w & 0xFFFF) < len(records):
            text = records[w & 0xFFFF].replace('<5>', '').strip()
            if text and text not in out:
                out.append(text)
    return out


def scene_table(data, glyphs):
    dirw = directory(data); rows = []
    for k in range(SCENE_COUNT):
        idx = SCENE_BASE + k; at = scene_container(data, dirw, k)
        row = {'scene': k, 'dir_word': idx, 'sector': sector(dirw[idx]), 'container': at}
        if at is not None:
            hdr, W, records = vsc.load(data, at, glyphs)
            starts = vsc.sweep(W)
            row['records'] = len(records) - 14
            row['place'] = captions(W, starts, records)
            row['exits'] = sorted({e for e in exits(W, starts) if e < SCENE_COUNT})
            first = [r for _, r in vsc.scene_lines(W, starts, hdr[2]) if 14 <= r < len(records)]
            row['first_line'] = records[first[0]].replace('\n', ' / ')[:70] if first else ''
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('archive')
    ap.add_argument('--json')
    ap.add_argument('--scene', type=lambda s: int(s, 0))
    a = ap.parse_args()
    data = open(a.archive, 'rb').read()
    glyphs = vst.glyph_names()
    if a.scene is not None:
        dirw = directory(data); idx = SCENE_BASE + a.scene
        print(f'scene {a.scene:#x}: directory word {idx} ({idx:#x}) = {dirw[idx]:08x}, sector {sector(dirw[idx])}')
        for kind, at, size in package(data, dirw, idx) or []:
            print(f'  part type {kind:2}  offset {at:#x}  size {size:#x}')
        return
    rows = scene_table(data, glyphs)
    if a.json:
        json.dump(rows, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    for r in rows:
        if r['container'] is None:
            print(f"{r['scene']:#05x}  word {r['dir_word']}  sector {r['sector']}  (no scene text)")
            continue
        place = ' | '.join(r['place']) or '-'
        ex = ' '.join(f'{e:#x}' for e in r['exits'])
        print(f"{r['scene']:#05x}  word {r['dir_word']}  container {r['container']:#x}  {r['records']:3} lines  "
              f"{place}  -> {ex or '-'}  « {r['first_line']}")


if __name__ == '__main__':
    main()
