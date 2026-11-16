#!/usr/bin/env python3
"""Locate a captured SLZ container inside VALKYRIE.BIN → its disc sector.

The runtime loads a compressed SLZ container into RAM via DMA, which bypasses CPU
watchpoints — so instead of hooking the read, we capture the resident compressed
bytes (vp_load_log.lua dumps them to container_0x*.bin) and find those exact bytes
in the disc image. The byte offset / 2048 is the sector.

Usage:
  python3 find_container_sector.py container_0x801c8c74.bin [/path/to/VALKYRIE.BIN]

Default BIN path: ~/Documents/VALKYRIE.BIN  (per CLAUDE.md). The archive is treated as
2048-byte sectors (same convention as bin_navigator.py).
"""
import mmap
import os
import sys

SECTOR = 2048


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    container_path = sys.argv[1]
    bin_path = (sys.argv[2] if len(sys.argv) > 2
                else os.path.expanduser(os.path.join("~", "Documents", "VALKYRIE.BIN")))

    data = open(container_path, "rb").read()
    magic = data[:3]
    typ = data[3] if len(data) > 3 else None
    print(f"container: {container_path}")
    print(f"  {len(data)} bytes, magic={magic!r}, type={typ}")
    if magic != b"SLZ":
        print("  warning: doesn't start with 'SLZ' — matching anyway")

    if not os.path.exists(bin_path):
        print(f"BIN not found: {bin_path}\nPass the path as the 2nd argument.")
        sys.exit(1)

    with open(bin_path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        # Try the whole blob first, then shrinking prefixes — a shorter prefix still
        # pins the sector even if the tail differs (e.g. trailing RAM past the resource).
        for plen in [len(data), 16384, 8192, 4096, 2048, 1024, 512, 256]:
            if plen > len(data):
                continue
            sig = data[:plen]
            hits, i = [], mm.find(sig)
            while i >= 0 and len(hits) < 16:
                hits.append(i)
                i = mm.find(sig, i + 1)
            if hits:
                tag = "whole container" if plen == len(data) else f"first {plen} bytes"
                print(f"matched {tag} at {len(hits)} location(s):")
                for off in hits:
                    print(f"  offset 0x{off:08x} ({off})  ->  sector {off // SECTOR}"
                          f"  (+{off % SECTOR} within sector)")
                mm.close()
                return
        mm.close()

    print("no contiguous match. The container may be stored inside a 10-sector streaming\n"
          "block (interleaved with sector headers). Try a smaller capture, or decompress\n"
          "and rescan; the SLZ header should still appear at the block's data start.")


if __name__ == "__main__":
    main()
