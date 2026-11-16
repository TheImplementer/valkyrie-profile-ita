#!/usr/bin/env python3
"""SLZ compressors for Valkyrie Profile: the inverse of vp_scene_text.slz1 / slz2.

Both formats are a flag byte (LSB first, 1 = literal byte, 0 = two-byte token) followed by up to
eight tokens. A token is b0 b1 with offset = b0 | (b1 & 0xF) << 8 and code = b1 >> 4:

  type 1 (EXE 0x800123CC): copy code + 3 bytes (3..18) from offset back; offset 0 ends the stream,
          and the game's streams always end with that marker (the decoder relies on it).
  type 2 (EXE 0x80012874): codes 0..14 copy code + 3 bytes (3..17); code 15 is a fill:
          offset < 256 -> fill the next byte offset + 19 times (19..274, a 3-byte token),
          otherwise fill the low byte (offset >> 8) + 3 times (4..18). No end marker.

The parse is optimal (dynamic programming over token costs: literal 9 bits, copy or short fill 17,
long fill 25), so the output is as small as the format allows for the matches found; matches are
searched over the whole 4,095-byte window through 3-byte hash chains.
"""
WINDOW = 0xFFF
CHAIN = 512           # candidates tried per position (newest first)


def _matches(data, maxlen):
    """For each position, the longest back-reference: (length, offset), length 0 if none >= 3."""
    n = len(data); best_len = [0] * n; best_off = [0] * n
    heads = {}
    for i in range(n - 2):
        key = data[i:i + 3]
        chain = heads.get(key)
        if chain is not None:
            limit = min(maxlen, n - i); bl = 2; bo = 0; tried = 0
            for p in reversed(chain):
                off = i - p
                if off > WINDOW or tried >= CHAIN:
                    break
                tried += 1
                if data[p + bl] != data[i + bl] if bl < limit else True:
                    if bl >= limit:
                        break
                    continue
                k = 3
                while k < limit and data[p + k] == data[i + k]:
                    k += 1
                if k > bl:
                    bl, bo = k, off
                    if k == limit:
                        break
            if bl >= 3:
                best_len[i] = bl; best_off[i] = bo
            chain.append(i)
            if len(chain) > 4 * CHAIN:
                del chain[:2 * CHAIN]
        else:
            heads[key] = [i]
    return best_len, best_off


def _runs(data):
    n = len(data); run = [0] * n; i = n - 1
    while i >= 0:
        run[i] = run[i + 1] + 1 if i + 1 < n and data[i + 1] == data[i] else 1
        i -= 1
    return run


def _pack(tokens):
    """tokens: (True, byte) literal or (False, bytes) for a 2/3-byte token."""
    out = bytearray()
    for g in range(0, len(tokens), 8):
        group = tokens[g:g + 8]; flags = 0
        for k, (lit, _) in enumerate(group):
            if lit:
                flags |= 1 << k
        out.append(flags)
        for lit, v in group:
            if lit:
                out.append(v)
            else:
                out += v
    return bytes(out)


def compress(data, kind):
    data = bytes(data); n = len(data)
    maxlen = 18 if kind == 1 else 17
    mlen, moff = _matches(data, maxlen)
    run = _runs(data) if kind == 2 else None
    INF = 1 << 60
    cost = [INF] * (n + 1); cost[n] = 0; choice = [None] * n
    for i in range(n - 1, -1, -1):
        c = 9 + cost[i + 1]; ch = ('L',)
        for L in range(3, mlen[i] + 1):
            v = 17 + cost[i + L]
            if v < c:
                c, ch = v, ('M', L, moff[i])
        if kind == 2:
            r = run[i]
            for L in range(4, min(18, r) + 1):
                v = 17 + cost[i + L]
                if v < c:
                    c, ch = v, ('F', L)
            for L in range(19, min(274, r) + 1):
                v = 25 + cost[i + L]
                if v < c:
                    c, ch = v, ('G', L)
        cost[i] = c; choice[i] = ch
    tokens = []; i = 0
    while i < n:
        ch = choice[i]
        if ch[0] == 'L':
            tokens.append((True, data[i])); i += 1
        elif ch[0] == 'M':
            L, off = ch[1], ch[2]
            tokens.append((False, bytes([off & 0xFF, (L - 3) << 4 | off >> 8]))); i += L
        elif ch[0] == 'F':
            L = ch[1]; off = (L - 3) << 8 | data[i]
            tokens.append((False, bytes([off & 0xFF, 0xF0 | off >> 8]))); i += L
        else:
            L = ch[1]; off = L - 19
            tokens.append((False, bytes([off & 0xFF, 0xF0 | off >> 8, data[i]]))); i += L
    if kind == 1:
        tokens.append((False, b'\x00\x00'))
    return _pack(tokens)


def _selftest():
    import os, random, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import vp_scene_text as vst
    rnd = random.Random(1)
    for trial in range(200):
        n = rnd.randrange(0, 3000)
        alpha = rnd.choice([b'\x00', b'ab', b'abcdefgh', bytes(range(256))])
        d = bytes(rnd.choice(alpha) if rnd.random() < 0.8 else rnd.randrange(256) for _ in range(n))
        for kind, dec in ((1, vst.slz1), (2, vst.slz2)):
            assert dec(compress(d, kind), len(d)) == d, (trial, kind)
    print('self-test ok')


if __name__ == '__main__':
    _selftest()
