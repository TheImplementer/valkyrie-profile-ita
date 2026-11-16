#!/usr/bin/env python3
"""VALKYRIE.BIN archive navigator — single-file local web app.

Usage: python3 bin_navigator.py [/path/to/VALKYRIE.BIN]
Defaults to ~/Documents/VALKYRIE.BIN. Opens browser at localhost:8765.
"""

import array
import bisect
import json
import math
import os
import struct
import sys
import threading
import webbrowser
import zlib
from functools import lru_cache
import socketserver
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ── constants ────────────────────────────────────────────────────────────────
SECTOR     = 2048
PORT       = 8765
INIT_KEY   = 0x64283921
MASK32     = 0xFFFFFFFF
MARKER     = 0x80010160   # structured sector marker (bytes 0-3)
BIN_MAGIC  = 0x19563824   # expected magic after XOR-decoding sector 0

BIN_PATH: Path = Path("~/Documents/VALKYRIE.BIN").expanduser()

# ── module-level state ───────────────────────────────────────────────────────
_fh = None                  # single open file handle
_sector_cache: dict = {}    # lba → bytes, capped at CACHE_MAX
CACHE_MAX = 64

# ── confirmed text-file catalog ───────────────────────────────────────────────
# Story/UI text locations established by reverse engineering (see DIALOGUE_SEARCH.md).
# byte_offset == sector * SECTOR + offset.  comp_size/decomp_size in bytes.
KNOWN_TEXT_FILES = [
    {"sector": 199,   "offset": 996,  "slz_type": 2, "comp_size": 5379,  "decomp_size": 7520,  "entries": 305,
     "content": "Menu text: NEW GAME, CONTINUE, character names, song titles"},
    {"sector": 84524, "offset": 1068, "slz_type": 2, "comp_size": 1031,  "decomp_size": 1256,  "entries": 128,
     "content": "UI"},
    {"sector": 84531, "offset": 1524, "slz_type": 1, "comp_size": 32639, "decomp_size": 62431, "entries": 2104,
     "content": "UI + skill help text"},
    {"sector": 84550, "offset": 1440, "slz_type": 1, "comp_size": 13019, "decomp_size": 22115, "entries": 1360,
     "content": "UI, \"SEND CHARACTER TO ASGARD\""},
    {"sector": 84559, "offset": 1676, "slz_type": 2, "comp_size": 5034,  "decomp_size": 9595,  "entries": 99,
     "content": "Skill names: FREYA, LUCIAN, SMASH AXE, etc."},
    {"sector": 84571, "offset": 1100, "slz_type": 1, "comp_size": 41321, "decomp_size": 77775, "entries": 2890,
     "content": "UI: items, spells, \"TO ODIN THE ALL-FATHER\""},
    {"sector": 84595, "offset": 1412, "slz_type": 1, "comp_size": 34335, "decomp_size": 64514, "entries": 2380,
     "content": "UI: items, descriptions"},
    {"sector": 84616, "offset": 544,  "slz_type": 1, "comp_size": 17054, "decomp_size": 29003, "entries": 1524,
     "content": "UI"},
    {"sector": 84632, "offset": 1860, "slz_type": 1, "comp_size": 13561, "decomp_size": 22926, "entries": 1367,
     "content": "UI, \"CANNOT REMOVE VALKYRIE\""},
    {"sector": 84644, "offset": 1056, "slz_type": 1, "comp_size": 28601, "decomp_size": 55400, "entries": 1813,
     "content": "UI + item descriptions"},
    {"sector": 84662, "offset": 304,  "slz_type": 2, "comp_size": 778,   "decomp_size": 1052,  "entries": 80,
     "content": "UI"},
    {"sector": 84669, "offset": 484,  "slz_type": 2, "comp_size": 1875,  "decomp_size": 2438,  "entries": 174,
     "content": "UI"},
    {"sector": 81713, "offset": 416,  "slz_type": 1, "comp_size": 2786,  "decomp_size": 4093,  "entries": 228,
     "content": "Small UI"},
    {"sector": 44303, "offset": 1408, "slz_type": 1, "comp_size": 21431, "decomp_size": 40819, "entries": 1541,
     "content": "Binary data — NOT readable text", "approx": True},
    {"sector": 84471, "offset": 0,    "slz_type": 2, "comp_size": None,  "decomp_size": None,  "entries": None,
     "content": "Small UI strings (sectors 84471–84474, mixed; unconfirmed offsets)", "approx": True},
]

# ── BIN parsing ──────────────────────────────────────────────────────────────

def lfsr_step(key: int) -> int:
    key = key ^ ((key << 1) & MASK32)
    key = (~key) & MASK32
    key = key ^ INIT_KEY
    return key


def read_at(byte_offset: int, n: int) -> bytes:
    """Atomic positioned read from the BIN.

    Uses os.pread so concurrent server threads never race on the shared file
    position (a plain seek+read on _fh is not thread-safe).
    """
    return os.pread(_fh.fileno(), n, byte_offset)


def read_sector_raw(lba: int) -> bytes:
    if lba in _sector_cache:
        return _sector_cache[lba]
    data = read_at(lba * SECTOR, SECTOR)
    if len(data) < SECTOR:
        data = data.ljust(SECTOR, b"\x00")
    if len(_sector_cache) >= CACHE_MAX:
        del _sector_cache[next(iter(_sector_cache))]
    _sector_cache[lba] = data
    return data


@lru_cache(maxsize=1)
def decode_sector0() -> bytes:
    raw = read_sector_raw(0)
    words = struct.unpack_from(f"<{SECTOR // 4}I", raw)
    key = INIT_KEY
    out = []
    for w in words:
        out.append(w ^ key)
        key = lfsr_step(key)
    result = struct.pack(f"<{len(out)}I", *out)
    magic = struct.unpack_from("<I", result, 0)[0]
    if magic != BIN_MAGIC:
        raise ValueError(f"bad sector-0 magic: 0x{magic:08X} (expected 0x{BIN_MAGIC:08X})")
    return result


@lru_cache(maxsize=1)
def parse_file_table() -> dict:
    dec = decode_sector0()

    # --- special entries from header words 1-7 (offsets 0x04-0x1C) ----------
    special = []
    for off in range(0x04, 0x24, 4):
        b = dec[off:off + 4]
        t = b[0]
        sec = b[1] | (b[2] << 8) | (b[3] << 16)
        if sec == 0:
            continue
        # validity: sector must be within file size
        max_sec = BIN_PATH.stat().st_size // SECTOR
        if sec >= max_sec:
            continue
        is_audio = _is_audio_sector(sec)
        special.append({"type": t, "sector": sec, "is_audio": is_audio})

    # --- regular entries from 0x24 onward ------------------------------------
    entries = []
    off = 0x24
    while off + 4 <= SECTOR:
        b = dec[off:off + 4]
        t = b[0]
        sec = b[1] | (b[2] << 8) | (b[3] << 16)
        if t not in (2, 3, 4) or sec == 0:
            break
        entries.append({"idx": len(entries), "type": t, "sector": sec, "is_audio": False})
        off += 4

    return {"entries": entries, "special": special}


@lru_cache(maxsize=1)
def _sorted_entry_starts() -> list:
    """Regular file-table entries sorted by start sector → (sector, idx) tuples."""
    entries = parse_file_table()["entries"]
    return sorted((e["sector"], e["idx"]) for e in entries)


def entry_for_sector(sector: int) -> dict | None:
    """File-table entry whose range contains ``sector``, or None if out of coverage.

    Files tile the disc in start-sector order, so the containing entry is the one
    with the greatest start ≤ sector.  Sectors at/after the last entry's start are
    treated as beyond table coverage (e.g. the 84xxx text cluster) and return None.
    """
    starts = _sorted_entry_starts()
    if not starts or sector < starts[0][0] or sector >= starts[-1][0]:
        return None
    secs = [s for s, _ in starts]
    j = bisect.bisect_right(secs, sector) - 1
    es, eidx = starts[j]
    return {"entry_idx": eidx, "entry_sector": es}


def _is_audio_sector(lba: int) -> bool:
    """Sector is audio if it lacks the 0x80010160 structured marker."""
    try:
        raw = read_sector_raw(lba)
        return struct.unpack_from("<I", raw, 0)[0] != MARKER
    except Exception:
        return False


def parse_sector_header(lba: int) -> dict:
    raw = read_sector_raw(lba)
    marker_val = struct.unpack_from("<I", raw, 0)[0]
    if marker_val != MARKER:
        return {
            "is_structured": False,
            "is_audio": True,
            "marker_hex": f"0x{marker_val:08X}",
        }
    sector_idx  = raw[4]
    block_size  = raw[6]
    block_num   = struct.unpack_from("<I", raw, 8)[0]
    payload_sz  = struct.unpack_from("<I", raw, 12)[0]
    extra0      = struct.unpack_from("<I", raw, 16)[0]
    preview     = raw[20:24].hex(" ")
    return {
        "is_structured": True,
        "is_audio": False,
        "marker_hex": f"0x{marker_val:08X}",
        "sector_idx": sector_idx,
        "block_size": block_size,
        "block_num": block_num,
        "payload_size": payload_sz,
        "extra0_hex": f"0x{extra0:08X}",
        "preview_hex": preview,
    }


def sector_entropy(data: bytes) -> float:
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    ent = 0.0
    for c in counts:
        if c:
            p = c / n
            ent -= p * math.log2(p)
    return round(ent, 3)


# ── TIM images ────────────────────────────────────────────────────────────────
# Standard PSX TIM format. Header: u32 magic 0x00000010, u32 flags (bits 0-1 =
# pixel mode 0/1/2/3 → 4/8/16/24-bpp, bit 3 = CLUT present). Optional CLUT block
# then the image block, each prefixed by a u32 byte-length. Paletted modes store
# BGR555 palette entries; 16-bpp is direct BGR555; 24-bpp is packed BGR888.

TIM_MAGIC = 0x00000010
_TIM_BPP  = {0: 4, 1: 8, 2: 16, 3: 24}
_TIM_MAX_DIM = 4096          # plausibility cap (also bounds the pure-Python decoder)


def _tim_pixel_width(mode: int, word_w: int) -> int:
    """TIM stores image width in 16-bit words; convert to pixels by mode."""
    if mode == 0: return word_w * 4      # 4-bpp: 4 px per word
    if mode == 1: return word_w * 2      # 8-bpp: 2 px per word
    if mode == 2: return word_w          # 16-bpp: 1 px per word
    if mode == 3: return (word_w * 2) // 3  # 24-bpp: 3 bytes per px, packed in words
    return word_w


def parse_tim_header(data: bytes, off: int = 0) -> dict | None:
    """Strictly validate a TIM at `off`; return metadata or None.

    The strictness here doubles as the false-positive filter for the scanner, so
    every structural inconsistency rejects the candidate.
    """
    if off < 0 or off + 8 > len(data):
        return None
    magic, flags = struct.unpack_from("<II", data, off)
    if magic != TIM_MAGIC:
        return None
    if flags & ~0xF:                      # reserved bits set → not a real TIM
        return None
    mode = flags & 0x3
    if mode not in _TIM_BPP:
        return None
    has_clut = bool(flags & 0x8)
    if mode == 3 and has_clut:            # 24-bpp never has a CLUT
        return None
    if mode in (0, 1) and not has_clut:   # paletted modes require a CLUT
        return None

    p = off + 8
    clut = None
    if has_clut:
        if p + 12 > len(data):
            return None
        bnum, _cox, _coy, cw, ch = struct.unpack_from("<IHHHH", data, p)
        if bnum < 12 or p + bnum > len(data):
            return None
        if cw == 0 or ch == 0 or cw * ch > 0x10000:
            return None
        if bnum < 12 + cw * ch * 2:       # declared block must cover the entries
            return None
        clut = {"entries": cw, "palettes": ch,
                "data_off": p + 12, "block_end": p + bnum}
        p += bnum

    if p + 12 > len(data):
        return None
    ibnum, _iox, _ioy, iw, ih = struct.unpack_from("<IHHHH", data, p)
    if ibnum < 12 or p + ibnum > len(data):
        return None
    if iw == 0 or ih == 0:
        return None
    if ibnum < 12 + iw * ih * 2:          # declared block must cover the pixels
        return None
    pix_w = _tim_pixel_width(mode, iw)
    if pix_w <= 0 or pix_w > _TIM_MAX_DIM or ih > _TIM_MAX_DIM:
        return None

    return {
        "offset":     off,
        "mode":       mode,
        "bpp":        _TIM_BPP[mode],
        "has_clut":   has_clut,
        "clut":       clut,                       # None or {entries,palettes,data_off,block_end}
        "clut_count": clut["palettes"] if clut else 0,
        "img_off":    p,                          # image block header start
        "pix_off":    p + 12,                     # raw pixel data start
        "word_w":     iw,                         # width in 16-bit words
        "width":      pix_w,
        "height":     ih,
        "total_size": (p + ibnum) - off,          # full TIM byte length
    }


def find_tims_in_data(data: bytes) -> list:
    """Find all valid TIMs in a buffer. Magic is word-aligned in practice, so we
    only test 4-byte-aligned offsets — faster and far fewer false positives."""
    out = []
    magic_le = struct.pack("<I", TIM_MAGIC)   # b"\x10\x00\x00\x00"
    n = len(data)
    i = 0
    while i + 8 <= n:
        j = data.find(magic_le, i)
        if j < 0:
            break
        if j % 4 == 0:
            t = parse_tim_header(data, j)
            if t:
                out.append(t)
                i = j + max(4, t["total_size"])   # skip past confirmed TIM
                continue
        i = j + 4
    return out


def scan_slz_for_tims(sector: int) -> list:
    """Probe a sector for SLZ-file headers, decompress each, and return any TIMs.

    Most of this game's art is SLZ-compressed (not raw and not in 10-sector
    blocks), so gallery/auto scans probe for the "SLZ" magic at 4-byte offsets
    within the sector, decompress sane-looking files, and scan the output.
    Returns a list of (byte_offset, tim_dict) where byte_offset is the SLZ file's
    absolute start (what load_text_file / the /api/image slz source expects).
    """
    raw = read_sector_raw(sector)
    out = []
    for o in range(0, SECTOR - 16, 4):
        if raw[o:o + 3] != b"SLZ" or raw[o + 3] not in (1, 2):
            continue
        comp   = struct.unpack_from("<I", raw, o + 4)[0]
        decomp = struct.unpack_from("<I", raw, o + 8)[0]
        if not (0 < comp < 4 * 1024 * 1024) or not (0 < decomp < 16 * 1024 * 1024):
            continue                                  # implausible header → skip
        byte_off = sector * SECTOR + o
        try:
            data = load_text_file(byte_off)["data"]
        except Exception:
            continue
        for t in find_tims_in_data(data):
            out.append((byte_off, t))
    return out


def _bgr555_to_rgba(v: int) -> tuple:
    """BGR555 u16 → (r, g, b, a). Value 0x0000 is the PSX transparent colour."""
    r = (v & 0x1F) << 3;          r |= r >> 5
    g = ((v >> 5) & 0x1F) << 3;   g |= g >> 5
    b = ((v >> 10) & 0x1F) << 3;  b |= b >> 5
    a = 0 if v == 0 else 255
    return (r, g, b, a)


def _clut_rgba(data: bytes, clut: dict, idx: int) -> list:
    """Decode one selectable palette (BGR555 entries) into RGBA tuples."""
    n   = clut["entries"]
    idx = max(0, min(idx, clut["palettes"] - 1))
    base = clut["data_off"] + idx * n * 2
    pal = []
    for k in range(n):
        off = base + k * 2
        v = struct.unpack_from("<H", data, off)[0] if off + 2 <= len(data) else 0
        pal.append(_bgr555_to_rgba(v))
    return pal


def decode_tim_rgba(data: bytes, tim: dict, clut_index: int = 0) -> tuple:
    """Decode a parsed TIM into (width, height, flat RGBA8 bytes)."""
    w, h = tim["width"], tim["height"]
    mode = tim["mode"]
    po   = tim["pix_off"]
    out  = bytearray(w * h * 4)

    if mode in (0, 1):                              # paletted
        pal  = _clut_rgba(data, tim["clut"], clut_index)
        plen = len(pal)
        stride = tim["word_w"] * 2                  # bytes per pixel row
        for y in range(h):
            row = po + y * stride
            for x in range(w):
                if mode == 1:                       # 8-bpp: 1 byte/pixel
                    bi = row + x
                    ci = data[bi] if bi < len(data) else 0
                else:                               # 4-bpp: low nibble = left pixel
                    bi = row + (x >> 1)
                    byte = data[bi] if bi < len(data) else 0
                    ci = (byte & 0x0F) if (x & 1) == 0 else (byte >> 4)
                r, g, b, a = pal[ci] if ci < plen else (0, 0, 0, 0)
                o = (y * w + x) * 4
                out[o]=r; out[o+1]=g; out[o+2]=b; out[o+3]=a

    elif mode == 2:                                 # 16-bpp direct BGR555
        for y in range(h):
            row = po + y * w * 2
            for x in range(w):
                off = row + x * 2
                v = struct.unpack_from("<H", data, off)[0] if off + 2 <= len(data) else 0
                r, g, b, a = _bgr555_to_rgba(v)
                o = (y * w + x) * 4
                out[o]=r; out[o+1]=g; out[o+2]=b; out[o+3]=a

    else:                                           # mode 3, 24-bpp BGR888
        stride = tim["word_w"] * 2
        for y in range(h):
            row = po + y * stride
            for x in range(w):
                off = row + x * 3
                if off + 3 <= len(data):
                    r, g, b = data[off], data[off+1], data[off+2]
                else:
                    r = g = b = 0
                o = (y * w + x) * 4
                out[o]=r; out[o+1]=g; out[o+2]=b; out[o+3]=255

    return w, h, bytes(out)


def encode_png(width: int, height: int, rgba: bytes) -> bytes:
    """Encode a flat RGBA8 buffer to PNG using only stdlib zlib."""
    def chunk(typ: bytes, body: bytes) -> bytes:
        c = typ + body
        return struct.pack(">I", len(body)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    sig    = b"\x89PNG\r\n\x1a\n"
    ihdr   = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    stride = width * 4
    raw    = bytearray()
    for y in range(height):
        raw.append(0)                              # filter type 0 (None)
        raw += rgba[y * stride:(y + 1) * stride]
    idat = zlib.compress(bytes(raw), 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def get_block_info(sector_lba: int) -> dict:
    hdr = parse_sector_header(sector_lba)
    if not hdr["is_structured"]:
        raise ValueError(f"sector {sector_lba} is not a structured block sector")
    sector_idx  = hdr["sector_idx"]
    block_start = sector_lba - sector_idx
    # read all 10 sectors
    sectors = []
    block_num    = 0
    payload_size = 0
    for i in range(10):
        lba = block_start + i
        h   = parse_sector_header(lba)
        is_padding = not h.get("is_structured") or h.get("sector_idx", 0) >= 8
        if i == 0 and h.get("is_structured"):
            block_num    = h["block_num"]
            payload_size = h["payload_size"]
        raw = read_sector_raw(lba)
        first4 = raw[32:36].hex(" ") if not is_padding else "ff ff ff ff"
        sectors.append({
            "lba":        lba,
            "idx":        h.get("sector_idx", i),
            "is_padding": is_padding,
            "first4_hex": first4,
        })
    return {
        "block_start":  block_start,
        "block_num":    block_num,
        "payload_size": payload_size,
        "sectors":      sectors,
    }


def lz77_decompress_multi(data: bytes) -> tuple:
    """Decompress all concatenated LZ77 streams into one output buffer.

    Returns (output_bytes, stream_list) where each stream dict has
    {start, end, output_len}.
    """
    out     = bytearray()
    streams = []
    pos     = 0
    while pos < len(data):
        stream_in_start  = pos
        stream_out_start = len(out)
        eos_hit = False
        while pos < len(data):
            if pos >= len(data):
                break
            flags = data[pos]; pos += 1
            for _ in range(8):
                if pos >= len(data):
                    break
                bit = flags & 1; flags >>= 1
                if bit:
                    out.append(data[pos]); pos += 1
                else:
                    if pos + 1 >= len(data):
                        break
                    b0 = data[pos]; b1 = data[pos + 1]; pos += 2
                    offset    = b0 | ((b1 & 0x0F) << 8)
                    copy_len  = (b1 >> 4) + 3
                    if offset == 0:
                        eos_hit = True
                        break
                    cp = len(out) - offset
                    for i in range(copy_len):
                        ref = cp + i
                        out.append(out[ref] if ref >= 0 else 0)
            if eos_hit:
                break
        streams.append({
            "start":      stream_in_start,
            "end":        pos,
            "output_len": len(out) - stream_out_start,
        })
    return bytes(out), streams


def _block_payload(sector: int) -> bytes:
    """Extract the raw LZ77 payload bytes from a structured 10-sector block."""
    blk = get_block_info(sector)
    payload = bytearray()
    for s in blk["sectors"]:
        if not s["is_padding"]:
            raw = read_sector_raw(s["lba"])
            payload.extend(raw[32:])
    return bytes(payload[:blk["payload_size"]])


def load_text_file(byte_offset: int, fallback_type: int = 2,
                   fallback_comp: int | None = None) -> dict:
    """Read and decompress an SLZ-headed text file at an absolute byte offset.

    Each text file begins with a 16-byte header: ASCII "SLZ" + type byte (1 or 2),
    u32 LE comp_size, u32 LE decomp_size, then padding; the compressed stream
    follows at +16.  These files are NOT 10-sector blocks, so we read the raw
    bytes directly (the catalog offsets were derived this way).  Returns
    {slz_type, comp_size, decomp_size, data}.
    """
    header = read_at(byte_offset, 16)
    if header[:3] == b"SLZ":
        slz_type    = header[3]
        comp_size   = struct.unpack_from("<I", header, 4)[0]
        decomp_size = struct.unpack_from("<I", header, 8)[0]
        stream = read_at(byte_offset + 16, comp_size + 64)  # +slack; decompressor self-terminates
        data   = slz_decompress(stream, slz_type)
        if decomp_size and len(data) > decomp_size:
            data = data[:decomp_size]
        return {"slz_type": slz_type, "comp_size": comp_size,
                "decomp_size": decomp_size, "data": data}
    # no SLZ header — treat the offset as a bare stream using catalog hints
    stream = read_at(byte_offset, fallback_comp or 64 * 1024)
    data   = slz_decompress(stream, fallback_type)
    return {"slz_type": fallback_type, "comp_size": fallback_comp,
            "decomp_size": len(data), "data": data}



# ── VP text files (confirmed format) ──────────────────────────────────────────
#
# Story/UI text in VALKYRIE.BIN is stored as an SLZ-compressed pointer table
# followed by VP-encoded null-terminated strings.  See DIALOGUE_SEARCH.md for the
# reverse-engineering notes that established these formats.

def slz1_decompress(payload: bytes, max_out: int | None = None) -> bytes:
    """Single-stream SLZ-1 (stops at the first end-of-stream marker, offset 0).

    Flag byte is read LSB-first; bit 1 = literal byte, bit 0 = back-reference
    (12-bit offset, 4-bit length+3).  This is the single-stream cousin of
    lz77_decompress_multi (which is the SLZ-2 / concatenated-stream variant).
    """
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
            if v1 == 0:        # end-of-stream
                break
            src = len(out) - v1
            for i in range(v2):
                ref = src + i
                out.append(out[ref] if 0 <= ref < len(out) else 0)
    return bytes(out)


def slz_decompress(payload: bytes, slz_type: int) -> bytes:
    """Decompress an SLZ payload; slz_type 1 = single-stream, 2 = multi-stream."""
    if slz_type == 1:
        return slz1_decompress(payload)
    out, _ = lz77_decompress_multi(payload)
    return out


def decode_vp_scheme_a(raw: bytes) -> str:
    """Decode one VP "Scheme A" string (used by every text file found so far).

    First byte: decoded = raw + 0x1F.  Subsequent bytes: decoded = raw - 1.
    decoded 0 -> SPACE, 0x0A -> newline; the custom font maps decoded 0x21-0x3A
    onto glyphs A-Z; 0x20-0x7F pass through; anything else shown as [xx].
    """
    result = []
    for j, b in enumerate(raw):
        d = (b + 0x1F) & 0xFF if j == 0 else (b - 1) & 0xFF
        if d == 0:
            result.append(' ')
        elif 0x21 <= d <= 0x3A:
            result.append(chr(ord('A') + d - 0x21))
        elif d == 0x0A:
            result.append('\n')
        elif 0x20 <= d < 0x80:
            result.append(chr(d))
        else:
            result.append(f"[{d:02x}]")
    return ''.join(result)


def _looks_readable(text: str) -> bool:
    """Heuristic: does a decoded string look like natural game text?

    Requires at least two letters and few undecodable [xx] escapes.  Filters out
    empty/single-char slots and binary data that leaks through the pointer table.
    """
    t = text.strip()
    if len(t) < 2 or sum(c.isalpha() for c in t) < 2:
        return False
    esc = 0
    i, n = 0, len(text)
    while i < n:                                   # count [xx] escape sequences
        if (text[i] == '[' and i + 3 < n and text[i + 3] == ']'
                and all(c in '0123456789abcdef' for c in text[i + 1:i + 3])):
            esc += 1; i += 4
        else:
            i += 1
    return esc * 4 < 0.3 * len(text)


def parse_text_file(dec: bytes) -> list:
    """Parse a decompressed VP text file into individual decoded strings.

    Layout: u32 LE text_section_offset, then a u32 LE offset array (each relative
    to text_section_offset) running until the read cursor reaches the text
    section, then the null-terminated VP-encoded string data.

    Each result is tagged ``readable``: True for the canonical (longest) string at
    a given null terminator that also looks like natural text.  Strings sharing a
    terminator are suffix fragments of one another (the pointer "staircase"), so
    only the longest is kept readable.
    """
    if len(dec) < 8:
        return []
    text_off = struct.unpack_from("<I", dec, 0)[0]
    if text_off < 4 or text_off > len(dec) or text_off % 4 != 0:
        return []
    strings = []
    idx = 0
    for p in range(4, text_off, 4):
        rel = struct.unpack_from("<I", dec, p)[0]
        start = text_off + rel
        if start >= len(dec):
            idx += 1
            continue
        end = dec.find(b"\x00", start)
        if end == -1:
            end = len(dec)
        raw = dec[start:end]
        strings.append({
            "index":      idx,
            "str_offset": start,
            "end_offset": end,
            "decoded":    decode_vp_scheme_a(raw),
            "raw_hex":    raw.hex(" "),
        })
        idx += 1

    # Canonical string per terminator = the one with the smallest start (longest).
    canonical: dict = {}
    for s in strings:
        cur = canonical.get(s["end_offset"])
        if cur is None or s["str_offset"] < cur["str_offset"]:
            canonical[s["end_offset"]] = s
    canon_ids = {id(s) for s in canonical.values()}
    for s in strings:
        s["readable"] = id(s) in canon_ids and _looks_readable(s["decoded"])
    return strings


# ── XA ADPCM decoder ─────────────────────────────────────────────────────────

SPU_F0 = [0, 60, 115, 98, 122]
SPU_F1 = [0,  0, -52, -55, -60]


def decode_spu_sector(sector_bytes: bytes, stereo: bool = False,
                      prev_L: list = None, prev_R: list = None) -> tuple:
    """Decode one 2048-byte SPU ADPCM sector to 16-bit PCM.

    Format: 128 × 16-byte blocks.
      byte 0: header — bits 3-0 = shift, bits 7-4 = filter index (0-4)
      byte 1: flags  — 0x00 = normal, 0x01 = loop end, 0x02 = loop start
      bytes 2-15: 14 bytes of 4-bit samples (28 nibbles, low nibble first)

    stereo=False (default): all 128 blocks → one mono channel (3584 samples).
    stereo=True: even blocks → left, odd blocks → right (1792 samples/ch).

    prev_L / prev_R are [current, previous] state lists updated in-place
    for seamless multi-sector playback.
    """
    if prev_L is None:
        prev_L = [0, 0]
    if prev_R is None:
        prev_R = [0, 0]
    pcm_L: list = []
    pcm_R: list = []
    n_blocks = len(sector_bytes) // 16
    for i in range(n_blocks):
        b     = i * 16
        hdr   = sector_bytes[b]
        shift = hdr & 0x0F
        filt  = min((hdr >> 4) & 0x0F, 4)
        f0    = SPU_F0[filt]
        f1    = SPU_F1[filt]
        if stereo:
            prev = prev_L if i % 2 == 0 else prev_R
            out  = pcm_L  if i % 2 == 0 else pcm_R
        else:
            prev, out = prev_L, pcm_L
        for k in range(14):
            byte = sector_bytes[b + 2 + k]
            for nib in (byte & 0x0F, (byte >> 4) & 0x0F):
                n = nib - 16 if nib >= 8 else nib
                p = (n << max(0, 12 - shift)) + ((f0 * prev[0] + f1 * prev[1] + 32) >> 6)
                p = max(-32768, min(32767, p))
                out.append(p)
                prev[1], prev[0] = prev[0], p
    return pcm_L, pcm_R


def make_wav_bytes(pcm_L: list, pcm_R: list, sample_rate: int = 37800) -> bytes:
    """Interleave PCM samples and wrap in a minimal WAV (PCM, 16-bit)."""
    channels = 2 if pcm_R else 1
    flat = [x for pair in zip(pcm_L, pcm_R) for x in pair] if channels == 2 else pcm_L
    data    = array.array("h", flat).tobytes()
    bal     = channels * 2
    byte_rt = sample_rate * bal
    fmt     = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1,
                          channels, sample_rate, byte_rt, bal, 16)
    dchk    = struct.pack("<4sI", b"data", len(data))
    riff    = struct.pack("<4sI4s", b"RIFF", 4 + len(fmt) + len(dchk) + len(data), b"WAVE")
    return riff + fmt + dchk + data


def sector_amplitude_fast(lba: int) -> int:
    """Quick amplitude estimate — partial ADPCM decode (32/128 blocks, state reset per sector).

    Returns 0-32767 peak value, or -1 for data sectors (marker present).
    Threshold ~3000 cleanly separates silence from audio content.
    """
    raw = read_sector_raw(lba)
    if struct.unpack_from("<I", raw, 0)[0] == MARKER:
        return -1
    peak = 0
    prev = [0, 0]
    for bi in range(0, 128, 4):          # every 4th block → 32 blocks sampled
        b     = bi * 16
        sh    = raw[b] & 0x0F
        filt  = min((raw[b] >> 4) & 0x0F, 4)
        f0    = SPU_F0[filt]
        f1    = SPU_F1[filt]
        for k in range(14):
            byte = raw[b + 2 + k]
            for nib in (byte & 0x0F, (byte >> 4) & 0x0F):
                n = nib - 16 if nib >= 8 else nib
                p = (n << max(0, 12 - sh)) + ((f0 * prev[0] + f1 * prev[1] + 32) >> 6)
                p = max(-32768, min(32767, p))
                if abs(p) > peak:
                    peak = abs(p)
                prev[1], prev[0] = prev[0], p
    return peak


# ── embedded HTML/CSS/JS ─────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>VALKYRIE.BIN Navigator</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0f172a;--surf:#1e293b;--surf2:#273548;--brd:#334155;--txt:#e2e8f0;--mut:#64748b;--acc:#6366f1}
body{background:var(--bg);color:var(--txt);font-family:'Cascadia Code','Fira Code',monospace;font-size:13px;
  display:grid;grid-template-rows:48px 1fr;grid-template-columns:280px 1fr 340px;height:100vh;overflow:hidden}
#topbar{grid-column:1/-1;background:var(--surf);border-bottom:1px solid var(--brd);
  display:flex;align-items:center;gap:16px;padding:0 16px}
#topbar .title{font-size:15px;font-weight:700;color:var(--acc)}
#topbar .fileinfo{color:var(--mut);font-size:12px}
#left{overflow-y:auto;border-right:1px solid var(--brd);display:flex;flex-direction:column}
#center{overflow-y:auto;border-right:1px solid var(--brd);padding:16px}
#right{overflow-y:auto;padding:12px}
.panel-hdr{padding:10px 12px;background:var(--surf);border-bottom:1px solid var(--brd);
  font-size:11px;font-weight:700;letter-spacing:.08em;color:var(--mut);text-transform:uppercase;position:sticky;top:0;z-index:1}
.section-label{padding:6px 12px 4px;font-size:10px;color:var(--mut);letter-spacing:.06em;text-transform:uppercase;border-top:1px solid var(--brd)}
#filter-wrap{padding:8px 10px;background:var(--surf);border-bottom:1px solid var(--brd)}
#filter{width:100%;background:var(--surf2);border:1px solid var(--brd);color:var(--txt);
  border-radius:4px;padding:5px 8px;font-size:12px;outline:none}
#filter:focus{border-color:var(--acc)}
#filter::placeholder{color:var(--mut)}
.entry-row{display:flex;align-items:center;gap:8px;padding:6px 12px;cursor:pointer;border-bottom:1px solid #1e293b}
.entry-row:hover{background:var(--surf2)}
.entry-row.active{background:#1e3a5f}
.badge{font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px;flex-shrink:0}
.bt2{background:#1e3a5f;color:#7dd3fc}.bt3{background:#14532d;color:#86efac}
.bt4{background:#431407;color:#fb923c}.baudio{background:#450a0a;color:#fca5a5}
.bt0{background:#312e81;color:#c4b5fd}
.row-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}
.row-sub{color:var(--mut);font-size:11px;margin-left:auto;flex-shrink:0}
.empty{padding:20px 12px;color:var(--mut);font-size:12px;text-align:center}
/* center panel */
.blk-hdr{margin-bottom:14px}
.blk-hdr h2{font-size:15px;font-weight:700;margin-bottom:4px}
.blk-hdr .sub{color:var(--mut);font-size:12px}
.sec-table{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:16px}
.sec-table th{text-align:left;padding:6px 10px;background:var(--surf);color:var(--mut);
  font-size:10px;text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid var(--brd)}
.sec-table td{padding:6px 10px;border-bottom:1px solid #1e293b;cursor:pointer}
.sec-table tr:hover td{background:var(--surf2)}
.sec-table tr.active td{background:#1e3a5f}
.sec-table .pad{opacity:.35}
.sec-table .mono{font-size:11px;color:var(--mut)}
.decomp-btn{background:var(--acc);color:#fff;border:none;padding:7px 18px;border-radius:5px;
  cursor:pointer;font-size:13px;font-family:inherit;margin-bottom:14px}
.decomp-btn:disabled{opacity:.5;cursor:default}
.decomp-result{background:var(--surf);border:1px solid var(--brd);border-radius:6px;padding:12px;font-size:12px}
.decomp-result .stat{display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap}
.stat-chip{background:var(--surf2);padding:4px 10px;border-radius:12px;color:var(--txt)}
.stat-chip span{color:var(--mut)}
.stream-list{margin-top:8px;max-height:120px;overflow-y:auto}
.stream-row{display:flex;gap:12px;padding:3px 0;font-size:11px;color:var(--mut);border-bottom:1px solid #1e293b}
.stream-row .si{color:var(--txt);min-width:24px}
.preview-hex{font-size:10px;color:#86efac;margin-top:8px;word-break:break-all;line-height:1.6}
/* right panel */
.sec-preview h3{font-size:13px;font-weight:700;margin-bottom:10px}
.ent-bar-wrap{margin-bottom:12px}
.ent-label{font-size:10px;color:var(--mut);margin-bottom:4px;display:flex;justify-content:space-between}
.ent-bar-bg{background:var(--surf2);border-radius:4px;height:8px;overflow:hidden}
.ent-bar{height:100%;border-radius:4px;transition:width .3s}
.hdr-fields{background:var(--surf);border:1px solid var(--brd);border-radius:6px;padding:10px;margin-bottom:12px;font-size:11px}
.hdr-fields .field{display:flex;gap:6px;padding:3px 0;border-bottom:1px solid #1e293b}
.hdr-fields .field:last-child{border:none}
.fld-name{color:var(--mut);min-width:90px}
.fld-val{color:#7dd3fc}
.fld-bytes{color:var(--mut);font-size:10px;margin-left:auto}
.hex-dump{font-size:11px;line-height:1.8}
.hex-row{display:flex;gap:8px;padding:1px 0}
.hex-row.hdr-hi{background:#1e3a5f;border-radius:2px}
.hex-addr{color:var(--mut);min-width:38px;user-select:none}
.hex-bytes{color:#cbd5e1;flex:1;word-break:break-all}
.hex-ascii{color:var(--mut);min-width:100px;letter-spacing:.02em}
.audio-badge{background:#450a0a;color:#fca5a5;padding:4px 12px;border-radius:12px;
  font-weight:700;font-size:12px;display:inline-block;margin-bottom:12px}
.nibble-chart{display:flex;align-items:flex-end;gap:2px;height:60px;margin-bottom:8px;background:var(--surf);
  border-radius:4px;padding:4px 6px;border:1px solid var(--brd)}
.nibble-bar{flex:1;background:var(--acc);border-radius:2px 2px 0 0;min-height:2px}
.nibble-labels{display:flex;gap:2px;font-size:9px;color:var(--mut);padding:0 6px}
.nibble-labels span{flex:1;text-align:center}
.placeholder{color:var(--mut);font-size:12px;padding:20px 0;text-align:center}
.search-btn{background:var(--surf2);border:1px solid var(--brd);color:var(--txt);
  padding:5px 14px;border-radius:4px;font-size:12px;font-family:inherit;cursor:pointer}
.search-btn:hover{border-color:var(--acc)}
.search-bar{display:flex;gap:8px;margin-bottom:16px}
.search-input{flex:1;background:var(--surf2);border:1px solid var(--brd);color:var(--txt);
  border-radius:4px;padding:7px 10px;font-size:13px;font-family:inherit;outline:none}
.search-input:focus{border-color:var(--acc)}
.search-go{background:var(--acc);color:#fff;border:none;padding:7px 16px;border-radius:4px;
  cursor:pointer;font-size:13px;font-family:inherit}
.search-go:disabled{opacity:.5;cursor:default}
.match-row{padding:10px 12px;border-bottom:1px solid var(--brd);cursor:pointer}
.match-row:hover{background:var(--surf2)}
.match-meta{font-size:11px;color:var(--mut);margin-bottom:4px}
.match-ctx{font-size:12px;color:#e2e8f0;word-break:break-all;line-height:1.6}
.match-ctx .hl{background:#fbbf24;color:#000;border-radius:2px;padding:0 1px}
.cand-row{padding:10px 12px;border-bottom:1px solid var(--brd);cursor:pointer}
.cand-row:hover{background:var(--surf2)}
.cand-meta{font-size:11px;color:var(--mut);margin-bottom:5px}
.cand-bars{display:flex;align-items:flex-end;gap:2px;height:40px}
.cand-bar-wrap{display:flex;flex-direction:column;align-items:center;flex:1;max-width:28px}
.cand-bar{width:100%;background:var(--acc);border-radius:2px 2px 0 0;min-height:2px}
.cand-bar-lbl{font-size:9px;color:var(--mut);margin-top:2px}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid var(--brd);
  border-top-color:var(--acc);border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
.audio-player{background:var(--surf);border:1px solid var(--brd);border-radius:8px;padding:14px;margin-bottom:14px}
.audio-meta{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.audio-meta .chip{background:var(--surf2);padding:3px 9px;border-radius:10px;font-size:11px;color:var(--mut)}
.audio-meta .chip b{color:var(--txt)}
.audio-ctrls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.play-btn{background:var(--acc);color:#fff;border:none;padding:7px 16px;border-radius:5px;cursor:pointer;
  font-size:12px;font-family:inherit;display:flex;align-items:center;gap:6px;white-space:nowrap}
.play-btn:disabled{opacity:.5;cursor:default}
.xa-ctrl-select{background:var(--surf2);border:1px solid var(--brd);color:var(--txt);
  padding:4px 8px;border-radius:4px;font-size:11px;outline:none;font-family:inherit}
.xa-ctrl-label{font-size:11px;color:var(--mut);display:flex;align-items:center;gap:4px;cursor:pointer;user-select:none}
.xa-audio{width:100%;margin-top:6px;display:none;accent-color:var(--acc)}
.xa-status{font-size:11px;color:var(--mut);margin-top:5px;min-height:14px}
.waveform-wrap{position:relative;background:var(--surf);border:1px solid var(--brd);
  border-radius:6px;margin-bottom:10px;height:76px;overflow:hidden;cursor:crosshair}
#waveform-cv{display:block;width:100%;height:76px}
.waveform-loading{position:absolute;inset:0;display:flex;align-items:center;
  justify-content:center;font-size:11px;color:var(--mut);pointer-events:none}
.wf-cursor{position:absolute;top:0;bottom:0;width:2px;background:var(--acc);
  pointer-events:none;display:none;z-index:2}
.track-list{max-height:120px;overflow-y:auto;margin-bottom:10px;
  border:1px solid var(--brd);border-radius:6px;font-size:11px}
.track-row{display:flex;align-items:center;gap:8px;padding:5px 10px;
  border-bottom:1px solid #1e293b;cursor:pointer}
.track-row:last-child{border:none}
.track-row:hover{background:var(--surf2)}
.track-row.active{background:#1e3a5f}
.track-row .ti{color:var(--mut);min-width:28px}
.track-row .td{color:var(--txt);flex:1}
.track-row .ts{color:var(--mut);font-size:10px}
/* images */
.bimg{background:#3b0764;color:#e9d5ff}
.tim-list{border:1px solid var(--brd);border-radius:6px;margin-bottom:14px;max-height:220px;overflow-y:auto}
.tim-row{display:flex;align-items:center;gap:8px;padding:6px 10px;border-bottom:1px solid #1e293b;cursor:pointer}
.tim-row:last-child{border:none}
.tim-row:hover{background:var(--surf2)}
.tim-row.active{background:#1e3a5f}
.img-viewer{background:var(--surf);border:1px solid var(--brd);border-radius:8px;padding:14px;margin-bottom:14px}
.img-meta{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.img-meta .chip{background:var(--surf2);padding:3px 9px;border-radius:10px;font-size:11px;color:var(--mut)}
.img-meta .chip b{color:var(--txt)}
.img-stage{background:repeating-conic-gradient(#1a2335 0% 25%,#141c2b 0% 50%) 50%/16px 16px;
  border:1px solid var(--brd);border-radius:6px;overflow:auto;max-height:520px;display:flex;
  align-items:center;justify-content:center;padding:10px}
.img-stage img{image-rendering:pixelated;display:block}
.img-ctrls{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:10px}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:10px;margin-top:12px}
.gallery-cell{background:var(--surf);border:1px solid var(--brd);border-radius:6px;padding:6px;cursor:pointer;text-align:center}
.gallery-cell:hover{border-color:var(--acc)}
.gallery-cell .thumb{background:repeating-conic-gradient(#1a2335 0% 25%,#141c2b 0% 50%) 50%/10px 10px;
  border-radius:4px;height:80px;display:flex;align-items:center;justify-content:center;overflow:hidden}
.gallery-cell .thumb img{image-rendering:pixelated;max-width:100%;max-height:80px}
.gallery-cell .cap{font-size:10px;color:var(--mut);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
</style>
</head>
<body>
<div id="topbar">
  <span class="title">VALKYRIE.BIN Navigator</span>
  <span id="fileinfo" class="fileinfo">loading…</span>
  <button class="search-btn" onclick="showStoryText()" style="margin-left:auto">Story text</button>
  <button class="search-btn" onclick="showSearch()">Search text</button>
  <button class="search-btn" onclick="showImageScan()">Scan images</button>
</div>
<div id="left">
  <div id="filter-wrap"><input id="filter" placeholder="filter: t:2  t:3  t:4  or sector #…" autocomplete="off"></div>
  <div id="special-section"></div>
  <div id="entry-list"><div class="empty">Loading file table…</div></div>
</div>
<div id="center"><div class="placeholder">← Select an entry to inspect its block</div></div>
<div id="right"><div class="placeholder">← Select a sector row to view its hex dump</div></div>

<script>
let allEntries = [], specialEntries = [], activeEntry = null, activeSector = null, currentBlockSector = null;

async function api(path) {
  const r = await fetch(path);
  return r.json();
}

function badge(type, isAudio) {
  if (type === 'img') return '<span class="badge bimg">IMG</span>';
  if (isAudio) return '<span class="badge baudio">audio</span>';
  const cls = {2:'bt2',3:'bt3',4:'bt4',0:'bt0'}[type] || 'bt0';
  return `<span class="badge ${cls}">t${type}</span>`;
}

function formatSize(bytes) {
  if (bytes >= 1e9) return (bytes/1e9).toFixed(2)+' GB';
  if (bytes >= 1e6) return (bytes/1e6).toFixed(1)+' MB';
  return (bytes/1024).toFixed(0)+' KB';
}

function entropyColor(e) {
  const t = Math.min(e / 8, 1);
  const r = Math.round(59 + 196*t), g = Math.round(130 - 83*t), b = Math.round(246 - 178*t);
  return `rgb(${r},${g},${b})`;
}

// ── file table ───────────────────────────────────────────────────────────────
async function loadFileTable() {
  const d = await api('/api/filetable');
  allEntries = d.entries || [];
  specialEntries = d.special || [];
  document.getElementById('fileinfo').textContent =
    `${d.filename}  ·  ${formatSize(d.file_size)}  ·  ${d.sector_count.toLocaleString()} sectors`;
  renderSpecial(specialEntries);
  renderEntries(allEntries);
}

function renderSpecial(sp) {
  const el = document.getElementById('special-section');
  if (!sp.length) { el.innerHTML=''; return; }
  let h = `<div class="panel-hdr">Special</div>`;
  sp.forEach((e,i) => {
    h += `<div class="entry-row" data-sector="${e.sector}" data-special="1">
      ${badge(e.type, e.is_audio)}
      <span class="row-text">${e.is_audio ? 'audio' : 'overlay'} ${i}</span>
      <span class="row-sub">${e.sector.toLocaleString()}</span>
    </div>`;
  });
  el.innerHTML = h;
}

function renderEntries(entries) {
  const el = document.getElementById('entry-list');
  if (!entries.length) { el.innerHTML='<div class="empty">No entries match.</div>'; return; }
  const counts = {2:0,3:0,4:0};
  entries.forEach(e => { counts[e.type] = (counts[e.type]||0)+1; });
  let h = `<div class="panel-hdr">${entries.length} entries`;
  const typeSummary = Object.entries(counts).filter(([,v])=>v).map(([k,v])=>`t${k}:${v}`).join(' ');
  h += ` &nbsp;<span style="color:var(--mut);font-weight:400">${typeSummary}</span></div>`;
  entries.forEach(e => {
    h += `<div class="entry-row" data-sector="${e.sector}" data-idx="${e.idx}">
      ${badge(e.type, false)}
      <span class="row-text">entry ${e.idx}</span>
      <span class="row-sub">${e.sector.toLocaleString()}</span>
    </div>`;
  });
  el.innerHTML = h;
  // re-apply active highlight
  if (activeEntry !== null) {
    const el2 = document.querySelector(`#entry-list [data-idx="${activeEntry}"]`);
    if (el2) el2.classList.add('active');
  }
}

// event delegation
document.getElementById('left').addEventListener('click', e => {
  const row = e.target.closest('.entry-row');
  if (!row) return;
  document.querySelectorAll('.entry-row.active').forEach(r => r.classList.remove('active'));
  row.classList.add('active');
  const sec = parseInt(row.dataset.sector);
  if (row.dataset.special) {
    activeEntry = null;
    loadBlockOrAudio(sec, true);
  } else {
    activeEntry = parseInt(row.dataset.idx);
    loadBlockOrAudio(sec, false);
  }
});

document.getElementById('filter').addEventListener('input', function() {
  const q = this.value.trim().toLowerCase();
  if (!q) { renderEntries(allEntries); return; }
  let filtered;
  if (q.startsWith('t:') || q.startsWith('type:')) {
    const t = parseInt(q.split(':')[1]);
    filtered = allEntries.filter(e => e.type === t);
  } else {
    filtered = allEntries.filter(e => String(e.sector).includes(q) || String(e.idx).includes(q));
  }
  renderEntries(filtered);
});

// ── block / audio loading ────────────────────────────────────────────────────
async function loadBlockOrAudio(sector, isSpecial) {
  document.getElementById('center').innerHTML = '<div class="placeholder"><span class="spinner"></span>Loading…</div>';
  document.getElementById('right').innerHTML = '<div class="placeholder">← Select a sector row</div>';
  activeSector = null;
  try {
    // check if audio
    const sh = await api(`/api/sector?n=${sector}`);
    if (!sh.is_structured) {
      renderAudioCenter(sector, sh);
      renderAudioRight(sector, sh);
      scanAndShowImages(sector, 'raw');
      return;
    }
    const blk = await api(`/api/block?sector=${sector}`);
    currentBlockSector = sector;
    renderBlockCenter(blk);
    scanAndShowImages(sector, 'block');
  } catch(err) {
    document.getElementById('center').innerHTML = `<div class="placeholder" style="color:#fca5a5">Error: ${err.message}</div>`;
  }
}

let _xaBaseSector  = 0;
let _xaPlayFrom    = null;
let _xaObjUrl      = null;
let _wfPxPerSector = 1;
let _wfData        = null;

function renderAudioCenter(sector, sh) {
  const ent = sh.entropy || 0;
  _xaBaseSector = sector;
  _xaPlayFrom   = sector;
  _wfData       = null;
  document.getElementById('center').innerHTML = `
    <div class="blk-hdr">
      <h2><span class="badge baudio" style="font-size:13px;padding:4px 12px">SPU ADPCM Audio</span></h2>
      <div class="sub" style="margin-top:8px">Sector ${sector.toLocaleString()}</div>
    </div>
    <div class="ent-bar-wrap">
      <div class="ent-label"><span>Entropy</span><span>${ent} bits/byte</span></div>
      <div class="ent-bar-bg"><div class="ent-bar" style="width:${ent/8*100}%;background:${entropyColor(ent)}"></div></div>
    </div>
    <div class="waveform-wrap" id="waveform-wrap">
      <div class="waveform-loading"><span class="spinner"></span>Scanning waveform…</div>
      <canvas id="waveform-cv"></canvas>
      <div class="wf-cursor" id="wf-cursor"></div>
    </div>
    <div class="track-list" id="track-list"></div>
    <div class="audio-player">
      <div class="audio-meta">
        <span class="chip">SPU ADPCM 4-bit</span>
        <span class="chip"><b>128</b> blocks/sector</span>
        <span class="chip"><b>3,584</b> samples/sector (mono)</span>
      </div>
      <div class="audio-ctrls">
        <button class="play-btn" id="play-btn" onclick="playXA()">▶ Decode &amp; play</button>
        <label class="xa-ctrl-label">
          <input type="checkbox" id="xa-stereo" style="accent-color:var(--acc)"> Stereo interleaved
        </label>
        <select id="xa-rate" class="xa-ctrl-select">
          <option value="22050" selected>22,050 Hz</option>
          <option value="44100">44,100 Hz</option>
          <option value="11025">11,025 Hz</option>
          <option value="32000">32,000 Hz</option>
        </select>
      </div>
      <audio id="xa-audio" class="xa-audio" controls></audio>
      <div id="xa-status" class="xa-status">Click waveform or a track to seek. Decodes 500 sectors per play.</div>
    </div>`;
  renderSectorRight(sector, sh);
  fetchWaveform(sector);
}

function renderAudioRight(sector, sh) {
  renderSectorRight(sector, sh);
}

// ── waveform ──────────────────────────────────────────────────────────────────
const WF_TRACK_COLORS = ['#6366f1','#22d3ee','#a78bfa','#34d399','#f472b6','#fb923c'];

async function fetchWaveform(sector) {
  try {
    const d = await api(`/api/waveform?sector=${sector}`);
    _wfData = d;
    drawWaveform(d, sector);
    renderTrackList(d.tracks);
  } catch(e) {
    const w = document.getElementById('waveform-wrap');
    if (w) w.innerHTML = '<div class="waveform-loading" style="color:#fca5a5">Waveform scan failed</div>';
  }
}

function drawWaveform(data, baseSector) {
  const wrap = document.getElementById('waveform-wrap');
  const cv   = document.getElementById('waveform-cv');
  if (!wrap || !cv) return;

  // clear loading overlay
  const loader = wrap.querySelector('.waveform-loading');
  if (loader) loader.remove();

  const W  = wrap.clientWidth  || 600;
  const H  = cv.clientHeight   || 76;
  cv.width  = W;
  cv.height = H;

  const amps  = data.amplitudes;
  const total = amps.length;
  _wfPxPerSector = W / total;

  // build per-pixel track colour lookup
  const trackColor = new Array(total).fill('#2d3748');  // silence colour
  (data.tracks || []).forEach((t, ti) => {
    const col = WF_TRACK_COLORS[ti % WF_TRACK_COLORS.length];
    for (let i = t.start - baseSector; i <= t.end - baseSector && i < total; i++) {
      if (i >= 0) trackColor[i] = col;
    }
  });

  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, W, H);

  // draw in screen-pixel buckets (average amplitude)
  for (let px = 0; px < W; px++) {
    const s0 = Math.floor(px / _wfPxPerSector);
    const s1 = Math.min(total - 1, Math.floor((px + 1) / _wfPxPerSector));
    let maxA = 0;
    for (let s = s0; s <= s1; s++) maxA = Math.max(maxA, amps[s] || 0);
    const barH = Math.max(1, Math.round(maxA / 32767 * H));
    const col  = trackColor[s0] || '#2d3748';
    ctx.fillStyle = col + '99';  // translucent fill
    ctx.fillRect(px, H - barH, 1, barH);
    ctx.fillStyle = col;
    ctx.fillRect(px, H - barH, 1, 2);  // bright top edge
  }

  // click → seek
  cv.onclick = (e) => {
    const rect = cv.getBoundingClientRect();
    const px   = e.clientX - rect.left;
    const sec  = baseSector + Math.round(px / _wfPxPerSector);
    _xaPlayFrom = sec;
    // move cursor
    const cursor = document.getElementById('wf-cursor');
    if (cursor) { cursor.style.left = px + 'px'; cursor.style.display = 'block'; }
    const status = document.getElementById('xa-status');
    if (status) status.textContent = `Start: sector ${sec.toLocaleString()} · click ▶ to play`;
    // highlight track row
    const offset = sec - baseSector;
    const track  = (data.tracks || []).find(t => offset >= t.start - baseSector && offset <= t.end - baseSector);
    document.querySelectorAll('.track-row').forEach(r => r.classList.remove('active'));
    if (track !== undefined) {
      const row = document.querySelector(`.track-row[data-idx="${track.idx}"]`);
      if (row) row.classList.add('active');
    }
  };
}

function renderTrackList(tracks) {
  const el = document.getElementById('track-list');
  if (!el) return;
  if (!tracks || !tracks.length) {
    el.innerHTML = '<div class="track-row"><span style="color:var(--mut)">No tracks detected</span></div>';
    return;
  }
  el.innerHTML = tracks.map(t => `
    <div class="track-row" data-idx="${t.idx}" data-sector="${t.start}"
         onclick="seekToTrack(${t.start}, ${t.idx})">
      <span class="ti">#${t.idx + 1}</span>
      <span class="td">${t.duration_s}s</span>
      <span class="ts">sec ${t.start.toLocaleString()} · ${t.sector_count} sectors</span>
    </div>`).join('');
}

function seekToTrack(sector, idx) {
  _xaPlayFrom = sector;
  document.querySelectorAll('.track-row').forEach(r => r.classList.remove('active'));
  const row = document.querySelector(`.track-row[data-idx="${idx}"]`);
  if (row) row.classList.add('active');
  // move canvas cursor
  if (_wfData) {
    const cv = document.getElementById('waveform-cv');
    const offset = sector - _xaBaseSector;
    const px = Math.round(offset * _wfPxPerSector);
    const cursor = document.getElementById('wf-cursor');
    if (cursor) { cursor.style.left = px + 'px'; cursor.style.display = 'block'; }
  }
  const status = document.getElementById('xa-status');
  if (status) status.textContent = `Track #${idx+1} · sector ${sector.toLocaleString()} · click ▶ to play`;
}

// ── playback ──────────────────────────────────────────────────────────────────
function playXA() {
  const sector = _xaPlayFrom ?? _xaBaseSector;
  const btn    = document.getElementById('play-btn');
  const audio  = document.getElementById('xa-audio');
  const status = document.getElementById('xa-status');
  const stereo = document.getElementById('xa-stereo').checked ? '1' : '0';
  const rate   = document.getElementById('xa-rate').value;

  if (_xaObjUrl) { URL.revokeObjectURL(_xaObjUrl); _xaObjUrl = null; }
  audio.style.display = 'none';
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Decoding…';
  status.style.color = 'var(--mut)';
  status.textContent = `Decoding from sector ${sector.toLocaleString()}…`;

  fetch(`/api/audio?sector=${sector}&stereo=${stereo}&rate=${rate}`)
    .then(r => {
      if (!r.ok) return r.json().then(j => { throw new Error(j.error || r.status); });
      const len = parseInt(r.headers.get('Content-Length') || '0');
      return r.blob().then(blob => ({ blob, len }));
    })
    .then(({ blob, len }) => {
      _xaObjUrl = URL.createObjectURL(blob);
      audio.src = _xaObjUrl;
      audio.style.display = 'block';
      audio.play().catch(() => {});
      btn.disabled = false;
      btn.innerHTML = '▶ Decode &amp; play';
      const ch  = stereo === '1' ? 2 : 1;
      const rt  = parseInt(rate);
      const smp = len > 44 ? Math.round((len - 44) / (ch * 2)) : 0;
      const dur = (smp / rt).toFixed(2);
      const kb  = (len / 1024).toFixed(0);
      status.textContent = `${kb} KB WAV · ${dur}s · sec ${sector.toLocaleString()}`;
    })
    .catch(err => {
      btn.disabled = false;
      btn.innerHTML = '▶ Decode &amp; play';
      status.style.color = '#fca5a5';
      status.textContent = `Error: ${err.message}`;
    });
}

// ── TIM images ────────────────────────────────────────────────────────────────
let _imgObjUrl = null, _imgZoom = 3, _imgNatW = 0, _imgNatH = 0;
let _imgCtx = null;          // {sector, source, byteOffset, offset, clutCount}
let _timImages = [];

function imgQuery(sector, source, offset, clut, byteOffset) {
  let q = `/api/image?sector=${sector}&source=${source}&offset=${offset}&clut=${clut||0}`;
  if (source === 'slz' && byteOffset != null) q += `&byte_offset=${byteOffset}`;
  return q;
}

// Best-effort scan triggered on entry/block open; only prepends on a hit.
async function scanAndShowImages(sector, source) {
  try {
    const d = await api(`/api/scan_images?sector=${sector}&source=${source}`);
    if (!d.count) return;
    const center = document.getElementById('center');
    const wrap = document.createElement('div');
    wrap.innerHTML = renderImageList(sector, d.source, d.images, sector*2048);
    center.insertBefore(wrap, center.firstChild);
  } catch(e) { /* silent — scan is best-effort */ }
}

function imgByteOffset(t) {
  return (t.byte_offset != null) ? t.byte_offset : t.sector * 2048;
}

function renderImageList(sector, source, images, byteOffset) {
  _timImages = images;
  const rows = images.map((t,i) => `
    <div class="tim-row" data-i="${i}" onclick="showTim(${i})">
      ${badge('img')}
      <span class="row-text">${t.bpp}bpp · ${t.width}×${t.height}</span>
      <span class="row-sub">${t.source} @${t.offset}${t.clut_count>1?` · ${t.clut_count} pal`:''}</span>
    </div>`).join('');
  return `<div class="blk-hdr"><h2>${images.length} TIM image${images.length>1?'s':''} found</h2>
            <div class="sub">source: ${source}</div></div>
          <div class="tim-list">${rows}</div>
          <div id="img-viewer-host"></div>`;
}

function showTim(i) {
  const t = _timImages[i];
  if (!t) return;
  document.querySelectorAll('.tim-row').forEach(r => r.classList.remove('active'));
  const row = document.querySelector(`.tim-row[data-i="${i}"]`);
  if (row) row.classList.add('active');
  _imgCtx = {sector: t.sector, source: t.source, offset: t.offset,
             byteOffset: imgByteOffset(t), clutCount: t.clut_count};
  _imgNatW = t.width; _imgNatH = t.height;
  const clutSel = t.clut_count > 1
    ? `<label class="xa-ctrl-label">Palette
         <select id="clut-sel" class="xa-ctrl-select" onchange="reloadTim()">${
           Array.from({length:t.clut_count}, (_,k)=>`<option value="${k}">${k}</option>`).join('')
         }</select></label>` : '';
  const host = document.getElementById('img-viewer-host');
  if (!host) return;
  host.innerHTML = `
    <div class="img-viewer">
      <div class="img-meta">
        <span class="chip">${t.bpp}bpp <b>mode ${t.mode}</b></span>
        <span class="chip"><b>${t.width}×${t.height}</b></span>
        <span class="chip">${t.clut_count?`${t.clut_count} palette${t.clut_count>1?'s':''}`:'no CLUT'}</span>
        <span class="chip">offset ${t.offset}</span>
      </div>
      <div class="img-stage"><img id="tim-img" alt="TIM"></div>
      <div class="img-ctrls">
        ${clutSel}
        <label class="xa-ctrl-label">Zoom
          <input type="range" min="1" max="8" value="${_imgZoom}" id="zoom-rng"
                 oninput="applyZoom(this.value)" style="accent-color:var(--acc)"></label>
        <a id="tim-dl" class="search-btn" download="tim_${_imgCtx.sector}_${t.offset}.png"
           style="margin-left:auto">Save PNG</a>
      </div>
    </div>`;
  reloadTim();
}

function reloadTim() {
  if (!_imgCtx) return;
  const clut = document.getElementById('clut-sel')?.value || 0;
  if (_imgObjUrl) { URL.revokeObjectURL(_imgObjUrl); _imgObjUrl = null; }
  const url = imgQuery(_imgCtx.sector, _imgCtx.source, _imgCtx.offset, clut, _imgCtx.byteOffset);
  fetch(url)
    .then(r => r.ok ? r.blob() : r.json().then(j => { throw new Error(j.error || r.status); }))
    .then(blob => {
      _imgObjUrl = URL.createObjectURL(blob);
      const img = document.getElementById('tim-img');
      const dl  = document.getElementById('tim-dl');
      if (img) img.src = _imgObjUrl;
      if (dl)  dl.href = _imgObjUrl;
      applyZoom(_imgZoom);
    })
    .catch(e => {
      const host = document.getElementById('img-viewer-host');
      if (host) host.innerHTML = `<div class="placeholder" style="color:#fca5a5">Error: ${e.message}</div>`;
    });
}

function applyZoom(z) {
  _imgZoom = parseInt(z);
  const img = document.getElementById('tim-img');
  if (!img) return;
  img.style.width  = (_imgNatW * _imgZoom) + 'px';
  img.style.height = (_imgNatH * _imgZoom) + 'px';
}

// ── archive image scan / gallery ──────────────────────────────────────────────
function showImageScan() {
  document.getElementById('center').innerHTML = `
    <div class="blk-hdr"><h2>Scan archive for TIM images</h2>
      <div class="sub">Sweeps a sector range; structured blocks are decompressed first.</div></div>
    <div class="search-bar" style="flex-wrap:wrap">
      <label class="xa-ctrl-label">Start <input id="scan-start" class="search-input" style="max-width:120px" value="0"></label>
      <label class="xa-ctrl-label">End <input id="scan-end" class="search-input" style="max-width:120px" value="2000"></label>
      <label class="xa-ctrl-label">Step <input id="scan-step" class="search-input" style="max-width:80px" value="1"></label>
      <button class="search-go" id="scan-go" onclick="runImageScan()">Scan</button>
    </div>
    <div id="scan-status" class="xa-status"></div>
    <div id="scan-gallery"></div>`;
}

async function runImageScan() {
  const start = parseInt(document.getElementById('scan-start').value) || 0;
  const end   = parseInt(document.getElementById('scan-end').value) || (start+2000);
  const step  = Math.max(1, parseInt(document.getElementById('scan-step').value) || 1);
  const btn   = document.getElementById('scan-go');
  const status= document.getElementById('scan-status');
  const gal   = document.getElementById('scan-gallery');
  btn.disabled = true; btn.textContent = 'Scanning…';
  status.style.color = 'var(--mut)';
  status.innerHTML = '<span class="spinner"></span>Scanning sectors…';
  gal.innerHTML = '';
  try {
    const d = await api(`/api/scan_range?start=${start}&end=${end}&step=${step}`);
    btn.disabled = false; btn.textContent = 'Scan';
    status.textContent = `${d.count} image${d.count!==1?'s':''} found · scanned ${d.scanned.toLocaleString()} sectors`
      + (d.truncated ? ` · result limit reached (stopped at sector ${d.last_sector.toLocaleString()})` : '');
    if (!d.count) { gal.innerHTML = '<div class="placeholder">No TIM images in this range.</div>'; return; }
    _timImages = d.images;
    gal.innerHTML = `<div class="gallery">${d.images.map((t,i)=>`
      <div class="gallery-cell" onclick="openGalleryImage(${i})">
        <div class="thumb"><img loading="lazy" src="${imgQuery(t.sector,t.source,t.offset,0,imgByteOffset(t))}"></div>
        <div class="cap">${t.width}×${t.height} ${t.bpp}bpp ${t.source}</div>
        <div class="cap">sec ${t.sector.toLocaleString()}</div>
      </div>`).join('')}</div>`;
  } catch(err) {
    btn.disabled = false; btn.textContent = 'Scan';
    status.style.color = '#fca5a5';
    status.textContent = `Error: ${err.message}`;
  }
}

// Open one gallery image in the full viewer (reuses the showTim machinery).
function openGalleryImage(i) {
  const t = _timImages[i];
  if (!t) return;
  document.getElementById('center').innerHTML = `
    <div class="blk-hdr"><h2>TIM image — sector ${t.sector.toLocaleString()}</h2>
      <div class="sub">source: ${t.source}</div></div>
    <div style="margin-bottom:12px"><button class="search-btn" onclick="showImageScan()">← Back to scan</button></div>
    <div id="img-viewer-host"></div>`;
  _timImages = [t];
  showTim(0);
}

function renderBlockCenter(blk) {
  let rows = blk.sectors.map((s,i) => {
    const padClass = s.is_padding ? ' pad' : '';
    const status   = s.is_padding ? '<span style="color:var(--mut)">padding</span>' : 'data';
    return `<tr class="${padClass}" data-lba="${s.lba}">
      <td>${s.idx}</td>
      <td class="mono">${s.lba.toLocaleString()}</td>
      <td>${status}</td>
      <td class="mono">${s.is_padding ? '—' : s.first4_hex}</td>
    </tr>`;
  }).join('');

  document.getElementById('center').innerHTML = `
    <div class="blk-hdr">
      <h2>Block ${blk.block_num} &nbsp;<span style="font-weight:400;color:var(--mut);font-size:13px">sectors ${blk.block_start.toLocaleString()}–${(blk.block_start+9).toLocaleString()}</span></h2>
      <div class="sub">payload ${blk.payload_size.toLocaleString()} bytes &nbsp;·&nbsp; max usable ${(8*2016).toLocaleString()} bytes</div>
    </div>
    <table class="sec-table">
      <thead><tr><th>Idx</th><th>Sector LBA</th><th>Status</th><th>First 4 bytes</th></tr></thead>
      <tbody id="sec-tbody">${rows}</tbody>
    </table>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">
      <button class="decomp-btn" style="margin-bottom:0" id="decomp-btn" onclick="doDecompress(${blk.block_start})">Decompress block</button>
    </div>
    <div id="decomp-result"></div>`;

  document.getElementById('sec-tbody').addEventListener('click', e => {
    const tr = e.target.closest('tr');
    if (!tr || tr.classList.contains('pad')) return;
    document.querySelectorAll('#sec-tbody tr.active').forEach(r => r.classList.remove('active'));
    tr.classList.add('active');
    loadSectorRight(parseInt(tr.dataset.lba));
  });
}

// ── decompress ───────────────────────────────────────────────────────────────
async function doDecompress(blockStart) {
  const btn = document.getElementById('decomp-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Decompressing…';
  try {
    const d = await api(`/api/decompress?sector=${blockStart}`);
    btn.innerHTML = 'Decompress block';
    btn.disabled = false;
    if (d.error) { document.getElementById('decomp-result').innerHTML=`<div style="color:#fca5a5">${d.error}</div>`; return; }
    const streamRows = (d.streams||[]).map((s,i)=>
      `<div class="stream-row"><span class="si">#${i+1}</span><span>in ${s.end-s.start}B</span><span>→ ${s.output_len}B out</span></div>`
    ).join('');
    document.getElementById('decomp-result').innerHTML = `
      <div class="decomp-result">
        <div class="stat">
          <div class="stat-chip"><span>streams </span>${d.stream_count}</div>
          <div class="stat-chip"><span>in </span>${d.input_size.toLocaleString()}B</div>
          <div class="stat-chip"><span>out </span>${d.output_size.toLocaleString()}B</div>
          <div class="stat-chip"><span>ratio </span>${d.ratio}×</div>
        </div>
        <div class="stream-list">${streamRows}</div>
        <div style="margin-top:8px;font-size:11px;color:var(--mut)">Output preview (first 128 bytes):</div>
        <div class="preview-hex">${formatHexStr(d.preview_hex)}</div>
        <div style="margin-top:10px">
          <button class="search-btn" onclick="viewDecompFull(${blockStart}, 0)">View full decompressed hex</button>
        </div>
      </div>`;
  } catch(err) {
    btn.innerHTML = 'Decompress block';
    btn.disabled = false;
    document.getElementById('decomp-result').innerHTML = `<div style="color:#fca5a5">Error: ${err.message}</div>`;
  }
}

// ── full decompressed hex viewer ─────────────────────────────────────────────
async function viewDecompFull(sector, offset) {
  const PAGE = 4096;
  document.getElementById('center').innerHTML =
    '<div class="placeholder"><span class="spinner"></span>Loading decompressed data…</div>';
  try {
    const d = await api(`/api/decomp_full?sector=${sector}&offset=${offset}&length=${PAGE}`);
    const prevOff = Math.max(0, offset - PAGE);
    const nextOff = offset + PAGE < d.total_size ? offset + PAGE : null;
    const navBtns = `
      <div style="display:flex;gap:8px;margin-bottom:12px;align-items:center">
        <button class="search-btn" onclick="viewDecompFull(${sector},${prevOff})" ${offset===0?'disabled':''}>← Prev</button>
        <span style="font-size:11px;color:var(--mut)">offset 0x${offset.toString(16)} – 0x${(offset+d.length-1).toString(16)} of ${d.total_size.toLocaleString()} B</span>
        <button class="search-btn" onclick="viewDecompFull(${sector},${nextOff})" ${nextOff===null?'disabled':''}>Next →</button>
        <button class="search-btn" style="margin-left:auto" onclick="loadBlockOrAudio(${sector},false)">Back to block</button>
      </div>`;
    const hexRows = d.rows.map(r =>
      `<div class="hex-row"><span class="hex-addr">${r.addr}</span><span class="hex-bytes">${r.hex}</span><span class="hex-ascii">${r.ascii}</span></div>`
    ).join('');
    document.getElementById('center').innerHTML = `
      <div>
        <div class="blk-hdr"><h2>Decompressed hex — sector ${sector.toLocaleString()}</h2></div>
        ${navBtns}
        <div class="hex-dump">${hexRows}</div>
        ${navBtns}
      </div>`;
  } catch(err) {
    document.getElementById('center').innerHTML =
      `<div class="placeholder" style="color:#fca5a5">Error: ${err.message}</div>`;
  }
}

// ── text search ──────────────────────────────────────────────────────────────
function showSearch(prefill) {
  document.getElementById('center').innerHTML = `
    <div style="max-width:640px">
      <div class="blk-hdr"><h2>Search text content</h2>
        <div class="sub">Decompresses every entry and scans for your query (ASCII, case-insensitive).
          If a result appears garbled, the file may use a custom character table.</div>
      </div>
      <div class="search-bar">
        <input class="search-input" id="search-q" placeholder="e.g. none of your business" autocomplete="off"
               value="${(prefill||'').replace(/"/g,'&quot;')}"
               onkeydown="if(event.key==='Enter')doSearch()">
        <button class="search-go" id="search-go" onclick="doSearch()">Search</button>
      </div>
      <div id="search-results"></div>
    </div>`;
  document.getElementById('search-q').focus();
  if (prefill) doSearch();
}

async function doSearch() {
  const q   = document.getElementById('search-q').value.trim();
  const btn = document.getElementById('search-go');
  const res = document.getElementById('search-results');
  if (!q) return;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Scanning…';
  res.innerHTML = '<div class="placeholder"><span class="spinner"></span>Decompressing and searching all entries…</div>';
  try {
    const d = await api('/api/search?q=' + encodeURIComponent(q));
    btn.disabled = false;
    btn.textContent = 'Search';
    if (d.error) { res.innerHTML = `<div style="color:#fca5a5">${d.error}</div>`; return; }
    if (!d.matches.length) {
      res.innerHTML = `<div class="placeholder">No matches for <b>${q}</b> in any decompressed entry.<br>
        <span style="font-size:11px;color:var(--mut)">The game may use a custom character table — ASCII search won't find encoded text.</span></div>`;
      return;
    }
    const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    const hiCtx = (text, q) => {
      const lo = text.toLowerCase(), ql = q.toLowerCase();
      let out = '', pos = 0, idx;
      while ((idx = lo.indexOf(ql, pos)) !== -1) {
        out += esc(text.slice(pos, idx)) + '<span class="hl">' + esc(text.slice(idx, idx+q.length)) + '</span>';
        pos = idx + q.length;
      }
      return out + esc(text.slice(pos));
    };
    let h = `<div style="font-size:11px;color:var(--mut);margin-bottom:10px">${d.matches.length} match${d.matches.length===1?'':'es'}</div>`;
    d.matches.forEach(m => {
      const ctx = hiCtx(m.context, q);
      const src = m.source ? ` &nbsp;·&nbsp; ${m.source}` : '';
      h += `<div class="match-row" onclick="jumpToEntry(${m.sector})">
        <div class="match-meta">entry ${m.entry_idx} &nbsp;·&nbsp; sector ${m.sector.toLocaleString()} &nbsp;·&nbsp; offset +${m.offset}${src}</div>
        <div class="match-ctx">${ctx}</div>
      </div>`;
    });
    res.innerHTML = h;
  } catch(err) {
    btn.disabled = false;
    btn.textContent = 'Search';
    res.innerHTML = `<div style="color:#fca5a5">Error: ${err.message}</div>`;
  }
}

function jumpToEntry(sector) {
  // highlight in file list and load the entry
  document.querySelectorAll('.entry-row.active').forEach(r => r.classList.remove('active'));
  const row = document.querySelector(`#entry-list [data-sector="${sector}"]`);
  if (row) { row.classList.add('active'); row.scrollIntoView({block:'nearest'}); }
  activeEntry = row ? parseInt(row.dataset.idx) : null;
  loadBlockOrAudio(sector, false);
}

// ── story / UI text (confirmed VP text files) ────────────────────────────────
function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function showStoryText() {
  document.getElementById('center').innerHTML = `
    <div style="max-width:760px">
      <div class="blk-hdr">
        <h2>Story / UI text</h2>
        <div class="sub">Confirmed VP text files, decoded with the game's character scheme
          (first byte +0x1F, rest −1; custom font 0x21–0x3A → A–Z) and SLZ-1/2 decompression.
          All located text so far is menu / item / skill / status text — true cutscene
          dialogue has not yet been confirmed.</div>
      </div>
      <div id="story-list"><div class="placeholder"><span class="spinner"></span>Loading catalog…</div></div>
      <div id="story-detail"></div>
    </div>`;
  try {
    const d = await api('/api/storytext');
    const list = document.getElementById('story-list');
    let h = `<div style="font-size:11px;color:var(--mut);margin-bottom:10px">${d.files.length} known text files — click to decode</div>`;
    d.files.forEach(f => {
      const slz   = `SLZ-${f.slz_type}`;
      const ents  = f.entries != null ? `${f.entries} strings` : '? strings';
      const dim   = f.approx ? 'opacity:.55' : '';
      const flag  = f.approx ? ` <span style="color:#fbbf24;font-size:10px">approx</span>` : '';
      const entry = f.in_file_table
        ? ` · <span style="color:#7dd3fc;cursor:pointer" onclick="event.stopPropagation();jumpToEntry(${f.entry_sector})">entry #${f.entry_idx}</span>`
        : ` · <span style="color:var(--mut)">not in file table</span>`;
      h += `<div class="match-row" style="${dim}" onclick="openStoryFile(${f.idx})">
        <div class="match-meta" style="color:#a78bfa;font-weight:700">${escHtml(f.content)}${flag}</div>
        <div class="match-meta">sector ${f.sector.toLocaleString()} · offset +${f.offset} · ${slz} · ${ents}${entry}</div>
      </div>`;
    });
    list.innerHTML = h;
  } catch(err) {
    document.getElementById('story-list').innerHTML =
      `<div style="color:#fca5a5">Error: ${err.message}</div>`;
  }
}

let storyShowRaw = false;
let storyReadableOnly = true;
let storyData = null;

async function openStoryFile(idx) {
  const det = document.getElementById('story-detail');
  det.innerHTML = `<div class="placeholder"><span class="spinner"></span>Decompressing &amp; decoding…</div>`;
  try {
    const d = await api('/api/storytext?idx=' + idx);
    if (d.error) { det.innerHTML = `<div style="color:#fca5a5">${d.error}</div>`; return; }
    storyData = d;
    renderStoryDetail();
    det.scrollIntoView({block:'nearest'});
  } catch(err) {
    det.innerHTML = `<div style="color:#fca5a5">Error: ${err.message}</div>`;
  }
}

function renderStoryDetail() {
  const d = storyData;
  if (!d) return;
  const det = document.getElementById('story-detail');
  let h = `<div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--brd)">
    <div style="font-weight:700;margin-bottom:4px">${escHtml(d.content)}
      <span style="font-size:10px;font-weight:700;color:#fff;background:#6d28d9;padding:2px 7px;border-radius:10px;margin-left:6px">SLZ-${d.slz_type}</span></div>
    <div style="font-size:11px;color:var(--mut);margin-bottom:10px">
      sector ${d.sector.toLocaleString()} · offset +${d.offset} ·
      ${storyReadableOnly ? d.readable_count+' readable of '+d.count : d.count+' strings'} ·
      ${d.comp_size ? d.comp_size.toLocaleString()+' → ' : ''}${d.decomp_size.toLocaleString()} B
      <label style="margin-left:10px;cursor:pointer;user-select:none">
        <input type="checkbox" ${storyReadableOnly?'checked':''} onchange="storyReadableOnly=this.checked;renderStoryDetail()"
          style="accent-color:var(--acc)"> readable only</label>
      <label style="margin-left:10px;cursor:pointer;user-select:none">
        <input type="checkbox" ${storyShowRaw?'checked':''} onchange="storyShowRaw=this.checked;renderStoryDetail()"
          style="accent-color:var(--acc)"> show raw bytes</label>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
      <button class="search-btn" onclick="loadSectorRight(${d.sector})">Raw hex (sector ${d.sector.toLocaleString()})</button>
      <button class="search-btn" onclick="viewStoryHex(${d.idx},0)">Decompressed hex</button>
      ${d.in_file_table
        ? `<button class="search-btn" onclick="jumpToEntry(${d.entry_sector})">→ file entry #${d.entry_idx}</button>`
        : `<span style="font-size:11px;color:var(--mut);align-self:center">not in file table</span>`}
    </div>`;
  if (!d.count) {
    h += `<div class="placeholder">No strings decoded — this file may not use the pointer-table layout
      ${d.approx?'(catalog entry is approximate).':'.'}</div></div>`;
    det.innerHTML = h;
    return;
  }
  const rows = storyReadableOnly ? d.strings.filter(s => s.readable) : d.strings;
  if (!rows.length) {
    h += `<div class="placeholder">No readable strings — this file is mostly binary / table data.
      Untick “readable only” to see every pointer-table entry.</div>`;
  }
  rows.forEach(s => {
    h += `<div style="padding:4px 0;border-bottom:1px solid #1e293b">
      <span style="color:var(--mut);font-size:10px;margin-right:8px">${s.index}</span>
      <span style="color:#e2e8f0;font-size:12px;white-space:pre-wrap;word-break:break-word">${escHtml(s.decoded)}</span>`;
    if (storyShowRaw) {
      h += `<div style="color:#86efac;font-size:10px;margin-top:2px;word-break:break-all">${s.raw_hex}</div>`;
    }
    h += `</div>`;
  });
  h += `</div>`;
  det.innerHTML = h;
}

// paged hex dump of a catalog file's SLZ-decompressed bytes (center panel)
async function viewStoryHex(idx, offset) {
  const PAGE = 4096;
  document.getElementById('center').innerHTML =
    '<div class="placeholder"><span class="spinner"></span>Decompressing…</div>';
  try {
    const d = await api(`/api/storytext_hex?idx=${idx}&offset=${offset}&length=${PAGE}`);
    if (d.error) {
      document.getElementById('center').innerHTML = `<div class="placeholder" style="color:#fca5a5">${d.error}</div>`;
      return;
    }
    const prevOff = Math.max(0, offset - PAGE);
    const nextOff = offset + PAGE < d.total_size ? offset + PAGE : null;
    const navBtns = `
      <div style="display:flex;gap:8px;margin-bottom:12px;align-items:center">
        <button class="search-btn" onclick="viewStoryHex(${idx},${prevOff})" ${offset===0?'disabled':''}>← Prev</button>
        <span style="font-size:11px;color:var(--mut)">offset 0x${offset.toString(16)} – 0x${(offset+d.length-1).toString(16)} of ${d.total_size.toLocaleString()} B</span>
        <button class="search-btn" onclick="viewStoryHex(${idx},${nextOff})" ${nextOff===null?'disabled':''}>Next →</button>
        <button class="search-btn" style="margin-left:auto" onclick="showStoryText()">Back to Story text</button>
      </div>`;
    const hexRows = d.rows.map(r =>
      `<div class="hex-row"><span class="hex-addr">${r.addr}</span><span class="hex-bytes">${r.hex}</span><span class="hex-ascii">${r.ascii}</span></div>`
    ).join('');
    document.getElementById('center').innerHTML = `
      <div>
        <div class="blk-hdr"><h2>Decompressed hex — Story text #${idx}</h2></div>
        ${navBtns}
        <div class="hex-dump">${hexRows}</div>
        ${navBtns}
      </div>`;
  } catch(err) {
    document.getElementById('center').innerHTML =
      `<div class="placeholder" style="color:#fca5a5">Error: ${err.message}</div>`;
  }
}

function formatHexStr(hex) {
  const bytes = hex.match(/.{2}/g)||[];
  return bytes.map((b,i) => `<span style="margin-right:${(i+1)%16===0?'8px':'3px'}">${b}</span>`).join('');
}

// ── sector hex dump ──────────────────────────────────────────────────────────
async function loadSectorRight(lba) {
  activeSector = lba;
  document.getElementById('right').innerHTML = '<div class="placeholder"><span class="spinner"></span>Loading…</div>';
  try {
    const d = await api(`/api/sector?n=${lba}`);
    renderSectorRight(lba, d);
  } catch(err) {
    document.getElementById('right').innerHTML = `<div class="placeholder" style="color:#fca5a5">Error: ${err.message}</div>`;
  }
}

function renderSectorRight(lba, d) {
  const hdr = d.header || {};
  const ent = d.entropy || 0;
  let html = `<div class="sec-preview">
    <h3>Sector ${lba.toLocaleString()}</h3>
    <div class="ent-bar-wrap">
      <div class="ent-label"><span>Entropy</span><span>${ent} bits/byte</span></div>
      <div class="ent-bar-bg"><div class="ent-bar" style="width:${ent/8*100}%;background:${entropyColor(ent)}"></div></div>
    </div>`;

  if (!d.is_structured) {
    html += `<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
      <div class="audio-badge" style="margin:0">XA ADPCM Audio</div>
      <button class="play-btn" style="padding:5px 12px;font-size:11px" onclick="playXADirect(${lba})">▶ Play</button>
    </div>
    <audio id="xa-right-audio" class="xa-audio" controls style="margin-bottom:8px"></audio>
    <div id="xa-right-status" class="xa-status"></div>`;
    // nibble histogram from first 256 bytes
    if (d.raw_first64) {
      const nibs = new Array(16).fill(0);
      (d.raw_first64.match(/.{2}/g)||[]).forEach(hx => {
        const b = parseInt(hx,16);
        nibs[b&0xF]++; nibs[(b>>4)&0xF]++;
      });
      const mx = Math.max(...nibs, 1);
      const bars = nibs.map((n,i)=>`<div class="nibble-bar" style="height:${Math.round(n/mx*52)+4}px" title="${i}:${n}"></div>`).join('');
      const labels = Array.from({length:16},(_,i)=>`<span>${i.toString(16)}</span>`).join('');
      html += `<div style="margin-bottom:4px;font-size:10px;color:var(--mut)">Nibble distribution (first 256 bytes)</div>
        <div class="nibble-chart">${bars}</div>
        <div class="nibble-labels">${labels}</div>`;
    }
  } else {
    html += `<div class="hdr-fields">`;
    const fields = [
      ['marker',       hdr.marker_hex||'',       '0–3'],
      ['sector_idx',   hdr.sector_idx,            '4'],
      ['block_size',   hdr.block_size,            '6'],
      ['block_num',    hdr.block_num,             '8–11'],
      ['payload_size', hdr.payload_size!=null ? hdr.payload_size.toLocaleString() : '', '12–15'],
      ['extra0',       hdr.extra0_hex||'',        '16–19'],
      ['preview',      hdr.preview_hex||'',       '20–23'],
    ];
    fields.forEach(([name,val,range])=>{
      html += `<div class="field"><span class="fld-name">${name}</span><span class="fld-val">${val}</span><span class="fld-bytes">+${range}</span></div>`;
    });
    html += '</div>';
  }

  // hex dump
  const rows = d.hex_rows || [];
  html += '<div class="hex-dump">';
  rows.forEach((row, i) => {
    const hi = i < 2 && d.is_structured ? ' hdr-hi' : '';
    html += `<div class="hex-row${hi}"><span class="hex-addr">${row.addr}</span><span class="hex-bytes">${row.hex}</span><span class="hex-ascii">${row.ascii}</span></div>`;
  });
  html += '</div></div>';
  document.getElementById('right').innerHTML = html;
}

// ── right-panel play button (sector hex view) ─────────────────────────────
let _xaRightUrl = null;

function playXADirect(sector) {
  const audio  = document.getElementById('xa-right-audio');
  const status = document.getElementById('xa-right-status');
  if (!audio) return;
  if (_xaRightUrl) { URL.revokeObjectURL(_xaRightUrl); _xaRightUrl = null; }
  audio.style.display = 'none';
  status.style.color = 'var(--mut)';
  status.textContent = 'Decoding…';

  fetch(`/api/audio?sector=${sector}`)
    .then(r => {
      if (!r.ok) return r.json().then(j => { throw new Error(j.error || r.status); });
      const len = parseInt(r.headers.get('Content-Length') || '0');
      return r.blob().then(blob => ({ blob, len }));
    })
    .then(({ blob, len }) => {
      _xaRightUrl = URL.createObjectURL(blob);
      audio.src = _xaRightUrl;
      audio.style.display = 'block';
      audio.play().catch(() => {});
      const smp = len > 44 ? Math.round((len - 44) / 2) : 0;
      status.textContent = `${(len/1024).toFixed(0)} KB · ${(smp/22050).toFixed(2)}s`;
    })
    .catch(err => {
      status.style.color = '#fca5a5';
      status.textContent = `Error: ${err.message}`;
    });
}

// ── init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', loadFileTable);
</script>
</body>
</html>"""


# ── HTTP handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)
        routes = {
            "/":               self._serve_html,
            "/api/filetable":  self._api_filetable,
            "/api/block":      lambda: self._api_block(qs),
            "/api/sector":     lambda: self._api_sector(qs),
            "/api/decompress": lambda: self._api_decompress(qs),
            "/api/search":          lambda: self._api_search(qs),
            "/api/storytext":       lambda: self._api_storytext(qs),
            "/api/storytext_hex":   lambda: self._api_storytext_hex(qs),
            "/api/decomp_full":     lambda: self._api_decomp_full(qs),
            "/api/audio":      lambda: self._api_audio(qs),
            "/api/waveform":   lambda: self._api_waveform(qs),
            "/api/image":       lambda: self._api_image(qs),
            "/api/scan_images": lambda: self._api_scan_images(qs),
            "/api/scan_range":  lambda: self._api_scan_range(qs),
        }
        fn = routes.get(parsed.path)
        if fn:
            try:
                fn()
            except Exception as exc:
                self._json({"error": str(exc)}, 500)
        else:
            self.send_error(404)

    def _serve_html(self):
        body = HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status=200):
        body = json.dumps(data, default=int).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _api_filetable(self):
        ft   = parse_file_table()
        stat = BIN_PATH.stat()
        self._json({
            **ft,
            "filename":     BIN_PATH.name,
            "file_size":    stat.st_size,
            "sector_count": stat.st_size // SECTOR,
        })

    def _api_block(self, qs):
        sector = int(qs.get("sector", ["0"])[0])
        self._json(get_block_info(sector))

    def _api_sector(self, qs):
        n    = int(qs.get("n", ["0"])[0])
        data = read_sector_raw(n)
        hdr  = parse_sector_header(n)
        rows = []
        for off in range(0, SECTOR, 16):
            chunk = data[off:off + 16]
            rows.append({
                "addr":  f"{off:04x}",
                "hex":   " ".join(f"{b:02x}" for b in chunk),
                "ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in chunk),
            })
        self._json({
            "hex_rows":     rows,
            "entropy":      sector_entropy(data),
            "is_structured":hdr.get("is_structured", False),
            "header":       hdr,
            "raw_first64":  data[:256].hex(),
        })

    def _api_audio(self, qs):
        start  = int(qs.get("sector", ["0"])[0])
        stereo = qs.get("stereo", ["0"])[0] != "0"
        rate   = max(8000, min(96000, int(qs.get("rate", ["22050"])[0])))
        limit  = min(int(qs.get("limit", ["500"])[0]), 5000)

        pcm_L: list = []
        pcm_R: list = []
        prev_L = [0, 0]
        prev_R = [0, 0]
        lba    = start
        count  = 0
        while count < limit:
            raw = read_sector_raw(lba)
            if struct.unpack_from("<I", raw, 0)[0] == MARKER:
                break                       # hit a data sector
            L, R = decode_spu_sector(raw, stereo, prev_L, prev_R)
            pcm_L.extend(L)
            pcm_R.extend(R)
            lba   += 1
            count += 1

        wav = make_wav_bytes(pcm_L, pcm_R if stereo else [], rate)
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav)))
        self.send_header("Content-Disposition",
                         f"inline; filename=\"xa_{start}.wav\"")
        self.end_headers()
        self.wfile.write(wav)

    def _api_waveform(self, qs):
        start  = int(qs.get("sector", ["0"])[0])
        limit  = min(int(qs.get("limit", ["5000"])[0]), 10000)
        SILENCE_THRESHOLD = 3000
        MIN_TRACK_SECTORS = 3

        amplitudes = []
        lba = start
        for _ in range(limit):
            amp = sector_amplitude_fast(lba)
            if amp == -1:
                break               # hit a data block — stop scan
            amplitudes.append(amp)
            lba += 1

        # detect tracks (runs above silence threshold)
        tracks = []
        in_track   = False
        track_start = 0
        for i, amp in enumerate(amplitudes):
            if not in_track and amp >= SILENCE_THRESHOLD:
                in_track    = True
                track_start = i
            elif in_track and amp < SILENCE_THRESHOLD:
                length = i - track_start
                if length >= MIN_TRACK_SECTORS:
                    sec_start = start + track_start
                    sec_end   = start + i - 1
                    dur = round(length * 3584 / 22050, 2)
                    tracks.append({
                        "idx": len(tracks),
                        "start": sec_start,
                        "end": sec_end,
                        "sector_count": length,
                        "duration_s": dur,
                    })
                in_track = False
        if in_track:
            length = len(amplitudes) - track_start
            if length >= MIN_TRACK_SECTORS:
                sec_start = start + track_start
                sec_end   = start + len(amplitudes) - 1
                dur = round(length * 3584 / 22050, 2)
                tracks.append({
                    "idx": len(tracks),
                    "start": sec_start,
                    "end": sec_end,
                    "sector_count": length,
                    "duration_s": dur,
                })

        result = json.dumps({
            "start": start,
            "count": len(amplitudes),
            "amplitudes": amplitudes,
            "tracks": tracks,
            "silence_threshold": SILENCE_THRESHOLD,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        self.wfile.write(result)

    def _api_decompress(self, qs):
        sector  = int(qs.get("sector", ["0"])[0])
        payload = _block_payload(sector)
        out, streams = lz77_decompress_multi(payload)
        self._json({
            "stream_count": len(streams),
            "input_size":   len(payload),
            "output_size":  len(out),
            "ratio":        round(len(out) / max(len(payload), 1), 2),
            "preview_hex":  out[:128].hex(),
            "streams":      streams,
        })

    def _api_decomp_full(self, qs):
        sector = int(qs.get("sector", ["0"])[0])
        offset = max(0, int(qs.get("offset", ["0"])[0]))
        length = min(max(1, int(qs.get("length", ["4096"])[0])), 16384)
        payload = _block_payload(sector)
        out, _  = lz77_decompress_multi(payload)
        chunk   = out[offset:offset + length]
        rows    = []
        for i in range(0, len(chunk), 16):
            rb = chunk[i:i + 16]
            rows.append({
                "addr":  f"{offset + i:06x}",
                "hex":   " ".join(f"{b:02x}" for b in rb),
                "ascii": "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in rb),
            })
        self._json({
            "total_size": len(out),
            "offset":     offset,
            "length":     len(chunk),
            "rows":       rows,
        })

    # ── images ───────────────────────────────────────────────────────────────
    def _image_buffer(self, sector, source, qs):
        """Rebuild the byte buffer a TIM offset was found in (block/raw/slz)."""
        if source == "raw":
            span = min(int(qs.get("span", ["64"])[0]), 128)
            return b"".join(read_sector_raw(sector + i) for i in range(span))
        if source == "slz":
            byte_off = int(qs.get("byte_offset", [str(sector * SECTOR)])[0])
            return load_text_file(byte_off)["data"]
        payload = _block_payload(sector)                 # source == "block"
        out, _  = lz77_decompress_multi(payload)
        return out

    @staticmethod
    def _tim_summary(t, sector, source, byte_offset=None):
        d = {
            "sector":     sector,
            "source":     source,
            "offset":     t["offset"],
            "mode":       t["mode"],
            "bpp":        t["bpp"],
            "width":      t["width"],
            "height":     t["height"],
            "clut_count": t["clut_count"],
            "total_size": t["total_size"],
        }
        if byte_offset is not None:        # SLZ file start, for the slz image source
            d["byte_offset"] = byte_offset
        return d

    def _api_scan_images(self, qs):
        """Scan one entry/block for TIMs. source=auto|raw|block|slz."""
        sector  = int(qs.get("sector", ["0"])[0])
        source  = qs.get("source", ["auto"])[0]
        results = []
        used    = source

        hdr = parse_sector_header(sector)
        if source in ("auto", "block") and hdr.get("is_structured"):
            try:
                payload = _block_payload(sector)
                out, _  = lz77_decompress_multi(payload)
                for t in find_tims_in_data(out):
                    results.append(self._tim_summary(t, sector, "block"))
                used = "block"
            except Exception:
                pass
        if source == "slz":
            byte_off = int(qs.get("byte_offset", [str(sector * SECTOR)])[0])
            data = load_text_file(byte_off)["data"]
            for t in find_tims_in_data(data):
                results.append(self._tim_summary(t, sector, "slz", byte_off))
            used = "slz"
        if not results and source in ("auto", "raw"):
            span = min(int(qs.get("span", ["64"])[0]), 128)
            raw  = b"".join(read_sector_raw(sector + i) for i in range(span))
            for t in find_tims_in_data(raw):
                results.append(self._tim_summary(t, sector, "raw"))
            # most art is SLZ-compressed — probe this sector for SLZ files too
            for byte_off, t in scan_slz_for_tims(sector):
                results.append(self._tim_summary(t, sector, "slz", byte_off))
            used = "raw+slz" if results else "raw"

        self._json({"sector": sector, "source": used,
                    "count": len(results), "images": results})

    def _api_image(self, qs):
        """Render a single TIM to PNG and stream it as an image/png blob."""
        sector = int(qs.get("sector", ["0"])[0])
        offset = int(qs.get("offset", ["0"])[0])
        source = qs.get("source", ["block"])[0]
        clut   = int(qs.get("clut", ["0"])[0])

        data = self._image_buffer(sector, source, qs)
        tim  = parse_tim_header(data, offset)
        if tim is None:
            self._json({"error": f"no valid TIM at offset {offset}"}, 404)
            return
        w, h, rgba = decode_tim_rgba(data, tim, clut)
        png = encode_png(w, h, rgba)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png)))
        self.send_header("Content-Disposition",
                         f'inline; filename="tim_{sector}_{offset}.png"')
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(png)

    def _api_scan_range(self, qs):
        """Sweep a sector range for TIMs to build a gallery. Bounded + reports
        truncation (no silent caps)."""
        start = int(qs.get("start", ["0"])[0])
        end   = int(qs.get("end",   [str(start + 2000)])[0])
        step  = max(1, int(qs.get("step",  ["1"])[0]))
        limit = min(int(qs.get("limit", ["300"])[0]), 1000)
        max_scan = min(int(qs.get("max_scan", ["20000"])[0]), 200000)
        do_slz = qs.get("slz", ["1"])[0] != "0"        # probe SLZ-compressed art

        images    = []
        seen      = set()
        scanned   = 0
        truncated = False
        lba       = start
        while lba < end and scanned < max_scan:
            scanned += 1
            try:
                hdr = parse_sector_header(lba)
                if hdr.get("is_structured"):
                    if hdr.get("sector_idx", 0) == 0:      # scan each block once
                        payload = _block_payload(lba)
                        out, _  = lz77_decompress_multi(payload)
                        for t in find_tims_in_data(out):
                            key = ("block", lba, t["offset"])
                            if key in seen:
                                continue
                            seen.add(key)
                            images.append(self._tim_summary(t, lba, "block"))
                            if len(images) >= limit:
                                truncated = True
                                break
                else:
                    span = min(int(qs.get("rawspan", ["64"])[0]), 128)
                    raw  = b"".join(read_sector_raw(lba + i) for i in range(span))
                    for t in find_tims_in_data(raw):
                        abs_key = ("raw", (lba * SECTOR) + t["offset"])
                        if abs_key in seen:
                            continue
                        seen.add(abs_key)
                        images.append(self._tim_summary(t, lba, "raw"))
                        if len(images) >= limit:
                            truncated = True
                            break
                    if not truncated and do_slz:
                        for byte_off, t in scan_slz_for_tims(lba):
                            slz_key = ("slz", byte_off, t["offset"])
                            if slz_key in seen:
                                continue
                            seen.add(slz_key)
                            images.append(self._tim_summary(t, lba, "slz", byte_off))
                            if len(images) >= limit:
                                truncated = True
                                break
            except Exception:
                pass
            if truncated or scanned >= max_scan:
                break
            lba += step

        self._json({
            "start":       start,
            "end":         end,
            "step":        step,
            "scanned":     scanned,
            "last_sector": lba,
            "count":       len(images),
            "truncated":   truncated,
            "images":      images,
        })

    def _api_storytext(self, qs):
        """List the confirmed text-file catalog, or decode one entry by idx."""
        idx_param = qs.get("idx", [None])[0]
        if idx_param is None:
            files = []
            for i, f in enumerate(KNOWN_TEXT_FILES):
                ent = entry_for_sector(f["sector"])
                files.append({
                    "idx":         i,
                    "sector":      f["sector"],
                    "offset":      f["offset"],
                    "byte_offset": f["sector"] * SECTOR + f["offset"],
                    "slz_type":    f["slz_type"],
                    "comp_size":   f["comp_size"],
                    "decomp_size": f["decomp_size"],
                    "entries":     f["entries"],
                    "content":     f["content"],
                    "approx":      f.get("approx", False),
                    "in_file_table": ent is not None,
                    "entry_idx":     ent["entry_idx"]    if ent else None,
                    "entry_sector":  ent["entry_sector"] if ent else None,
                })
            self._json({"files": files})
            return

        try:
            idx = int(idx_param)
            f = KNOWN_TEXT_FILES[idx]
        except (ValueError, IndexError):
            self._json({"error": f"bad catalog index: {idx_param}"}, 400)
            return

        byte_offset = f["sector"] * SECTOR + f["offset"]
        loaded  = load_text_file(byte_offset, f["slz_type"], f["comp_size"])
        strings = parse_text_file(loaded["data"])
        ent     = entry_for_sector(f["sector"])
        self._json({
            "idx":         idx,
            "content":     f["content"],
            "sector":      f["sector"],
            "offset":      f["offset"],
            "byte_offset": byte_offset,
            "slz_type":    loaded["slz_type"],
            "comp_size":   loaded["comp_size"],
            "decomp_size": len(loaded["data"]),
            "approx":      f.get("approx", False),
            "in_file_table": ent is not None,
            "entry_idx":     ent["entry_idx"]    if ent else None,
            "entry_sector":  ent["entry_sector"] if ent else None,
            "count":       len(strings),
            "readable_count": sum(1 for s in strings if s["readable"]),
            "strings":     strings,
        })

    def _api_storytext_hex(self, qs):
        """Hex dump of a catalog file's SLZ-decompressed bytes (paged)."""
        try:
            idx = int(qs.get("idx", ["-1"])[0])
            f = KNOWN_TEXT_FILES[idx]
        except (ValueError, IndexError):
            self._json({"error": "bad catalog index"}, 400)
            return
        offset = max(0, int(qs.get("offset", ["0"])[0]))
        length = min(max(1, int(qs.get("length", ["4096"])[0])), 16384)
        byte_offset = f["sector"] * SECTOR + f["offset"]
        data  = load_text_file(byte_offset, f["slz_type"], f["comp_size"])["data"]
        chunk = data[offset:offset + length]
        rows  = []
        for i in range(0, len(chunk), 16):
            rb = chunk[i:i + 16]
            rows.append({
                "addr":  f"{offset + i:06x}",
                "hex":   " ".join(f"{b:02x}" for b in rb),
                "ascii": "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in rb),
            })
        self._json({
            "total_size": len(data),
            "offset":     offset,
            "length":     len(chunk),
            "rows":       rows,
        })

    def _api_search(self, qs):
        query = qs.get("q", [""])[0].strip()
        if not query:
            self._json({"error": "missing query parameter q"}, 400)
            return
        try:
            q_bytes = query.lower().encode("ascii", errors="replace")
        except Exception:
            self._json({"error": "query must be ASCII"}, 400)
            return

        ft      = parse_file_table()
        matches = []

        def _search_buf(buf, label, entry_idx, sec):
            """Case-insensitive ASCII byte search."""
            lc  = bytes(b | 0x20 if 0x41 <= b <= 0x5A else b for b in buf)
            pos = 0
            found = []
            while len(matches) + len(found) < 200:
                idx = lc.find(q_bytes, pos)
                if idx == -1:
                    break
                c0  = max(0, idx - 30)
                c1  = min(len(buf), idx + len(q_bytes) + 30)
                ctx = "".join(
                    chr(b) if 0x20 <= b <= 0x7E else f"[{b:02x}]"
                    for b in buf[c0:c1]
                )
                found.append({
                    "entry_idx": entry_idx,
                    "sector":    sec,
                    "offset":    idx,
                    "context":   ctx,
                    "source":    label,
                })
                pos = idx + 1
            return found

        # wide-char variants: [char][0x00] (UTF-16LE) and [0x00][char] (UTF-16BE)
        q_le = bytes(c for b in q_bytes for c in (b, 0))   # "hi" → 68 00 69 00
        q_be = bytes(c for b in q_bytes for c in (0, b))   # "hi" → 00 68 00 69

        def _search_wide(buf, q_wide, label, entry_idx, sec):
            """Exact 2-byte-per-char search; context decoded as char pairs."""
            found = []
            # try both byte alignments (text may start at any offset)
            for align in (0, 1):
                lc_buf = bytearray(buf)
                for i in range(align, len(lc_buf) - 1, 2):
                    if 0x41 <= lc_buf[i] <= 0x5A:
                        lc_buf[i] |= 0x20
                lc_buf = bytes(lc_buf)
                pos = align
                while len(matches) + len(found) < 200:
                    idx = lc_buf.find(q_wide, pos)
                    if idx == -1:
                        break
                    # decode context as char pairs aligned to match start
                    c0 = max(0, idx - 24)
                    c1 = min(len(buf), idx + len(q_wide) + 24)
                    ctx_parts = []
                    # align c0 to same parity as idx
                    if (c0 % 2) != (idx % 2):
                        c0 += 1
                    i2 = c0
                    while i2 < c1 - 1:
                        lo, hi = buf[i2], buf[i2 + 1]
                        char_b = lo if label.endswith("le") else hi
                        null_b = hi if label.endswith("le") else lo
                        if null_b == 0 and 0x20 <= char_b <= 0x7E:
                            ctx_parts.append(chr(char_b))
                        else:
                            ctx_parts.append(f"[{lo:02x}{hi:02x}]")
                        i2 += 2
                    found.append({
                        "entry_idx": entry_idx,
                        "sector":    sec,
                        "offset":    idx,
                        "context":   "".join(ctx_parts),
                        "source":    label,
                    })
                    pos = idx + 2
            return found

        def _raw_sector_read(sec, n=8):
            buf = bytearray()
            for i in range(n):
                try:
                    s = read_sector_raw(sec + i)
                    if i > 0 and struct.unpack_from("<I", s, 0)[0] == MARKER:
                        break
                    buf.extend(s)
                except Exception:
                    break
            return bytes(buf)

        for e in ft["entries"]:
            sec = e["sector"]
            payload = None
            try:
                payload = _block_payload(sec)
            except Exception:
                pass

            if payload is not None:
                matches.extend(_search_buf(payload, "raw", e["idx"], sec))
                matches.extend(_search_wide(payload, q_le, "raw/utf16le", e["idx"], sec))
                try:
                    out, _ = lz77_decompress_multi(payload)
                    matches.extend(_search_buf(out, "decompressed", e["idx"], sec))
                    matches.extend(_search_wide(out, q_le, "decompressed/utf16le", e["idx"], sec))
                    matches.extend(_search_wide(out, q_be, "decompressed/utf16be", e["idx"], sec))
                except Exception:
                    pass
            else:
                buf = _raw_sector_read(sec)
                matches.extend(_search_buf(buf, "raw-sector", e["idx"], sec))
                matches.extend(_search_wide(buf, q_le, "raw-sector/utf16le", e["idx"], sec))

            if len(matches) >= 200:
                break

        self._json({"query": query, "matches": matches})

# ── entry point ───────────────────────────────────────────────────────────────

def main():
    global BIN_PATH, _fh

    if len(sys.argv) > 1:
        BIN_PATH = Path(sys.argv[1]).expanduser()
    if not BIN_PATH.exists():
        print(f"error: file not found: {BIN_PATH}", file=sys.stderr)
        sys.exit(1)

    _fh = open(BIN_PATH, "rb")
    size = BIN_PATH.stat().st_size

    print(f"VALKYRIE.BIN Navigator")
    print(f"  File   : {BIN_PATH}  ({size:,} bytes)")
    print(f"  Server : http://localhost:{PORT}")
    print(f"  Stop   : Ctrl-C")

    threading.Timer(0.6, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    class _Server(socketserver.ThreadingMixIn, HTTPServer):
        daemon_threads = True

    try:
        _Server(("localhost", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nShutdown.")
    finally:
        _fh.close()


if __name__ == "__main__":
    main()
