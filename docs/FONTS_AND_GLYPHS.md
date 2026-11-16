# Fonts and glyphs

Valkyrie Profile doesn't have one font. Every scene, menu and battle table carries its own small
font, holding just the letters its text needs, in its own order. A few things aren't text at all, but
pictures of text. This document explains how the fonts are stored, how a byte of text becomes a
letter on screen, and how the translation adds letters the English game never had, such as `è`.

The counts below were checked on disc 1. How the scene text itself is stored is in
`docs/SCENE_TEXT_FORMAT.md`.

## Where the fonts are

| text | font | glyphs | how a byte picks a glyph |
|---|---|---:|---|
| scene dialogue | one per scene, stream 1 of its text part | 96-124 (352 in scene 0) | glyph number, in order of first use |
| world-map text (file 4797) | stream 38 of the file | 76 | glyph number, as in scenes |
| the 13 menu tables | one per menu package | 100-136 | ASCII: byte + `0x1F` |
| the 4 battle tables | one per table, beside it | 56-112 | glyph number, in order of first use |
| menu titles (italic) | a picture sheet plus a table | 96 characters | ASCII |
| title-screen menu | none: the labels are pictures | | |

## The glyph format

Scene, world-map, menu and battle fonts all use the same layout:

```
u32 n          number of glyphs
u32 12         rows per glyph
n bytes        each glyph's width in pixels, 0 to 12
n × 24 bytes   each glyph's bitmap: 12 rows, one 16-bit little-endian value per row
```

Glyph number *c* is entry *c* − 1. In a row, the highest bit is the leftmost pixel. Glyphs are one
bit per pixel; colour and shadow are added when the text is drawn.

Here is a made-up 5-pixel `L` (not one of the game's glyphs), to show the encoding:

```
row  value   pixels         bytes on disc
 0   0x0000  ............   00 00
 1   0x8000  #...........   00 80
 2   0x8000  #...........   00 80
 ⋮
 9   0xF800  #####.......   00 F8
10   0x0000  ............   00 00
11   0x0000  ............   00 00
```

## Scene fonts: one per scene, in order of first use

Each scene's font was built from the text it shows. Its glyphs are numbered in the order they first
appear in the scene's records, from record 0 on. All 1,118 fonts on disc 1 follow that order exactly.

**So most codes are the same in every scene.** Records 0-13 are the same 14 system messages in every
scene, and together they use 96 different glyphs. Those take codes 1 to 96 everywhere, with the same
bitmaps in all 1,118 fonts. Code `0x31` is always the space, 4 pixels wide. Only a letter the system
messages lack gets a code from 97 up, and that code depends on the scene. In Valhalla, `H` is code
`0x62`. In another scene, `0x62` may be a different letter or not exist at all.

**Padding.** Every scene font has a multiple of 4 glyphs. Each was padded with up to 3 blank glyphs of
width 0 that no text uses. No glyph that text uses has width 0.

**Identifying characters.** Because codes from 97 up change from scene to scene, the tools don't
use a code page. They identify each glyph by its bitmap. Scenes 1 to 1117 use 125 different
bitmaps in all. Scene 0, a Japanese debug menu, and a debug text file add Japanese characters.
`vp_glyph_names.json` names 158 bitmaps, keyed by a hash of each bitmap, so the repository holds no
font data.

## Menu fonts: ASCII

The 13 menu tables (the title screen's New Game menu, the camp menu and its sub-menus, Configure,
Data and the world map) work differently. Each menu package has one font, and a text byte maps to a
glyph like ASCII:

* `0x01`-`0x5F` are the characters ASCII `0x20`-`0x7E`, the byte plus `0x1F`. So `S` (`0x53`) is
  byte `0x34`. All 78 named glyphs in that range are the ASCII character in all 13 fonts.
* `0x60` and up are extra glyphs, such as icons, rules and the strips in the skill help. They differ
  between menus.
* `0x80` is a line break, and `0x81 0x80 nn` sets a colour.

The menu fonts use the same typeface as the scene fonts.

## Battle fonts: their own numbering again

The four battle tables (the battle menu and attack names, character and monster names, item and spell
names with their help lines, and the battle hints) each sit beside their own font. Like scene fonts,
these number their glyphs in order of first use: the item font starts E, l, i, x, r, from "Elixir".
The tools identify each glyph by comparing its bitmap with the menu fonts. A few battle-only symbols
(`…`, `「`, `·`, `’`) are listed by hand in `vp_battle_text.py`. `0x80` is the line break, so a battle
font can hold at most 127 glyphs. Each ends with blank glyphs that pad it to an even count.

## Italic menu titles

The titles at the top of the menu screens (such as "New Game", or a sub-menu's name) use an italic
font that works like a sprite sheet:

* **The sheet:** a 4-bit picture, 256 × 128 pixels, that the game puts in video memory at
  (896, 256).
* **The table:** 96 entries of 10 bytes, for ASCII `0x20`-`0x7F`. Each gives the glyph's width,
  height, x and y offsets, advance, and its position on the sheet. An entry with width 0 is drawn as a
  12-pixel gap.

The game draws a title as one sprite per character. The disc has two identical copies of this font,
in the title package (file 6) and in package 2173, which the camp menu loads.

## The title-screen menu: pictures

The title menu's labels (New Game, Continue and the rest) aren't text at all. Each is drawn on a
picture sheet (file 7), with a thin layer and a blurred glow behind it. The title code places each
label with a sprite whose size and position are written into its instructions. To translate them,
`vp_title_menu.py` renders new labels with a serif font, packs them into the sheet and patches those
instructions. `docs/VP_DIALOGUE_LOAD_PATH.md` has the details.

## How a glyph reaches the screen

For dialogue, the message box lays out and draws each line itself:

* **Advance:** each glyph moves the pen right by its width from the font, times the horizontal
  scale (`<7>`).
* **Wrapping:** at each glyph, the game measures the line up to the end of the current word. If
  that would pass the box's width, the line breaks before the glyph. A space at the start of a wrapped line isn't drawn.
* **Line height:** a line is 14 pixels: 12 rows of glyph plus 2, times the vertical scale (`<8>`).
* **Drawing:** each glyph is copied into a 16 × 14 cell with a shadow one pixel right and down, and
  drawn as a sprite. The colour command `<4>` picks one of four colour pairs, for the glyph and its
  shadow.

`docs/VP_DIALOGUE_LOAD_PATH.md` ("The message box") has the measured colours and the tests.

## Adding letters: `è` and friends

The English game has no accented letters in any font. `vp_accents.py` draws them from the game's
own letters: the base letter plus a 2-pixel accent. The fonts share one typeface, so one set of
accented letters works for all of them.

* **Lower case:** the letters start at row 4, so the accent goes in rows 1-2, where the dot of the
  `i` is.
* **Capitals:** they start at row 1, so the letter loses 2 rows and the accent goes on top.

It makes `à è é ì ò ù`, `á í ó ú`, `â ê î ô û`, `ä ë ï ö ü` and the capitals `À È É Ì Ò Ù Á Í Ó Ú`.

How each kind of font takes a new letter:

| font | how a letter is added |
|---|---|
| scene | a new glyph at the end, with the next code. The count is kept even: an odd count puts the bitmaps at odd addresses, which crashes the game when it reads them as 16-bit values. |
| world map | the same as a scene font |
| battle | a new glyph over a blank padding glyph, up to 127 glyphs |
| menu | can't grow, because the bytes are fixed ASCII. An accented letter replaces one of ten characters the menus never show, `{ } \ ^ _ ` \| @ < >`, the same one in every menu. So the menus can hold at most ten different accented letters. |
| italic titles | drawn into the sheet's free rows, from the base letter's italic glyph plus an accent, and the table entry of one of those ten characters points at it |
| title screen | the labels are rendered again as pictures |

For scene and world-map fonts, the editor takes each new glyph from `vp_glyph_db.json`: the most
common bitmap and width of every character across all scene fonts. It builds the file from the disc
the first time it runs. The accented letters are composed from it.

## Not known yet

* **Why a multiple of 4:** the game only needs an even count, as far as is known. Why the original
  fonts were padded to a multiple of 4 hasn't been found.
