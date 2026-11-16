"""Make a distributable translation patch from the edit files.

    python3 vp_release.py --disc1 DISC1.cue [--disc2 DISC2.cue] --edits edits_it \
        --name "Valkyrie Profile (Italiano)" --version 0.1 -o release/

For each disc it builds the patched image with vp_text_editor (into OUT/work/, which is not part of
the release), then writes two patches against the original .bin:
* an xdelta / VCDIFF patch (RFC 3284, with xdelta3's per-window Adler-32 so a wrong source image
  is refused; what Delta Patcher, xdelta3 and Rom Patcher JS apply);
* a PPF 3.0 patch (the traditional PlayStation format; some emulators load it next to the image).
Both are written here, in pure Python, and each is applied back to the original image and
checked against the built one before it is kept. A README.txt with the source and result hashes
goes next to them, and everything is zipped. The patches hold only the changed bytes: no game
data beyond the edited sectors.
"""
import argparse
import hashlib
import importlib.util
import os
import shutil
import struct
import sys
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SECTOR = 2352
# Redump dumps of Valkyrie Profile (USA) (redump.org/disc/655 and 656, read 2026-11-13): single
# Mode 2 data track each. Players will have these, so a public patch should be made from them.
REDUMP = {
    'Disc 1': {'serial': 'SLUS-01156', 'size': 730448880, 'CRC32': 'dd0422d5',
               'MD5': 'f579cb951587081019f91af9458d305b', 'SHA-1': 'fa13cbde448a47fa87b6c7829df1c5e6215f5f66'},
    'Disc 2': {'serial': 'SLUS-01179', 'size': 747352704, 'CRC32': 'c2132829',
               'MD5': '19f36bd625e676ea5d74db612d26eb6c', 'SHA-1': '7cc65766b46273b4ee166269170ecbaa433908fd'},
}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- differences

def diff_runs(src, dst, merge=8):
    """[(offset, length)] of the byte ranges where dst differs from src (same size), runs closer
    than merge bytes joined. Compared sector by sector, then byte by byte inside a sector."""
    assert len(src) == len(dst)
    runs = []
    for s in range(0, len(src), SECTOR):
        a = src[s:s + SECTOR]; b = dst[s:s + SECTOR]
        if a == b:
            continue
        i = 0; n = len(a)
        while i < n:
            if a[i] != b[i]:
                j = i
                while j < n and (a[j] != b[j] or any(a[k] != b[k] for k in range(j, min(n, j + merge)))):
                    j += 1
                if runs and runs[-1][0] + runs[-1][1] + merge >= s + i:
                    o, _ = runs[-1]; runs[-1] = (o, s + j - o)
                else:
                    runs.append((s + i, j - i))
                i = j
            else:
                i += 1
    return runs


# ---------------------------------------------------------------- VCDIFF (xdelta)

def _vint(n):
    """RFC 3284 integer: base 128, most significant digit first, high bit set on all but the last."""
    out = [n & 0x7F]; n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F)); n >>= 7
    return bytes(reversed(out))


WINDOW = 1 << 22                        # 4 MB target windows, each over the same source range


def vcdiff(src, dst, runs):
    """A VCDIFF delta turning src into dst (same size). Each window copies its own range of the
    source (VCD_SOURCE) and adds the changed bytes; instructions use the default code table:
    index 1 = ADD with the size in the instruction stream, index 19 = COPY mode 0 (VCD_SELF)
    with the size in the instruction stream."""
    out = bytearray(b'\xd6\xc3\xc4\x00\x00')     # magic 'VCD' + version 0, Hdr_Indicator 0
    by_win = {}
    for o, n in runs:                   # split runs at window edges
        while n:
            w = o // WINDOW; k = min(n, (w + 1) * WINDOW - o)
            by_win.setdefault(w, []).append((o, k)); o += k; n -= k
    for w in range((len(dst) + WINDOW - 1) // WINDOW):
        t0 = w * WINDOW; tl = min(WINDOW, len(dst) - t0)
        data = bytearray(); inst = bytearray(); addr = bytearray()
        at = t0
        for o, n in by_win.get(w, []):
            if o > at:
                inst += bytes([19]) + _vint(o - at); addr += _vint(at - t0)
            inst += bytes([1]) + _vint(n); data += dst[o:o + n]
            at = o + n
        if at < t0 + tl:
            inst += bytes([19]) + _vint(t0 + tl - at); addr += _vint(at - t0)
        # VCD_ADLER32 (xdelta3's extension, Win_Indicator 0x04): the Adler-32 of the target window,
        # 4 bytes big-endian after the section lengths, so a wrong source image is caught
        check = zlib.adler32(dst[t0:t0 + tl]).to_bytes(4, 'big')
        body = (_vint(tl) + b'\x00' + _vint(len(data)) + _vint(len(inst)) + _vint(len(addr)) + check
                + data + inst + addr)
        out += b'\x05' + _vint(tl) + _vint(t0) + _vint(len(body)) + body
    return bytes(out)


def _rint(buf, i):
    n = 0
    while True:
        b = buf[i]; i += 1; n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            return n, i


def vcdiff_apply(src, patch):
    """Decode a VCDIFF delta of the kind vcdiff() writes (and the general VCD_SOURCE / default
    code table case for ADD, RUN and COPY mode 0), as a check."""
    assert patch[:4] == b'\xd6\xc3\xc4\x00' and patch[4] == 0, 'not a plain VCDIFF'
    i = 5; out = bytearray()
    while i < len(patch):
        ind = patch[i]; i += 1
        assert ind & ~0x04 == 1, 'only VCD_SOURCE windows'
        sl, i = _rint(patch, i); sp, i = _rint(patch, i)
        dl, i = _rint(patch, i); end = i + dl
        tl, i = _rint(patch, i); assert patch[i] == 0; i += 1
        ld, i = _rint(patch, i); li, i = _rint(patch, i); la, i = _rint(patch, i)
        check = None
        if ind & 0x04:
            check = int.from_bytes(patch[i:i + 4], 'big'); i += 4
        data = patch[i:i + ld]; inst = patch[i + ld:i + ld + li]; addr = patch[i + ld + li:i + ld + li + la]
        seg = src[sp:sp + sl]; win = bytearray(); di = ii = ai = 0
        while ii < len(inst):
            op = inst[ii]; ii += 1
            size, ii = _rint(inst, ii)
            if op == 1:
                win += data[di:di + size]; di += size
            elif op == 0:
                win += data[di:di + 1] * size; di += 1
            elif op == 19:
                a, ai = _rint(addr, ai)
                assert a + size <= sl, 'copy past the source segment'
                win += seg[a:a + size]
            else:
                raise ValueError(f'instruction {op} not handled')
        assert len(win) == tl and i + ld + li + la == end
        if check is not None and zlib.adler32(bytes(win)) != check:
            raise ValueError('VCDIFF window checksum mismatch: wrong source image')
        out += win; i = end
    return bytes(out)


# ---------------------------------------------------------------- PPF 3.0

def ppf3(src, dst, runs, description):
    """A PPF 3.0 patch (BIN image, block check on, no undo data): header 'PPF30', method 2, a
    50-byte description, image type 0, block check 1, undo 0, a pad byte, the 1024 bytes of the
    source at 0x9320 (sector 16) for the block check, then records: u64 offset, u8 length, data."""
    out = bytearray(b'PPF30\x02')
    out += description.encode('ascii', 'replace')[:50].ljust(50, b' ')
    out += bytes([0, 1, 0, 0])
    out += src[0x9320:0x9320 + 1024]
    for o, n in runs:
        while n:
            k = min(n, 255)
            out += o.to_bytes(8, 'little') + bytes([k]) + dst[o:o + k]; o += k; n -= k
    return bytes(out)


def ppf3_apply(src, patch):
    assert patch[:6] == b'PPF30\x02'
    img = bytearray(src); i = 60
    if patch[57]:
        assert patch[60:60 + 1024] == src[0x9320:0x9320 + 1024], 'block check: not the right image'
        i += 1024
    undo = patch[58]
    while i < len(patch):
        if patch[i:i + 18] == b'@BEGIN_FILE_ID.DIZ':
            break
        o = int.from_bytes(patch[i:i + 8], 'little'); k = patch[i + 8]; i += 9
        img[o:o + k] = patch[i:i + k]; i += k * (2 if undo else 1)
    return bytes(img)


# ---------------------------------------------------------------- release

def hashes(data):
    return {'CRC32': f'{zlib.crc32(data):08x}', 'MD5': hashlib.md5(data).hexdigest(),
            'SHA-1': hashlib.sha1(data).hexdigest(), 'size': len(data)}


def cue_bin(cue):
    for line in open(cue, encoding='latin-1'):
        if line.strip().upper().startswith('FILE'):
            name = line.split('"')[1]
            p = os.path.join(os.path.dirname(cue), name)
            if not os.path.exists(p):            # the cue may name it in another case
                low = {f.lower(): f for f in os.listdir(os.path.dirname(cue) or '.')}
                p = os.path.join(os.path.dirname(cue), low.get(name.lower(), name))
            return p
    raise ValueError(f'{cue}: no FILE line')


def build_disc(cue, edit_paths, out_cue, log):
    ed = _load('vp_text_editor')
    disc, arc, dirw, names = ed.open_disc(cue)
    f = ed.EditFiles(edit_paths)
    # Disc 2 has 1,013 of disc 1's 1,118 scenes (the same text where both have one): a scene edit
    # for a scene this disc lacks is left out, not an error.
    edits, absent = [], []
    for e in f.edits:
        try:
            ed.Scene(arc, dirw, e[1], names)
        except (SystemExit, AssertionError, struct.error):
            absent.append(e[1]); continue
        edits.append(e)
    ed.build_image(disc, arc, dirw, names, edits, out_cue, True, log, **f.build_args())
    return absent


README = '''{name} {version}
{line}

A patch for Valkyrie Profile (USA), PlayStation, {discs}.
It changes {what}.

HOW TO APPLY
------------
1. You need your own copy of the game as BIN/CUE, matching the "original" hashes below.
2. Apply the .xdelta patch for each disc to that disc's .bin with Delta Patcher, xdelta UI,
   xdelta3 (xdelta3 -d -s ORIGINAL.bin PATCH.xdelta PATCHED.bin) or Rom Patcher JS
   (https://www.marcrobledo.com/RomPatcher.js/).
   The .ppf patches are the same changes in PPF 3.0 format. DuckStation applies one by itself:
   rename it to the image's name (e.g. "Valkyrie Profile (USA) (Disc 1).ppf"), put it next to the
   .cue/.bin, and turn on Settings > Console > "Apply Image Patches".
3. Keep the .cue file: it does not change. If you rename the patched .bin, edit the FILE line of
   the .cue to match.
4. Check that the patched .bin matches the "patched" hashes below.

{discblocks}
{notes}This patch contains only the changed bytes; it does not contain the game.
'''


def make_release(discs, edits_dir, name, version, out, notes='', log=print, require_redump=False):
    """discs: [(label, cue)]. Returns the zip path."""
    os.makedirs(out, exist_ok=True)
    work = os.path.join(out, 'work'); os.makedirs(work, exist_ok=True)
    edit_paths = sorted(os.path.join(edits_dir, f) for f in os.listdir(edits_dir) if f.endswith('.txt'))
    files = []; blocks = []
    for label, cue in discs:
        log(f'== {label}: building from {len(edit_paths)} edit files')
        built_cue = os.path.join(work, f'{label}.cue')
        absent = build_disc(cue, edit_paths, built_cue, lambda s: None)
        if absent:
            log(f'   {len(absent)} edited scene(s) are not on this disc and were left out: '
                + ', '.join(f'{x:#x}' for x in absent))
        src = open(cue_bin(cue), 'rb').read()
        hs = hashes(src); ref = REDUMP.get(label, {})
        redump = bool(ref) and ref['SHA-1'] == hs['SHA-1']
        if not redump:
            msg = (f'{label}: the source image is not the Redump dump (SHA-1 {hs["SHA-1"]}, Redump '
                   f'{ref.get("SHA-1", "?")}): players with the Redump image may not be able to apply it')
            if require_redump:
                raise ValueError(msg)
            log('   WARNING: ' + msg)
        dst = open(cue_bin(built_cue), 'rb').read()
        if len(src) != len(dst):
            raise ValueError(f'{label}: the built image has another size ({len(dst)} vs {len(src)})')
        runs = diff_runs(src, dst)
        log(f'   {len(runs)} changed ranges, {sum(n for _, n in runs)} bytes')
        base = f'{name} v{version} ({label})'
        xd = vcdiff(src, dst, runs)
        if vcdiff_apply(src, xd) != dst:
            raise ValueError(f'{label}: the xdelta patch does not reproduce the built image')
        pp = ppf3(src, dst, runs, f'{name} v{version} {label}')
        if ppf3_apply(src, pp) != dst:
            raise ValueError(f'{label}: the PPF patch does not reproduce the built image')
        for ext, blob in (('xdelta', xd), ('ppf', pp)):
            p = os.path.join(out, f'{base}.{ext}')
            open(p, 'wb').write(blob); files.append(p)
            log(f'   {os.path.basename(p)}: {len(blob)} bytes (checked)')
        hd = hashes(dst)
        blocks.append(f'{label}\n' + '-' * len(label) + '\n'
                      + (f'original: the Redump dump, Valkyrie Profile (USA) ({label}) [{ref["serial"]}]\n'
                         if redump else f'original: {os.path.basename(cue_bin(cue))} (NOT the Redump dump)\n')
                      + ''.join(f'  {k:<6} {v}\n' for k, v in hs.items())
                      + 'patched\n' + ''.join(f'  {k:<6} {v}\n' for k, v in hd.items()))
    title = f'{name} {version}'
    readme = README.format(name=name, version=version, line='=' * len(title),
                           discs=' and '.join(l for l, _ in discs) if len(discs) > 1 else discs[0][0],
                           what='the text: dialogue, menus, battle text and the title screen',
                           discblocks='\n'.join(blocks), notes=(notes.strip() + '\n\n') if notes.strip() else '')
    rp = os.path.join(out, 'README.txt'); open(rp, 'w', encoding='utf-8').write(readme); files.append(rp)
    zp = os.path.join(out, f'{name} v{version}.zip')
    with zipfile.ZipFile(zp, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, os.path.basename(f))
    log(f'wrote {zp} ({os.path.getsize(zp)} bytes)')
    return zp


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--disc1', required=True); ap.add_argument('--disc2')
    ap.add_argument('--edits', required=True, help='directory of edit files (all *.txt are used)')
    ap.add_argument('--name', required=True); ap.add_argument('--version', required=True)
    ap.add_argument('--notes', default='', help='a text file whose contents go into the README')
    ap.add_argument('--keep-work', action='store_true', help='keep the built images in OUT/work')
    ap.add_argument('--require-redump', action='store_true', help='stop unless each source image is the Redump dump')
    ap.add_argument('-o', '--output', required=True)
    a = ap.parse_args()
    discs = [('Disc 1', a.disc1)] + ([('Disc 2', a.disc2)] if a.disc2 else [])
    notes = open(a.notes, encoding='utf-8').read() if a.notes else ''
    try:
        make_release(discs, a.edits, a.name, a.version, a.output, notes, require_redump=a.require_redump)
    except ValueError as e:
        raise SystemExit(str(e))
    if not a.keep_work:
        shutil.rmtree(os.path.join(a.output, 'work'), ignore_errors=True)


if __name__ == '__main__':
    main()
