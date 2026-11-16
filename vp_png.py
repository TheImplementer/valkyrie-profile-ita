#!/usr/bin/env python3
"""Minimal PNG read/write (8-bit RGB / RGBA, non-interlaced) with only the standard library."""
import struct
import zlib


def read(path):
    """Rows of (r, g, b) tuples."""
    d = open(path, 'rb').read()
    assert d[:8] == b'\x89PNG\r\n\x1a\n', 'not a PNG'
    i = 8; idat = b''
    while i < len(d):
        n, = struct.unpack('>I', d[i:i + 4]); t = d[i + 4:i + 8]; c = d[i + 8:i + 8 + n]; i += 12 + n
        if t == b'IHDR':
            w, h, depth, ctype = struct.unpack('>IIBB', c[:10])
            assert depth == 8 and ctype in (2, 6) and c[12] == 0, 'only 8-bit RGB/RGBA, not interlaced'
        elif t == b'IDAT':
            idat += c
    raw = zlib.decompress(idat); bpp = 3 if ctype == 2 else 4; stride = w * bpp
    out = []; prev = bytearray(stride); p = 0
    for _ in range(h):
        f = raw[p]; line = bytearray(raw[p + 1:p + 1 + stride]); p += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0; b = prev[x]; c = prev[x - bpp] if x >= bpp else 0
            if f == 1: line[x] = (line[x] + a) & 255
            elif f == 2: line[x] = (line[x] + b) & 255
            elif f == 3: line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                pa = abs(b - c); pb = abs(a - c); pc = abs(a + b - 2 * c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out.append([tuple(line[x * bpp:x * bpp + 3]) for x in range(w)]); prev = line
    return out


def encode(px):
    h = len(px); w = len(px[0])
    raw = b''.join(b'\0' + bytes(v for p in row for v in p[:3]) for row in px)

    def chunk(t, c):
        return struct.pack('>I', len(c)) + t + c + struct.pack('>I', zlib.crc32(t + c) & 0xFFFFFFFF)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def write(path, px):
    open(path, 'wb').write(encode(px))
