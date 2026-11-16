# Menu and battle text

Not all of Valkyrie Profile's text is scene dialogue. Item names and their help lines, the camp menu,
skill names, the save screen, the battle menu, monster names and Lenneth's hints in battle are kept
somewhere else: in **string tables**, each loaded with its own menu or battle screen. This document
explains those tables, how their text is encoded, and how the translation edits them.

The counts below were checked on disc 1. Dialogue is in `docs/SCENE_TEXT_FORMAT.md`, and the fonts
in `docs/FONTS_AND_GLYPHS.md`.

## The tables

There are 17 tables. A scan of every file that isn't a scene found exactly these.

**13 menu tables, 10,674 strings:**

| file | screen | strings |
|---:|---|---:|
| 6 | title screen and New Game menu (also song titles and character names) | 305 |
| 2175 | camp menu, top level | 64 |
| 2176 | camp menu: Skill | 1,506 |
| 2177 | Transfer | 926 |
| 2178 | Ability | 232 |
| 2179 | Use Item | 2,278 |
| 2180 | Divine Item | 1,893 |
| 2181 | Status | 1,026 |
| 2182 | Party | 947 |
| 2183 | Equipment | 1,329 |
| 2184 | Configure | 40 |
| 2185 | Data (save and load) | 87 |
| 4734 | world map (controls, place names) | 41 |

**4 battle tables, 1,848 strings:**

| table | contents | strings |
|---|---|---:|
| 1500.7 | battle menu, elements, skill and attack names | 333 |
| 1500.9 | character and monster names | 321 |
| 1806.1 | item, equipment and spell names and help lines | 1,080 |
| 1988.2 | battle dialogue: Lenneth's tactical hints, boss lines | 114 |

A battle table is named by its file and part: `1806.1` is part 1 of file 1806. File 1501 holds an
identical copy of both 1500 tables.

## Where they sit

**Menu packages.** Files 2175 to 2185 each have three parts:

* the sub-menu's code (type 18);
* the string table (type 4);
* its font (type 7).

File 6 is the title screen's 52-part package, with the table and font among its parts. In file 4734
the font is the second stream of the text part.

**Battle files** use a different package layout: a count, a 0, then a (flags, size) pair for each
part, then the parts. The table and its font are two neighbouring parts, each a one-stream SLZ
container.

The tables are compressed with SLZ (`docs/SLZ_COMPRESSION.md`). Among the menu tables, the six
smallest use type 2 and the rest type 1.

## The table layout

Menu and battle tables share one layout:

```
u32 text_offset            = 4 + 8 × n
n × (u32 id, u32 offset)   message id, and where its string starts, counted from text_offset
strings                    each ending with a 0 byte
```

* **Ids belong to a screen.** `0x64` is "New Game" in the title table, and a place name in the world
  map table. The camp sub-menus share most ids: 2,412 ids appear in more than one table, and an item
  keeps its id in every table that names it.
* **Order:** the ids are in ascending order, except in tables 2179 and 2180. There, the strings `[`
  and `]` (ids `0x1A8E` and `0x1A8F`) are listed twice. The tools keep every table's order as it is.
* **No sharing:** no two entries point at the same bytes, and none points into the middle of a
  string.

## Encoding: menus

Menu text is one byte per character, close to ASCII:

| bytes | meaning |
|---|---|
| `01`-`5F` | the ASCII characters `0x20`-`0x7E`: the byte plus `0x1F`. "Skill" is `34 4C 4A 4D 4D`. |
| `80` | line break |
| `81 80 nn` | colour `nn` for what follows: 07 the blue labels (LV, DME), 02 red ("Empty"), 01 orange, 03 black |
| `60`-`7F`, `81`-`85` | extra glyphs: icons, rules and the strips in skill help. They differ between menus. `81` is a glyph whenever the next byte isn't `80`. |

The colour code appears 478 times, mostly as colour 7. The byte `81` also appears 85 times as a glyph: in the Use Item
table's font, glyph `0x81` is a tall closing bracket, 6 pixels wide. Followed by a space (`01`), it ends
the stat lines in skill help. The encoding was first misread as "Scheme A",
which subtracted 1 above `0x40` and read lower case as upper case.

## Encoding: battle text

The battle tables use the same layout, and `80` is still a line break. But each byte is a glyph number
in that table's own font, numbered in order of first use, like a scene font. The item table's font
starts with E, l, i, x, r, from "Elixir". This is why early work saw the item table as "binary data,
not readable text".

To read it, the tools match each battle glyph's bitmap against the menu fonts, which use the same
typeface:

* **Named glyphs:** every glyph of the names table matches. The other tables have a few that don't:
  Japanese placeholders (空欄 "blank", 予約 "reserved", 未定 "undecided") and symbols.
* **Symbols:** four battle-only symbols are named by hand, `…`, `「`, `·` and `’`. The rest are kept as
  raw codes.
* **Padding:** each font ends with blank glyphs of width 0 that only make the count even.

## How the game loads them

* **A heap block per table:** each table, and each font, is decompressed into a heap block of its own
  size. A longer table just gets a bigger block. In a camp-menu memory dump, table 2175 is at
  `0x80102BC4`. Mid-battle, the 1806 item table is at `0x801197DC`, with 720 KB of heap free.
* **The disc is the limit:** a file can't outgrow its sectors (`docs/VALKYRIE_BIN_TABLE.md`). After
  the tools re-compress, the menu files have room for 92 bytes (Configure) up to 2.6 KB (Divine Item)
  more of compressed text. The battle files have 0.7 KB (1988) to 2.1 KB (1501). Text compresses
  about 2:1.

## Editing them

```bash
python3 vp_text_editor.py menus  DISC.cue                         # list the 13 menu tables
python3 vp_text_editor.py export DISC.cue --menu 2175 -o menu_2175.txt
python3 vp_text_editor.py export DISC.cue --battle 1806.1 -o battle_1806.1.txt
python3 vp_text_editor.py build  DISC.cue edits_it/*.txt -o OUT.cue
```

An edit file lists messages by id. From `edits_it/menu_2175.txt`:

```
@@ 0x0005
Abilità
```

* **Text:** a line break is a real line break, and `{81 80 07}` is raw bytes in hex (a colour, an icon).
* **Linked copies:** `build` also changes every other table that has the same id with the same
  original text. Renaming an item in one table renames it in the battle table and every camp
  sub-menu. 662 of the battle item table's strings are also in the camp menus. `--no-link` turns this
  off.
* **Rebuilding:** an unchanged string keeps its bytes. A changed one goes in its old place if it fits,
  or after the last string.
* **Width:** menu text is drawn at fixed places, so a longer label can run into the next one. `build`
  warns when an edit makes a string wider than the original, measured with the font's widths.
  `docs/GLOSSARY.md` lists the lines shortened for this.
* **New letters:** a menu font can't grow, so an accented letter replaces one of ten characters the
  menus never show. A battle font gains a glyph over its blank padding, up to 127 glyphs.
  `docs/FONTS_AND_GLYPHS.md` explains both.
* **Checks:** `check --menus` decodes and re-encodes all 10,674 menu and 1,848 battle strings, and
  rebuilds every table and font byte for byte.

## Not known yet

* **The 1806 and 1988 tables on screen:** they were checked by reading the built disc back. The item
  list and the battle hints haven't been seen in the emulator yet.
