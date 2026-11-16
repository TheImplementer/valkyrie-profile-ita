# World map text

The world map has its own text: Freya's first lessons in the sky, the description of each town and
dungeon, and messages such as "Press START button to begin spiritual concentration." None of it is
in the scenes or the 13 menu tables. This document explains where it is and how it's stored.

Everything below was checked on both discs: the world-map file is the same on each.

## How it was found

* **Not a scene:** while Freya's lesson plays, the game never runs the routine that shows scene text.
  The world map draws its boxes itself.
* **A memory dump:** a dump of the console's RAM, taken during the lesson, was searched for the
  lines' *pattern*: bytes that are equal exactly where the letters are equal, whatever the encoding.
  That found "spiritual concentration" at `0x8009DB54`.
* **The disc:** every SLZ stream on the disc was decompressed and searched for the same bytes. One
  matched: file **4797**, at byte 248,520.

## File 4797: a bundle of streams

File 4797 isn't a package like a scene's. It's a list of 67 SLZ streams with a small table in front:

```
u32 n                 67
n × u32               for each stream: its byte offset in the file (bits 0-23)
                      and a group number (bits 24-31)
the streams           each one "SLZ" + type, sizes, then its data, starting on a multiple of 4
```

| streams | group | what |
|---|---:|---|
| 0-37 | 0 | pictures (TIM images): the map's graphics |
| **38** | 1 | **the font**, 76 glyphs |
| **39** | 2 | **the strings**, 136 of them |
| 40-46 | 3-6 | not identified yet |
| 47-66 | 10 | not identified yet; 20 streams of the same kind |

The file has 189 sectors, and the streams end at byte 386,557 of 387,072. The streams are
compressed with SLZ (`docs/SLZ_COMPRESSION.md`), and each is a single stream, not a chain.

## The font (stream 38)

The font has the same layout as a scene font (`docs/FONTS_AND_GLYPHS.md`): a count, the widths and
the 12-row bitmaps. Its 76 glyphs are numbered in the order the strings first use them, and the last
one is a blank glyph that only makes the count even.

## The strings (stream 39)

The strings use the menu tables' layout (`docs/MENU_AND_BATTLE_TEXT.md`):

```
u32 text_offset            = 4 + 8 × 136
136 × (u32 id, u32 offset)
strings                    each ending with a 0 byte
```

But the text inside is encoded like a scene record (`docs/SCENE_TEXT_FORMAT.md`): one byte per glyph of
stream 38, and two-byte commands such as `80 80` for a line break, `81 80` for a new page, and
`<3:..>` for the typing speed.

The ids fall into four groups:

| ids | strings | what |
|---|---:|---|
| `0x01`-`0x0B` | 11 | messages: "Press START button to begin spiritual concentration.", "This Chapter is concluded.", the "2 Periods or more are necessary" warnings, and a few debug labels ("Start Flag", "Delete flag") |
| `0x64`-`0x89` | 35 | a description of each town and dungeon ("A merchant town in the Gerabellum region…") |
| `0xC8`-`0x10A` | 67 | a list of characters with their home towns ("Arngrim (Artolia)") and of places |
| `0x12C`-`0x16A` | 23 | Freya's lessons on the first visit (the sky, the first soul, the undead) and her warning |

The first entry of the place descriptions and of the name list is "Nashi", Japanese for "none".

**Command 2.** The lessons use command 2, `<2>`, 36 times, at the end of spoken lines and before page
breaks. No scene uses it. It looks like a wait for a button, but the world map's own text code, which
reads it, hasn't been traced.

## How the game loads it

In the memory dump, the font is at `0x8009CEE4` and the strings at `0x8009D668`. Each sits in a heap
block of its unpacked size, so a longer table or font just gets a bigger block. What limits the text
is the file's 189 sectors on the disc.

## Editing it

```bash
python3 vp_text_editor.py export DISC.cue --worldmap -o worldmap.txt
```

The edit file starts with `# Valkyrie Profile world map text` and lists messages by hex id, with
the text written as in a scene edit file. From `edits_it/worldmap.txt`:

```
@@ 0x012c
Freya
「Senti qualcosa?<2>
```

`build`, `vp_release.py` and the GUI all pick this file up. `vp_worldmap_text.py` does the work:

* **Encoding:** it encodes with the scene editor's encoder. A character the font lacks, such as `è`,
  is added as a new glyph, and the count is kept even.
* **Rebuilding:** it rewrites streams 38 and 39 in their own SLZ types and rebuilds the offset table,
  keeping the group numbers. With no edits, the file rebuilds byte for byte.

The Italian translation changes 20 of these messages so far: the START prompt and Freya's first
lessons. The lessons up to `0x149` were checked in the emulator: the boxes size themselves to the
text, and no line wraps. The later ones play after Arngrim's chapter, and were checked by reading the
built disc back.

## Not known yet

* **Streams 40-66:** what groups 3 to 10 hold.
* **The name list:** where the game shows the list of characters and places (`0xC8`-`0x10A`).
* **Command 2:** what the world map's text code does with it, exactly.
