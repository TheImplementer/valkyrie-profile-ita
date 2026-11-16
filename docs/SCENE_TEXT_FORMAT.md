# The scene text format

Valkyrie Profile's story is told in **scenes**: a room of a town, a dungeon floor, a cutscene. Each of
the 1,118 scenes on a disc carries its own script, its own lines of text and its own font. This
document explains how that text is stored, from the bytes of one line up to the whole block.

Everything below was checked on all 1,118 scenes of disc 1 (19,015 lines). How the game finds a
scene's package on the disc is in `docs/VALKYRIE_BIN_TABLE.md`. How its data is compressed is in
`docs/SLZ_COMPRESSION.md`.

## Where it is

Each scene's package has a **text part** (part type 4): an SLZ container of two streams.

```
text part (SLZ container)
 ├─ stream 0, SLZ type 1: the scene block = header + event script + text index + text records
 └─ stream 1, SLZ type 2: the scene font = the glyphs this scene's text uses
```

The game decompresses both when the scene loads. The block goes to a buffer whose address is kept
in `0x8007F1AC`, and the font to one kept in `0x8007F1D8`.

## The scene block

```
+0x00  header: seven 32-bit words, hdr[0] to hdr[6]
+0x1C  event script: hdr[0] bytes of 32-bit instructions
       index: hdr[3] 16-bit offsets, one per record
       records: hdr[3] records, back to back, each ending with a 0 byte
```

| word | meaning |
|---|---|
| hdr[0] | size of the event script in bytes, so also where the index starts (counted from `0x1C`) |
| hdr[1] | always 0 |
| hdr[2] | the scene's main function: an instruction number in the script |
| hdr[3] | number of text records |
| hdr[4] | total size of the records in bytes, with their end bytes |
| hdr[5], hdr[6] | two entry points in the shared library, below |

* **The script** is a list of 32-bit instructions, with the opcode in the top byte. Its first 10,564
  bytes (`0xA51` instructions) are a **shared library**, the same in every scene. The scene's own code
  follows. The script shows a line by pushing its record number and calling a library function.
  `docs/EVENT_SCRIPT.md` explains the script.
* **The index** has one 16-bit offset per record, counted from the end of the index. The first offset
  is always 0, and each record starts right where the previous one ends.

Since the records sit back to back, a longer line moves every record after it. When the editor
rebuilds a block, it rewrites the index and hdr[4].

## Records 0 to 13: system messages

The first 14 records are the same in every scene, byte for byte: "The item has broken.", the
Artifact prompts, "Exit to World Map?", "Battle" and so on. The game shows them through the shared
library and the field engine, whatever scene is loaded. The scene's own lines start at record 14.

## A record

A record is a string of **glyph codes** and **commands**, ended by a 0 byte:

| bytes | meaning |
|---|---|
| `01`-`7F` | one glyph: the character drawn by glyph number 1 to 127 of this scene's font |
| `80`-`FF` then one byte | a two-byte code: a command, or a glyph number above 127 |
| `00` | end of the record |

A two-byte code `b1 b2` has the value **`(b1 & 0x7F) | b2 << 7`**:

* **`0x4000` or more** is a command, number value − `0x4000`. Its argument bytes follow, and their
  number depends on the command. An argument byte may be 0: that isn't an end, because the decoder
  skips arguments.
* **Below `0x4000`** is a glyph number of 128 or more. Only scene 0, a Japanese debug menu with 352
  glyphs, uses them.

So command *c* is written as the two bytes `0x80 | c` and `0x80` (for *c* up to 127).

### Commands

The number of argument bytes for each command comes from the game's text renderer (its handlers,
through the table at `0x8007DC6C`). With these counts every record on the disc decodes cleanly. The
uses are counted over disc 1:

| cmd | bytes | written as | meaning | uses |
|---:|---:|---|---|---:|
| 0 | 0 | a line break | new line | 20,425 |
| 1 | 0 | `<page>` | new page: wait for a button, then clear the box | 306 |
| 3 | 1 | `<3:0c>` | typing speed: frames per character from here on; `ff` = the player's text-speed option | 5,603 |
| 4 | 1 | `<4:02>` | text colour; `00` = normal | 4,530 |
| 5 | 0 | `<5>` | centre this line in the box | 20,331 |
| 6 | 2 | `<6:970d>` | queue a portrait by number, for a speaker the script gives no face | 49 |
| 7 | 1 | `<7:02>` | horizontal scale (a long dash is a stretched `-`) | 180 |
| 8 | 1 | `<8:02>` | vertical scale | 2 |
| 12 | 2 | `<12:5109>` | preload a voice clip for the next spoken line | 297 |
| 14 | 1 | `<14:00>` | insert a number, such as the EXP just earned | 3,356 |
| 17 | 2 | `<17:7800>` | go on by itself after N frames (`0x0078` = 120 frames, two seconds) | 753 |
| 18 | 0 | `<18>` | start the queued voice clip here | 640 |
| 19 | 2 | `<19:0100>` | show the queued portrait: `0100` bottom left, `0000` bottom right, `ffff` the box's side | 1,834 |

Commands 2, 9, 10, 11, 13, 15 and 16 aren't used on the disc. Command 2 stops the text pass, and the
others do nothing. Two-byte arguments are
little-endian: `<17:7800>` is the bytes `78 00`, 120. Each command was tested on an edited disc;
`VP_DIALOGUE_LOAD_PATH.md` has the tests.

### A spoken line

Speech is laid out the same way everywhere: the speaker's name, a line break, then `「` and the words.
The name is part of the text, not a separate field. Here is Valkyrie's first line in Valhalla
(scene `0x16`, record 14), all 39 bytes:

```
58 38 48 3D 4B 3B 35 34   Valkyrie        8 glyphs
80 80                     line break      command 0
61                        「
83 80 04                  <3:04>          type at 4 frames per character
92 80                     <18>            start the voice clip
62 3C 52 31 3E 3C 39 36 38 48 49 35 46
                          How nostalgic   13 glyphs
83 80 0C                  <3:0c>          slow down to 12 frames per character
55 55 55                  ・・・
91 80 78 00               <17:7800>       go on after 120 frames
00                        end
```

The voice clip is loaded before the line: by the script for the first line of a conversation
(built-in `0xAB`), and by the previous line's `<12>` after that. The record only says where it starts. There's no `<19>`, so this line shows no portrait.

## The scene font

Stream 1 of the text part is the scene's font:

```
u32 n          number of glyphs, a multiple of 4 (96 to 124; 352 in scene 0)
u32 12         rows per glyph
n bytes        the width of each glyph in pixels (0 to 12)
n × 24 bytes   the bitmaps: 12 rows of 16 bits each, little-endian, highest bit = leftmost pixel
```

Glyph number *c* is entry *c* − 1. So in scene `0x16`, code `62` is glyph 98, which is `H`, 8 pixels
wide.

**Each scene has its own font, numbered in the order its records first use each glyph.** Records 0-13
are the same in every scene and use 96 glyphs, so codes 1 to 96 are the same everywhere. Codes from 97
up depend on the scene: `0x62` is `H` in Valhalla but may be another letter elsewhere. To read a line, the tools find each glyph's character by its **bitmap**.
`docs/FONTS_AND_GLYPHS.md` explains the fonts in more detail. The tools name 158 bitmaps in all: letters, punctuation, two sets of digits,
the button symbols, arrows, `♪`, `▼`, and a few Japanese characters in debug text.
`vp_glyph_names.json` maps each one (by a hash of its bitmap) to its character.

**Adding a character.** The English font has no `è`. When an edit uses a character the scene's font
lacks, the editor adds a glyph to the end of that scene's font. The accented letters are drawn from
the game's own letters (`vp_accents.py`). Fonts must keep an even number of glyphs: the renderer reads
the bitmaps as 16-bit values, and an odd *n* puts them at odd addresses, which crashes the game. So the
editor pads with a blank glyph.

## How the tools show it

`vp_text_editor.py export DISC.cue --scene 0x16` writes a scene's records as text. Each `@@ N` line
starts record N:

```
@@ 14
Valkyrie
「<3:04><18>How nostalgic<3:0c>・・・<17:7800>
```

* **Text:** characters stand for glyphs, real line breaks for command 0, and `<page>` for command 1.
* **Commands:** `<N>` or `<N:hex>` with their argument bytes in hex, as on the disc.
* **Raw glyphs:** `{xx}` is a glyph code the tools don't have a character for.

`build` turns this back into bytes. It uses each character's existing code where the font has it and
adds glyphs where it doesn't. Then it rebuilds the index and header, compresses the block and font,
and writes the package back in place. The files in `edits_it/` are in this format.

## Not known yet

* **hdr[5] and hdr[6]:** they point into the shared library (most often instructions 492 and 2120).
  What the game uses them for hasn't been traced.
