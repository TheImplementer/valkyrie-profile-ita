# SLZ: how Valkyrie Profile compresses its data

Most of what Valkyrie Profile loads from the disc is compressed: scene scripts and text, fonts,
graphics and program code. The data starts with the letters `SLZ`, and the romhacking community calls
the two formats SLZ01 and SLZ02. This document explains both.

The numbers below come from a survey of every SLZ stream on disc 1: 13,772 of them, each decoded to
exactly the size its header gives, using exactly the bytes its header gives.

## The idea: say "copy that again" instead of repeating it

Both formats are kinds of LZ77, the family behind ZIP and PNG. The decoder writes its output one byte
or one chunk at a time. Each step is either:

* a **literal**: "the next byte is `0x41`"; or
* a **copy**: "go back 5 bytes in what you've written so far, and copy 9 bytes from there".

Text, scripts and pictures repeat themselves a lot, so copies replace much of the data. On disc 1,
type 1 streams shrink to 43% of their size and type 2 streams to 61%.

## The stream header

Every stream starts with a 16-byte header:

| offset | size | meaning |
|---:|---:|---|
| 0 | 3 | `SLZ` |
| 3 | 1 | type: 0 stored, 1 or 2 compressed |
| 4 | 4 | compressed size, the bytes after the header |
| 8 | 4 | uncompressed size |
| 12 | 4 | distance to the next stream, or 0 if this is the last one |

All numbers are little-endian. Several streams can follow each other as a **container**: each one's
last field says how far ahead the next one starts. That's always 16 + the compressed size, rounded up
to a multiple of 4. On disc 1, 12,395 streams end a container and 1,377 lead to a next one.

* **Type 0** isn't compressed: the data follows as is (127 streams, all with equal sizes).
* **Type 1** (5,000 streams) is decoded by the game's function at `0x800123CC`.
* **Type 2** (8,645 streams) is decoded by the function at `0x80012874`.

For example, the text part of every scene package is a container of two streams: the scene block
(script and text) as type 1, then the scene's font, usually as type 2. 354 of the 4,472 files in
`VALKYRIE.BIN` are a bare container. Others hold containers inside them, such as the parts of a
package, and some aren't compressed at all, such as video and voice clips
(`docs/VALKYRIE_BIN_TABLE.md` explains the files).

## Flag bytes and tokens

After the header, both types are a series of **groups**. Each group is one flag byte followed by up to
eight tokens. The flag's bits, lowest first, say what each token is:

* **1**: a literal, one byte copied to the output.
* **0**: a two-byte reference `b0 b1`, read as:

```
offset = b0 | (b1 & 0x0F) << 8      12 bits: 1 to 4,095 bytes back
code   = b1 >> 4                    4 bits: 0 to 15
```

The decoder keeps going until it has written the uncompressed size from the header.

### Type 1

* **A copy:** a reference copies **code + 3** bytes (3 to 18) from **offset** bytes back.
* **The end:** offset 0 ends the stream. All 5,000 type-1 streams on the disc end with this marker,
  and the game's decoder relies on it.

### Type 2

* **A copy:** codes 0 to 14 copy **code + 3** bytes (3 to 17), as in type 1.
* **Code 15 is a fill, not a copy:** it repeats one byte many times, which suits pictures and blank
  areas.
  * If the offset is below 256, a third byte follows: the value to repeat. It is repeated
    **offset + 19** times (19 to 274).
  * Otherwise the offset's low 8 bits are the value, repeated **(offset >> 8) + 3** times (4 to 18).
* **The end:** there's no end marker. The decoder stops at the uncompressed size.

On disc 1, type 2 streams use 2.7 million short fills and 1.0 million long ones.

### Copies that overlap

A copy may be longer than its offset, such as "go back 5, copy 9". The decoder copies one byte at a
time, so the bytes it has just written get copied again. That's how one copy repeats a pattern. Disc 1
has about 1.2 million such copies. No copy on the disc reaches back before the start of its stream.

## Two worked examples

**Type 1:** `BLAH BLAH BLAH!` (15 bytes) compresses to 12:

```
5F  42 4C 41 48 20  05 60  21  00 00
```

* `5F` is the flag byte, `01011111`. Read from the lowest bit: five literals, a reference, a literal,
  a reference.
* `42 4C 41 48 20` are the literals `BLAH `.
* `05 60` is a reference: offset `0x005` = 5, code 6, so it copies 9 bytes from 5 back. That's
  `BLAH BLAH`. The copy overlaps itself, so `BLAH ` is copied, then copied again.
* `21` is the literal `!`.
* `00 00` has offset 0: the end.

**Type 2:** 40 zero bytes then ten `0x07` bytes (50 bytes) compress to 6:

```
00  15 F0 00  07 F7
```

* `00` is the flag byte: both tokens are references.
* `15 F0 00` is code 15 with offset `0x015` = 21, below 256: a long fill. The value is the third byte,
  `00`, repeated 21 + 19 = 40 times.
* `07 F7` is code 15 with offset `0x707`, which is 256 or more: a short fill. The value is the low
  byte, `07`, repeated `0x7` + 3 = 10 times.

## Decoding in Python

These are the tools' decoders (`slz1` and `slz2` in `vp_scene_text.py`), which follow the game's:

```python
def slz1(p, ulen):
    out = bytearray(); o = 0; left = 0
    while o < len(p) and len(out) < ulen:
        if left == 0:
            flags = p[o]; o += 1; left = 8
        bit = flags & 1; flags >>= 1; left -= 1
        if bit:                                   # literal
            out.append(p[o]); o += 1
            continue
        b1, b2 = p[o], p[o + 1]; o += 2
        off = (b2 & 0xF) << 8 | b1
        if off == 0:                              # end marker
            break
        s = len(out) - off
        for i in range((b2 >> 4) + 3):            # copy, one byte at a time
            out.append(out[s + i])
    return bytes(out[:ulen])


def slz2(p, ulen):
    out = bytearray(); o = 0; left = 0
    while len(out) < ulen:
        if left == 0:
            flags = p[o]; o += 1; left = 8
        bit = flags & 1; flags >>= 1; left -= 1
        if bit:                                   # literal
            out.append(p[o]); o += 1
            continue
        b0, b1 = p[o], p[o + 1]
        off = b0 | (b1 & 0xF) << 8; code = b1 >> 4
        if code < 15:                             # copy
            s = len(out) - off
            for i in range(code + 3):
                out.append(out[s + i])
            o += 2
        elif off < 256:                           # long fill: value in a third byte
            out += bytes([p[o + 2]]) * (off + 19); o += 3
        else:                                     # short fill: value in the offset
            out += bytes([off & 0xFF]) * ((off >> 8) + 3); o += 2
    return bytes(out[:ulen])
```

## Compressing

To change text, the editor has to compress it again. `vp_slz.py` writes both types:

* **Finding matches:** it searches the whole 4,095-byte window, through chains of earlier positions
  that start with the same three bytes.
* **Choosing tokens:** it uses an **optimal parse**. Working back from the end of the data, it finds
  the cheapest way to encode each position. A literal costs 9 bits (8 plus its flag bit), a copy or
  short fill 17, and a long fill 25.

So its output is as small as the format allows for the matches it finds. On 300 random streams from
the disc, it came out 1.5% to 1.7% smaller than the game's own data. Every scene package got at least
248 bytes smaller. That spare room is what lets a longer Italian line fit where the English one was
(`docs/VALKYRIE_BIN_TABLE.md` explains why files can't grow).

`python3 vp_slz.py` runs a self-test: it compresses 200 random inputs with both types and checks that
they decode back exactly.

## A bug the early tools had

The first decoder (`bin_navigator.py`, not in this repository) decoded type 2 like type 1: code 15 as a
copy, and offset 0 as the end. Every type-2 stream with a fill came out wrong from its first fill
on. A 19-byte fill of zeros is `00 F0 xx`. Read the old way, that's an end marker, so the old tools
saw one stream end and a new one begin, and called the data "multi-stream". There are no such streams.
`docs/DIALOGUE_SEARCH.md` was written with that decoder, so its notes on images and blocks may be off.

## Not known yet

* **Which tool compressed the original data:** the game only decodes. Its streams were made once,
  during development, by a tool that isn't on the disc. Our `vp_slz.py` makes smaller output from the
  same data, 1.5% to 1.7% smaller, so that tool didn't find the smallest encoding. Whether it's a
  known compressor hasn't been checked. Knowing it would only help to rebuild the original bytes
  exactly: the game accepts any stream that decodes correctly, so the translation doesn't need it.
* **Type 0 in the game's code:** the tools read type 0 as stored data, and the sizes agree. The
  game's handling of type 0 hasn't been traced.
* **Copies before the start:** no stream on the disc has one, so what the game would do with one is
  untested.
