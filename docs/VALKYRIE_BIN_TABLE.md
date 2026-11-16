# The file table in VALKYRIE.BIN

Almost all of Valkyrie Profile lives in one file on each disc, `VALKYRIE.BIN` (635 MB on disc 1).
It holds thousands of smaller files: scenes, music, voice clips, menus, videos, program code. The
disc's own file system doesn't list them. The game finds them through a table at the start of
`VALKYRIE.BIN`, which it reads once at boot. This document explains how that table works.

Everything below was checked on both Redump discs of the USA release. Every file's position and size
decodes as described, and each file ends exactly where the next one starts.

## Where the table is

The table fills the first **13 sectors** of `VALKYRIE.BIN` (a sector is 2,048 bytes). That is 6,656
little-endian 32-bit words. The first file starts right after it, at sector 13.

```
VALKYRIE.BIN
 sectors 0-12    the table (encrypted)
   words 0x0000-0x13FF   one word per file: its position on the disc, and the high byte of its size
   words 0x1400-0x18FF   one byte per file: the low byte of its size (a second encryption)
   words 0x1900-0x19FF   not decrypted by the game; contents unknown
 sector 13 on    the files, back to back in table order
 the last 13,500 sectors    padding: zeros, with a short marker every 50 sectors
```

## Step 1: decrypting it

The whole table is XORed with a key that changes after every word. To decrypt, start with the key
`0x64283921`. XOR it with the first word, update the key, XOR the second word, and so on:

```python
K0 = 0x64283921
key = K0
for i, w in enumerate(words):
    words[i] = w ^ key
    key = (~(key ^ (key << 1)) & 0xFFFFFFFF) ^ K0     # the next key
```

The key update is a kind of linear-feedback shift register (LFSR). It repeats every 32 words. The
game does this in `cd_subsystem_init` (`0x80011D38` in the executable): it reads the 13 sectors and
decrypts the first 6,400 words into RAM at `0x80090024`.

For example, word `0xE30` is `BE053F14` on the disc, the key at that point is `BE663921`, and
`BE053F14` XOR `BE663921` is `00630635`.

## Step 2: reading an entry

After decryption, word *n* (from 1 on) describes file *n*. Its four bytes, lowest first:

| byte | meaning | `00630635` |
|---|---|---|
| 0 | minutes of the file's disc position, in BCD | `35` |
| 1 | seconds, in BCD | `06` |
| 2 | frames (1/75 of a second), in BCD | `63` |
| 3 | high byte of the file's size in sectors | `00` |

**The position is a CD address, in the form the CD drive takes.** CD-ROMs locate data by time: minutes,
seconds and frames, with 75 frames (sectors) per second. BCD writes each decimal digit in 4 bits, so
`0x35` means 35. The PlayStation's CD command `Setloc` takes exactly these three bytes. So the game
can pass them straight to the drive with no arithmetic. Word `0xE30` points to 35:06:63.

**Converting to a sector of VALKYRIE.BIN.** Count frames: (35 × 60 + 6) × 75 + 63 = 157,863. That is the
disc's sector number, plus 150 because every CD starts its count at 00:02:00. `VALKYRIE.BIN` itself
starts 150 sectors into the data, so subtract 300 in all: sector **157,713**.

```python
def bcd(x):
    return (x >> 4) * 10 + (x & 15)

def sector(word):
    frames = (bcd(word & 0xFF) * 60 + bcd(word >> 8 & 0xFF)) * 75 + bcd(word >> 16 & 0xFF)
    return frames - 300
```

Word `0xE30` is the package of scene `0x16`, Valhalla, where the game opens. Scene packages start at
word 3610, so scene *k* is word 3610 + *k* (see `VP_DIALOGUE_LOAD_PATH.md`, "Which scene is which").

## Step 3: the size

A file's size in sectors is split in two:

* the **high byte** is byte 3 of its word, as above;
* the **low byte** is in a separate byte table that starts at word `0x1400` (byte offset `0x5000`).
  It holds one byte per word, so file *n*'s byte is at offset `0x5000` + *n*.

The byte table has its own encryption. It isn't decrypted with the word key, but XORed with eight
bytes repeated, `63 C7 2B 1F 43 A7 8B FF`, starting from *n* mod 8:

```python
K8 = bytes.fromhex('63c72b1f43a78bff')
size = (word >> 24) << 8 | (raw[0x5000 + n] ^ K8[n % 8])
```

For Valhalla (*n* = `0xE30`, *n* mod 8 = 0), the byte on disc is `0x1E`, and `0x1E` XOR `0x63` = `0x7D`
= 125 sectors. That matches the next file's start: 157,713 + 125 = 157,838.

**The game doesn't seem to use the byte table.** It loads file *n* by reading words *n* and *n* + 1, and
the next file's position marks where this one ends. In RAM, the boot code runs the word decryption
over the byte table too, which scrambles it, and later reuses that memory. The tools also take sizes
from the next word. The byte table still gives the right size for every file, so it works as a
cross-check.

## Empty words

Some words are 0: no file. Disc 1 has 4,472 files among words 1 to 4859, and 387 empty words in
between (word 3, words 2646-2653 and others). The 260 words after 4859 are empty too. Their size bytes are 0 too. The files themselves have no
gaps: in word order, each one starts where the previous one ends.

When the next word is empty, a file's end is the start of the next file that is not empty. That is
what `file_end` in `vp_scenes.py` does.

| | disc 1 | disc 2 |
|---|---:|---:|
| `VALKYRIE.BIN` sectors | 310,265 | 317,452 |
| files | 4,472 | 4,451 |
| last word used | 4859 | 4866 |
| empty words before it | 387 | 415 |
| sectors after the last file (padding) | 13,500 | 13,500 |

The 13,500 sectors of padding are exactly three minutes of disc time.

## What the files are

The numbering is fixed, so code refers to files by word number. Some landmarks on disc 1:

| word | file |
|---:|---|
| 1 | boot and title code |
| 2 | "これはディスク１です" ("this is disc 1") |
| 8 | 11,760 sectors of video |
| 1490, 2292 | game program overlays (2292 is the field engine) |
| 2294 | a small text file of debug messages |
| 3605-3608 | shared packages, loaded alongside the scenes |
| 3610-4727 | the 1,118 scene packages, one per scene id |
| 4733, 4734 | the map screen and its graphics |
| 4735-4765 | automap packages |
| 4797 | the world map's text |

What's inside a scene package, and how its text is found and drawn, is in `VP_DIALOGUE_LOAD_PATH.md`.

## An early misreading

The first tools (`DIALOGUE_SEARCH.md`) decrypted only sector 0 and read each word as a "type" byte
followed by a 24-bit sector number. On disc 1, byte 0 of words 9 to `0x1FF` is 2, 3 or 4, so it looked
like a file type, and the scan stopped at the first word where it wasn't. That gave the "503-entry file
table". Those bytes are the minutes of each file's position: the files in those words sit between
minutes 2 and 4 of disc 1. On disc 2 the same words hold minutes 0 and 1.

## Not known yet

* **Word 0** is `19563824` on both discs. It isn't a file. It may be a checksum or a version number.
* **Words `0x1900`-`0x19FF`**: the game reads them but doesn't decrypt them.
* **Where the eight-byte key comes from**: it may be a byte-sized form of the word LFSR, but this
  hasn't been worked out.
* **The marker in the padding**: every 50th padding sector starts with a few bytes
  (`08 00 37 01 A0 B5 6C 00`), and the rest is zero.

## Why the translation never changes the table

The table is read once at boot, and every file's room on the disc is fixed by the next file's start.
So `vp_text_editor.py` never moves a file. It rewrites a scene package in place, and refuses an edit
if the rebuilt package needs more sectors than the package had. The text is recompressed, and so far
every edit has fit. The decryption is `directory()` in `vp_scenes.py`, and the
conversion to a sector is `sector()`.
