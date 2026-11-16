#!/usr/bin/env python3
"""Render PSX TIM textures from VALKYRIE.BIN to PNG, reusing bin_navigator's pipeline.

Used to locate the font glyph atlas. Flattens transparency onto a visible background
and nearest-neighbor upscales so small atlases are legible. Can emit a single image or
a contact sheet of several index entries.

  python3 render_tim.py one <id> [--clut N] [--scale S] [--out f.png]
  python3 render_tim.py sheet <id> <id> ...   [--clut N] [--out f.png]
"""
import json
import os
import struct
import sys

import bin_navigator as bn

IDX = json.load(open(os.path.join(os.path.dirname(__file__), "images_index.json")))
BYID = {e["id"]: e for e in IDX["images"]}
BG = (255, 0, 255)            # magenta marks transparent pixels


def source_buffer(e):
    if e["source"] == "slz":
        return bn.load_text_file(e["byte_offset"])["data"]
    if e["source"] == "raw":
        return b"".join(bn.read_sector_raw(e["sector"] + i) for i in range(64))
    payload = bn._block_payload(e["sector"])
    out, _ = bn.lz77_decompress_multi(payload)
    return out


def render_rgba(e, clut=0):
    """Return (w, h, rgba) for an index entry, or None on failure."""
    data = source_buffer(e)
    tim = bn.parse_tim_header(data, e["offset"])
    if tim is None:
        return None
    return bn.decode_tim_rgba(data, tim, clut)


def flatten_scale(w, h, rgba, scale, bg=BG):
    """Composite onto bg (opaque) and nearest-neighbor upscale; return (W,H,rgb-rgba)."""
    W, H = w * scale, h * scale
    out = bytearray(W * H * 4)
    for y in range(h):
        for x in range(w):
            o = (y * w + x) * 4
            r, g, b, a = rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]
            if a == 0:
                r, g, b = bg
            for dy in range(scale):
                base = ((y * scale + dy) * W + x * scale) * 4
                for dx in range(scale):
                    p = base + dx * 4
                    out[p] = r; out[p + 1] = g; out[p + 2] = b; out[p + 3] = 255
    return W, H, bytes(out)


def render_one(id_, clut=0, scale=6, out=None):
    e = BYID[id_]
    res = render_rgba(e, clut)
    if res is None:
        print(f"#{id_}: parse failed"); return
    w, h, rgba = res
    W, H, flat = flatten_scale(w, h, rgba, scale)
    out = out or f"tim_{id_}.png"
    open(out, "wb").write(bn.encode_png(W, H, flat))
    print(f"#{id_} sec{e['sector']} {w}x{h} {e['bpp']}bpp clut={e['clut_count']} -> {out} ({W}x{H})")


def render_sheet(ids, clut=0, out="contact.png", target=150, pad=8):
    tiles = []
    for id_ in ids:
        e = BYID[id_]
        res = render_rgba(e, clut)
        if res is None:
            print(f"#{id_}: parse failed"); continue
        w, h, rgba = res
        s = max(1, target // max(w, h))
        W, H, flat = flatten_scale(w, h, rgba, s)
        tiles.append((id_, e, W, H, flat))
        print(f"  tile #{id_} sec{e['sector']} {w}x{h} clut={e['clut_count']} scaled x{s} -> {W}x{H}")
    if not tiles:
        return
    cols = 4
    cellw = max(t[2] for t in tiles) + pad * 2
    cellh = max(t[3] for t in tiles) + pad * 2
    rows = (len(tiles) + cols - 1) // cols
    SW, SH = cols * cellw, rows * cellh
    sheet = bytearray(b"\x20" * (SW * SH * 4))           # dark gray
    for i in range(0, len(sheet), 4):
        sheet[i + 3] = 255
    print("\nlayout (row,col) -> id:")
    for k, (id_, e, W, H, flat) in enumerate(tiles):
        r, c = divmod(k, cols)
        ox, oy = c * cellw + pad, r * cellh + pad
        for y in range(H):
            for x in range(W):
                so = (y * W + x) * 4
                do = ((oy + y) * SW + (ox + x)) * 4
                sheet[do:do + 4] = flat[so:so + 4]
        print(f"  ({r},{c}) = #{id_}  sec{e['sector']} {e['width']}x{e['height']} clut={e['clut_count']}")
    open(out, "wb").write(bn.encode_png(SW, SH, bytes(sheet)))
    print(f"\nwrote {out} ({SW}x{SH})")


def dump_all(folder="tim_dump"):
    os.makedirs(folder, exist_ok=True)
    n_ok = n_fail = 0
    for e in IDX["images"]:
        w0, h0 = e["width"], e["height"]
        scale = max(1, min(8, 192 // max(w0, h0, 1)))
        try:
            res = render_rgba(e, 0)
            if res is None:
                n_fail += 1; continue
            w, h, rgba = res
            W, H, flat = flatten_scale(w, h, rgba, scale)
            name = f"{e['id']:03d}_sec{e['sector']:06d}_{w}x{h}_{e['bpp']}b_{e['clut_count']}c.png"
            open(os.path.join(folder, name), "wb").write(bn.encode_png(W, H, flat))
            n_ok += 1
        except Exception as ex:
            n_fail += 1
        if (n_ok + n_fail) % 100 == 0:
            print(f"  {n_ok + n_fail}/{len(IDX['images'])} ...", flush=True)
    print(f"done: {n_ok} rendered, {n_fail} failed -> {folder}/")


def main():
    bn._fh = open(bn.BIN_PATH, "rb")
    a = sys.argv[1:]
    if a and a[0] == "dump":
        dump_all(a[1] if len(a) > 1 else "tim_dump")
        return
    clut = int(a[a.index("--clut") + 1]) if "--clut" in a else 0
    scale = int(a[a.index("--scale") + 1]) if "--scale" in a else 6
    out = a[a.index("--out") + 1] if "--out" in a else None
    for f in ("--clut", "--scale", "--out"):
        if f in a:
            i = a.index(f); del a[i:i + 2]
    mode = a[0]
    ids = [int(x) for x in a[1:]]
    if mode == "one":
        render_one(ids[0], clut, scale, out)
    elif mode == "sheet":
        render_sheet(ids, clut, out or "contact.png")


if __name__ == "__main__":
    main()
