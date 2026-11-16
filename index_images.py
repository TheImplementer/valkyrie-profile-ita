#!/usr/bin/env python3
"""Index every TIM image in VALKYRIE.BIN.

Reuses the TIM detection from bin_navigator.py and walks the whole archive in
three ways, because the game stores art three different ways:

  * raw      — TIM stored uncompressed in plain sectors (whole-file chunk scan)
  * block    — TIM inside a 10-sector LZ77 streaming block (decompress, scan)
  * slz      — TIM inside an SLZ-compressed file (decompress, scan)

Writes a JSON index; each entry carries an /api/image URL you can open directly
in a running bin_navigator (http://localhost:8765).

Usage:
  python3 index_images.py [/path/to/VALKYRIE.BIN] [--out images_index.json]
                          [--start SECTOR] [--end SECTOR]
                          [--no-raw] [--no-blocks] [--no-slz] [--limit N] [--quiet]

Default BIN path: ~/Documents/VALKYRIE.BIN
"""

import argparse
import json
import math
import struct
import sys
import time
from pathlib import Path

import bin_navigator as B   # local module — reuse TIM parse/scan/decompress helpers

SECTOR = B.SECTOR


def log(msg: str, quiet: bool) -> None:
    if not quiet:
        print(msg, file=sys.stderr, flush=True)


def image_url(rec: dict) -> str:
    """An /api/image query string that reproduces this TIM in bin_navigator."""
    q = f"/api/image?sector={rec['sector']}&source={rec['source']}&offset={rec['offset']}"
    if rec["source"] == "raw":
        need = math.ceil((rec["offset"] + rec["total_size"]) / SECTOR)
        q += f"&span={min(128, max(64, need))}"
    if rec["source"] == "slz":
        q += f"&byte_offset={rec['byte_offset']}"
    return q


def _rec(t: dict, source: str, sector: int, **extra) -> dict:
    rec = {
        "source":     source,
        "sector":     sector,
        "offset":     t["offset"],
        "mode":       t["mode"],
        "bpp":        t["bpp"],
        "width":      t["width"],
        "height":     t["height"],
        "clut_count": t["clut_count"],
        "total_size": t["total_size"],
        **extra,
    }
    rec["url"] = image_url(rec)
    return rec


def scan_raw(start_off: int, end_off: int, limit: int, quiet: bool,
             chunk: int = 8 << 20, overlap: int = 512 << 10) -> list:
    """Find uncompressed TIMs by scanning the file in overlapping chunks."""
    recs, seen = [], set()
    off = start_off
    while off < end_off:
        n   = min(chunk + overlap, end_off - off)
        buf = B.read_at(off, n)
        for t in B.find_tims_in_data(buf):
            a = off + t["offset"]
            if a in seen:
                continue                       # overlap region double-hit
            seen.add(a)
            recs.append(_rec(t, "raw", a // SECTOR, offset=a % SECTOR, abs_offset=a))
            if len(recs) >= limit:
                log(f"  [raw] hit limit {limit}", quiet)
                return recs
        off += chunk
        log(f"  [raw] {off/1e6:6.0f} MB scanned · {len(recs)} found", quiet)
    return recs


def scan_structured(start_sec: int, end_sec: int, do_blocks: bool, do_slz: bool,
                    limit: int, quiet: bool) -> list:
    """Walk sectors: decompress 10-sector blocks and SLZ files, scan each."""
    recs = []
    slz_seen, blk_seen = set(), set()
    lba = start_sec
    t0  = time.time()
    next_report = start_sec
    while lba < end_sec:
        head   = B.read_at(lba * SECTOR, 8)
        marker = struct.unpack_from("<I", head, 0)[0] if len(head) >= 4 else 0
        if marker == B.MARKER:                 # structured 10-sector block
            idx = head[4] if len(head) >= 5 else 0
            if idx == 0 and do_blocks:
                try:
                    payload  = B._block_payload(lba)
                    out, _   = B.lz77_decompress_multi(payload)
                    for t in B.find_tims_in_data(out):
                        key = (lba, t["offset"])
                        if key in blk_seen:
                            continue
                        blk_seen.add(key)
                        recs.append(_rec(t, "block", lba))
                        if len(recs) >= limit:
                            log(f"  [blk] hit limit {limit}", quiet)
                            return recs
                except Exception:
                    pass
            lba += (10 - idx) if 0 <= idx < 10 else 1   # jump past block interior
            continue
        if do_slz:                             # plain / padding sector — probe SLZ
            try:
                for byte_off, t in B.scan_slz_for_tims(lba):
                    key = (byte_off, t["offset"])
                    if key in slz_seen:
                        continue
                    slz_seen.add(key)
                    recs.append(_rec(t, "slz", lba, byte_offset=byte_off))
                    if len(recs) >= limit:
                        log(f"  [slz] hit limit {limit}", quiet)
                        return recs
            except Exception:
                pass
        lba += 1
        if lba >= next_report:
            log(f"  [walk] sector {lba:>7,}/{end_sec:,} · {len(recs)} found · "
                f"{time.time()-t0:5.0f}s", quiet)
            next_report += 20000
    return recs


def main() -> None:
    ap = argparse.ArgumentParser(description="Index all TIM images in VALKYRIE.BIN")
    ap.add_argument("bin", nargs="?", default="~/Documents/VALKYRIE.BIN")
    ap.add_argument("--out", default="images_index.json")
    ap.add_argument("--start", type=int, default=0, help="first sector")
    ap.add_argument("--end",   type=int, default=None, help="last sector (exclusive)")
    ap.add_argument("--no-raw",    action="store_true", help="skip uncompressed scan")
    ap.add_argument("--no-blocks", action="store_true", help="skip 10-sector blocks")
    ap.add_argument("--no-slz",    action="store_true", help="skip SLZ files")
    ap.add_argument("--limit", type=int, default=100000, help="max images per pass")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    B.BIN_PATH = Path(args.bin).expanduser()
    if not B.BIN_PATH.exists():
        print(f"error: file not found: {B.BIN_PATH}", file=sys.stderr)
        sys.exit(1)
    B._fh = open(B.BIN_PATH, "rb")
    size  = B.BIN_PATH.stat().st_size
    total_sectors = size // SECTOR
    end_sec = total_sectors if args.end is None else min(args.end, total_sectors)

    log(f"Indexing {B.BIN_PATH.name} ({size:,} bytes, {total_sectors:,} sectors)", args.quiet)
    log(f"Range: sectors {args.start:,}–{end_sec:,}", args.quiet)

    t0 = time.time()
    images = []
    if not args.no_raw:
        log("Pass 1/2: uncompressed (raw) TIMs…", args.quiet)
        images += scan_raw(args.start * SECTOR, end_sec * SECTOR, args.limit, args.quiet)
    if not (args.no_blocks and args.no_slz):
        log("Pass 2/2: blocks + SLZ files…", args.quiet)
        images += scan_structured(args.start, end_sec,
                                  not args.no_blocks, not args.no_slz,
                                  args.limit, args.quiet)

    images.sort(key=lambda r: (r["sector"], r["offset"]))
    for i, r in enumerate(images):
        r["id"] = i

    by_source, by_bpp, by_dim = {}, {}, {}
    for r in images:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        by_bpp[r["bpp"]]       = by_bpp.get(r["bpp"], 0) + 1
        d = f"{r['width']}x{r['height']}"
        by_dim[d] = by_dim.get(d, 0) + 1

    index = {
        "archive":       B.BIN_PATH.name,
        "file_size":     size,
        "sector_count":  total_sectors,
        "scan_range":    [args.start, end_sec],
        "elapsed_sec":   round(time.time() - t0, 1),
        "count":         len(images),
        "by_source":     by_source,
        "by_bpp":        {str(k): v for k, v in sorted(by_bpp.items())},
        "navigator_hint": "Open any image with http://localhost:8765 + its \"url\" field",
        "images":        images,
    }
    Path(args.out).write_text(json.dumps(index, indent=2))
    B._fh.close()

    # human summary
    print(f"\nIndexed {len(images)} TIM images in {index['elapsed_sec']}s "
          f"→ {args.out}")
    print(f"  by source : {by_source}")
    print(f"  by bpp    : {index['by_bpp']}")
    top = sorted(by_dim.items(), key=lambda kv: -kv[1])[:8]
    print(f"  top dims  : {', '.join(f'{d}×{n}' for d, n in top)}")
    for r in images[:10]:
        bo = f" byte_off={r['byte_offset']}" if "byte_offset" in r else ""
        print(f"  #{r['id']:<4} {r['source']:<5} sec {r['sector']:>7,} "
              f"{r['width']}x{r['height']} {r['bpp']}bpp cluts={r['clut_count']}{bo}")
    if len(images) > 10:
        print(f"  … and {len(images)-10} more (see {args.out})")


if __name__ == "__main__":
    main()
