#!/usr/bin/env python3
"""Read and patch VALKYRIE.BIN inside a raw cue/bin image (MODE2/2352), without touching the original.

A raw sector is 2352 bytes. VALKYRIE.BIN's sectors are Mode 2 Form 1:
  0x000 sync (12)  0x00C header MM SS FF 02 (4)  0x010 subheader (8, twice the same 4 bytes)
  0x018 user data (2048)  0x818 EDC (4)  0x81C ECC P parity (172)  0x8C8 ECC Q parity (104)
EDC is a CRC-32 (reflected polynomial 0xD8018001) over subheader + data; ECC is the CD-ROM
Reed-Solomon product code (GF(2^8), polynomial 0x11D) over header + subheader + data + EDC, with the
4 header bytes taken as zero (Mode 2). Both are recomputed for every sector the editor writes;
`selftest` recomputes them for existing sectors and compares with the disc.

The archive file is found through the ISO 9660 root directory (VALKYRIE.BIN at LBA 150 on SLUS-01156).
"""
import os
import re
import shutil
import struct

RAW = 2352
DATA = 2048
ARCHIVE_NAME = b'VALKYRIE.BIN;1'

_F = [0] * 256; _B = [0] * 256; _EDC = [0] * 256
for _i in range(256):
    _j = ((_i << 1) ^ (0x11D if _i & 0x80 else 0)) & 0xFF
    _F[_i] = _j; _B[_i ^ _j] = _i
    _e = _i
    for _ in range(8):
        _e = (_e >> 1) ^ (0xD8018001 if _e & 1 else 0)
    _EDC[_i] = _e


def edc(buf):
    e = 0
    for b in buf:
        e = (e >> 8) ^ _EDC[(e ^ b) & 0xFF]
    return e


def _ecc_block(src, major_count, minor_count, major_mult, minor_inc):
    size = major_count * minor_count; out = bytearray(2 * major_count)
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1); a = 0; b = 0
        for _ in range(minor_count):
            t = src[index]; index += minor_inc
            if index >= size:
                index -= size
            a ^= t; b ^= t; a = _F[a]
        a = _B[_F[a] ^ b]
        out[major] = a; out[major + major_count] = a ^ b
    return out


def seal_form1(sector):
    """Recompute EDC and ECC of a Mode 2 Form 1 raw sector in place."""
    struct.pack_into('<I', sector, 0x818, edc(sector[0x10:0x818]))
    work = bytearray(sector[0xC:0x8C8]); work[0:4] = b'\0\0\0\0'
    p = _ecc_block(work, 86, 24, 2, 86)
    work[0x81C - 0xC:0x8C8 - 0xC] = p; sector[0x81C:0x8C8] = p
    q = _ecc_block(bytearray(work) + bytearray(104), 52, 43, 86, 88)
    sector[0x8C8:0x930] = q


def parse_cue(path):
    text = open(path, encoding='latin-1').read()
    m = re.search(r'FILE\s+"([^"]+)"\s+BINARY', text, re.I)
    if not m or 'MODE2/2352' not in text.upper():
        raise SystemExit(f'{path}: expected one FILE "...bin" BINARY with a MODE2/2352 track')
    return os.path.join(os.path.dirname(os.path.abspath(path)), m.group(1)), text, m


class Disc:
    def __init__(self, cue):
        self.cue = cue
        self.bin, self.cue_text, self._m = parse_cue(cue)
        self.f = open(self.bin, 'rb')
        self.lba, self.size = self._find_archive()

    def raw(self, lba):
        self.f.seek(lba * RAW); return self.f.read(RAW)

    def _find_archive(self):
        pvd = self.raw(16)[24:24 + DATA]
        if pvd[1:6] != b'CD001':
            raise SystemExit('no ISO 9660 volume descriptor at LBA 16')
        lba, size = struct.unpack_from('<I', pvd, 156 + 2)[0], struct.unpack_from('<I', pvd, 156 + 10)[0]
        d = b''.join(self.raw(lba + k)[24:24 + DATA] for k in range((size + DATA - 1) // DATA))
        i = 0
        while i < len(d):
            n = d[i]
            if n == 0:
                i = (i // DATA + 1) * DATA; continue
            e = d[i:i + n]
            if e[33:33 + e[32]] == ARCHIVE_NAME:
                return struct.unpack_from('<I', e, 2)[0], struct.unpack_from('<I', e, 10)[0]
            i += n
        raise SystemExit('VALKYRIE.BIN not found in the root directory')

    def archive(self):
        """The whole VALKYRIE.BIN (user data of each sector)."""
        out = bytearray(); n = self.size // DATA
        self.f.seek(self.lba * RAW)
        for _ in range(n):
            out += self.f.read(RAW)[24:24 + DATA]
        return bytes(out)

    def write_copy(self, out_cue, patches):
        """Copy the image to out_cue / its .bin and write `patches` {archive sector: 2048 bytes} into
        the copy, resealing each sector. Returns the number of sectors written."""
        out_cue = os.path.abspath(out_cue)
        out_bin = os.path.splitext(out_cue)[0] + '.bin'
        if os.path.abspath(out_bin) == os.path.abspath(self.bin) or os.path.abspath(out_cue) == os.path.abspath(self.cue):
            raise SystemExit('refusing to overwrite the original image')
        os.makedirs(os.path.dirname(out_bin), exist_ok=True)
        shutil.copyfile(self.bin, out_bin)
        with open(out_bin, 'r+b') as g:
            for k in sorted(patches):
                lba = self.lba + k
                sec = bytearray(self.raw(lba))
                if sec[0xF] != 2 or sec[0x12] & 0x20:
                    raise SystemExit(f'archive sector {k}: not Mode 2 Form 1')
                sec[24:24 + DATA] = patches[k]
                seal_form1(sec)
                g.seek(lba * RAW); g.write(sec)
        cue = self.cue_text[:self._m.start(1)] + os.path.basename(out_bin) + self.cue_text[self._m.end(1):]
        open(out_cue, 'w', encoding='latin-1').write(cue)
        return len(patches)


def selftest(cue, samples=(0, 1, 1000, 157827, 300000)):
    d = Disc(cue)
    for k in samples:
        s = d.raw(d.lba + k); t = bytearray(s); t[0x818:] = bytes(RAW - 0x818)
        seal_form1(t)
        print(f'archive sector {k}: EDC/ECC {"match" if bytes(t) == s else "DIFFER"}')


if __name__ == '__main__':
    import sys
    selftest(sys.argv[1])
