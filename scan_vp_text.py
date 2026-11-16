#!/usr/bin/env python3
"""
Scan ALL SLZ files in VALKYRIE.BIN for VP Scheme A encoded text patterns.

VP Scheme A has TWO valid encodings for uppercase letters:
  - Glyph-based:  A=0x21..Z=0x3A (custom font table)
  - ASCII-based:  A=0x41..Z=0x5A (passthrough, since 0x20<=d<0x80)

Both decode to the same character. The known text files use ASCII-based encoding.
Patterns below cover both variants.

First byte encoding:  raw = decoded - 0x1F  (two variants per char)
Subsequent encoding:  raw = decoded + 1     (two variants per char)

We search for:
  LENNETH  - all-subseq (glyph): 2d262f2f263529
  LENNETH  - first+subseq (glyph): 0d262f2f263529
  LENNETH  - all-subseq (ASCII): 4d464f4f465549
  LENNETH  - first+subseq (ASCII): 2d464f4f465549
  ... and similarly for VALKYRIE, DROW, and other names
"""

import sys
import os
import struct

BIN_PATH = os.path.expanduser("~/Documents/VALKYRIE.BIN")
SECTOR = 2048

# VP patterns - both glyph-based and ASCII-based encodings, both first+subseq and all-subseq
PATTERNS = {
    # Glyph-based (glyph values 0x21-0x3A for A-Z)
    "LENNETH_glyph_full":    b'\x0d\x26\x2f\x2f\x26\x35\x29',
    "LENNETH_glyph_subseq":  b'\x2d\x26\x2f\x2f\x26\x35\x29',
    "VALKYRIE_glyph_full":   b'\x17\x22\x2d\x2c\x3a\x33\x2a\x26',
    "VALKYRIE_glyph_subseq": b'\x37\x22\x2d\x2c\x3a\x33\x2a\x26',
    "DROW_glyph_full":       b'\x05\x33\x30\x38',
    "DROW_glyph_subseq":     b'\x25\x33\x30\x38',
    # ASCII-based (ASCII values 0x41-0x5A for A-Z)
    "LENNETH_ascii_full":    b'\x2d\x46\x4f\x4f\x46\x55\x49',
    "LENNETH_ascii_subseq":  b'\x4d\x46\x4f\x4f\x46\x55\x49',
    "VALKYRIE_ascii_full":   b'\x37\x42\x4d\x4c\x5a\x53\x4a\x46',
    "VALKYRIE_ascii_subseq": b'\x57\x42\x4d\x4c\x5a\x53\x4a\x46',
    "DROW_ascii_full":       b'\x25\x53\x50\x58',
    "DROW_ascii_subseq":     b'\x45\x53\x50\x58',
    # Additional names - ASCII-based only (since that's what the known files use)
    "ALICIA_ascii_full":     b'\x22\x4d\x4a\x44\x4a\x42',
    "HRIST_ascii_full":      b'\x29\x53\x4a\x54\x55',
    "SILMERIA_ascii_full":   b'\x34\x4a\x4d\x4e\x46\x53\x4a\x42',
    "ODIN_ascii_full":       b'\x30\x45\x4a\x4f',
    "FREYA_ascii_full":      b'\x27\x53\x46\x5a\x42',
    "LUCIAN_ascii_full":     b'\x2d\x56\x44\x4a\x42\x4f',
    "BRAHMS_ascii_full":     b'\x23\x53\x42\x49\x4e\x54',
    "ARNGRIM_ascii_full":    b'\x22\x53\x4f\x48\x53\x4a\x4e',
    "JELANDA_ascii_full":    b'\x2b\x46\x4d\x42\x4f\x45\x42',
    "LEZARD_ascii_full":     b'\x2d\x46\x5b\x42\x53\x45',
    # Glyph-based additional names
    "ALICIA_glyph_full":     b'\x02\x2d\x2a\x24\x2a\x22',
    "HRIST_glyph_full":      b'\x09\x33\x2a\x34\x35',
    "SILMERIA_glyph_full":   b'\x14\x2a\x2d\x2e\x26\x33\x2a\x22',
    "ODIN_glyph_full":       b'\x10\x25\x2a\x2f',
    "FREYA_glyph_full":      b'\x07\x33\x26\x3a\x22',
    "BRAHMS_glyph_full":     b'\x03\x33\x22\x29\x2e\x34',
    "ARNGRIM_glyph_full":    b'\x02\x33\x2f\x28\x33\x2a\x2e',
}

def slz1_decompress(payload: bytes, max_out=None) -> bytes:
    out = bytearray()
    offset = 0
    flag_byte = 0
    bits_left = 0
    n = len(payload)
    while offset < n:
        if max_out is not None and len(out) >= max_out:
            break
        if bits_left == 0:
            flag_byte = payload[offset]; offset += 1; bits_left = 8
            if offset >= n:
                break
        bit = flag_byte & 1; flag_byte >>= 1; bits_left -= 1
        if bit == 1:
            out.append(payload[offset]); offset += 1
        else:
            if offset + 1 >= n:
                break
            b1 = payload[offset]; b2 = payload[offset + 1]; offset += 2
            v1 = ((b2 & 0xF) << 8) | b1
            v2 = (b2 >> 4) + 3
            if v1 == 0:
                break
            src = len(out) - v1
            for i in range(v2):
                ref = src + i
                out.append(out[ref] if 0 <= ref < len(out) else 0)
    return bytes(out)

def lz77_decompress_multi(data: bytes):
    out = bytearray()
    offset = 0
    n = len(data)
    streams = 0
    while offset < n:
        if offset >= n:
            break
        flag_byte = data[offset]; offset += 1
        bits_left = 8
        while bits_left > 0 and offset < n:
            bit = flag_byte & 1; flag_byte >>= 1; bits_left -= 1
            if bit == 1:
                out.append(data[offset]); offset += 1
            else:
                if offset + 1 >= n:
                    break
                b1 = data[offset]; b2 = data[offset + 1]; offset += 2
                v1 = ((b2 & 0xF) << 8) | b1
                v2 = (b2 >> 4) + 3
                if v1 == 0:
                    streams += 1
                    break
                src = len(out) - v1
                for i in range(v2):
                    ref = src + i
                    out.append(out[ref] if 0 <= ref < len(out) else 0)
    return bytes(out), streams

def slz_decompress(payload: bytes, slz_type: int) -> bytes:
    if slz_type == 1:
        return slz1_decompress(payload)
    out, _ = lz77_decompress_multi(payload)
    return out

def main():
    bin_size = os.path.getsize(BIN_PATH)
    print(f"BIN size: {bin_size:,} bytes ({bin_size // SECTOR:,} sectors)")
    print(f"Patterns to search: {len(PATTERNS)}")
    print()

    matches = []
    files_processed = 0
    files_skipped = 0
    files_found = 0

    CHUNK = 64 * 1024 * 1024  # 64 MB chunks
    overlap = 256  # overlap to catch headers spanning chunk boundaries

    with open(BIN_PATH, 'rb') as f:
        seen_offsets = set()
        pos = 0
        prev_progress_bucket = -1

        while pos < bin_size:
            f.seek(pos)
            chunk = f.read(CHUNK)
            if not chunk:
                break

            chunk_base = pos

            for magic, slz_type in [(b'SLZ\x01', 1), (b'SLZ\x02', 2)]:
                search_pos = 0
                while True:
                    idx = chunk.find(magic, search_pos)
                    if idx == -1:
                        break
                    abs_offset = chunk_base + idx

                    if abs_offset in seen_offsets:
                        search_pos = idx + 1
                        continue
                    seen_offsets.add(abs_offset)

                    # Parse 16-byte SLZ header
                    hdr_end = idx + 16
                    if hdr_end > len(chunk):
                        f.seek(abs_offset)
                        header = f.read(16)
                    else:
                        header = chunk[idx:hdr_end]

                    if len(header) < 16:
                        search_pos = idx + 1
                        continue

                    comp_size = struct.unpack_from('<I', header, 4)[0]
                    decomp_size = struct.unpack_from('<I', header, 8)[0]

                    # Filter: reasonable size range
                    if not (512 <= comp_size <= 2_000_000):
                        search_pos = idx + 1
                        continue

                    files_found += 1
                    files_processed += 1

                    # Read the compressed payload (comp_size INCLUDES the 16-byte header)
                    payload_size = comp_size - 16
                    payload_offset = abs_offset + 16
                    if payload_offset + payload_size > bin_size:
                        files_skipped += 1
                        search_pos = idx + 1
                        continue

                    f.seek(payload_offset)
                    payload = f.read(payload_size)

                    try:
                        decompressed = slz_decompress(payload, slz_type)
                    except Exception as e:
                        files_skipped += 1
                        search_pos = idx + 1
                        continue

                    if len(decompressed) < 4:
                        search_pos = idx + 1
                        continue

                    sector = abs_offset // SECTOR
                    offset_in_sector = abs_offset % SECTOR

                    for pattern_name, pattern in PATTERNS.items():
                        search_d = 0
                        while True:
                            found_at = decompressed.find(pattern, search_d)
                            if found_at == -1:
                                break
                            matches.append({
                                'bin_offset': abs_offset,
                                'sector': sector,
                                'offset_in_sector': offset_in_sector,
                                'slz_type': slz_type,
                                'comp_size': comp_size,
                                'decomp_size': decomp_size,
                                'pattern': pattern_name,
                                'decomp_pos': found_at,
                            })
                            print(f"  *** MATCH: {pattern_name} ***")
                            print(f"      BIN offset:       0x{abs_offset:08X} ({abs_offset:,})")
                            print(f"      Sector:           {sector} (offset {offset_in_sector} in sector)")
                            print(f"      SLZ type:         {slz_type}")
                            print(f"      comp/decomp size: {comp_size}/{decomp_size}")
                            print(f"      Pos in decomp:    {found_at} (0x{found_at:X})")
                            ctx_start = max(0, found_at - 24)
                            ctx = decompressed[ctx_start:found_at + len(pattern) + 24]
                            print(f"      Context hex:      {ctx.hex()}")
                            print()
                            search_d = found_at + 1

                    search_pos = idx + 1

            next_pos = pos + CHUNK - overlap
            curr_bucket = pos // (256 * 1024 * 1024)
            if curr_bucket != prev_progress_bucket:
                pct = 100.0 * pos / bin_size
                print(f"  [Progress] {pos/1e6:.0f} MB / {bin_size/1e6:.0f} MB ({pct:.1f}%)  "
                      f"found/processed={files_found} skipped={files_skipped} matches={len(matches)}",
                      flush=True)
                prev_progress_bucket = curr_bucket

            pos = next_pos

    print()
    print("=" * 60)
    print(f"SCAN COMPLETE")
    print(f"  SLZ headers found (size 512-2M): {files_found}")
    print(f"  Files skipped (error/truncated): {files_skipped}")
    print(f"  Total pattern matches:           {len(matches)}")
    print()

    if matches:
        print("ALL MATCHES SUMMARY:")
        for m in matches:
            print(f"  [{m['pattern']}] sector={m['sector']} offset={m['offset_in_sector']} "
                  f"slz_type={m['slz_type']} decomp_pos={m['decomp_pos']} "
                  f"comp={m['comp_size']} decomp={m['decomp_size']}")
    else:
        print("No matches found for any pattern.")

if __name__ == '__main__':
    main()
