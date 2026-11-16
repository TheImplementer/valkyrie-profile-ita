# Valkyrie Profile (SLUS-01156): where the story dialogue lives and how it is loaded

Traced live in TinyPSX on 2026-11-12. This follows up `docs/VP_DIALOGUE_FORMAT.md`. That document had the
glyph/command idea right. What was missing:

* the **block structure** (header, record index);
* the **argument byte counts**;
* the **per-scene fonts**, which is why capitals "change";
* a bug in **SLZ type 2** decoding;
* the **package directory**.

Every claim below was observed in the running game, read from its code, or checked byte for byte against
the disc. Guesses are marked as such.

**Result:** `vp_scene_text.py --all` decodes all **1,119** text containers on the disc, 19,023 records,
with no command desync → `vp_all_scene_text.txt`. They are the 1,118 scenes, one per scene id, plus one
small non-scene file (directory word 2294). The only unknown glyphs are in scene 0, a Japanese debug
menu. `vp_scenes.py` says which scene is which, and `vp_flow.py` gives each scene's lines in play order.

## Short answer

```
VALKYRIE.BIN header, sectors 0-12 (encrypted, the same LFSR as sector 0 run on)
 └─ words 1-0x13FF  the file table: one word per file, BCD disc position MM SS FF + size
            (docs/VALKYRIE_BIN_TABLE.md)
      └─ scene id k → word 3610 + k; e.g. scene 0x16 → word 0xE30 = 35:06:63
         → LBA 157,863 = VALKYRIE.BIN sector 157,713
scene package: one contiguous read (here 125 sectors)
   u32 count (14), u32 header size (0x78), 14 x (u32 type, u32 size), then the parts
   └─ part type 0x04 = text container: SLZ entries
       ├─ entry 0, SLZ type 1: SCENE BLOCK = event script + text index + records
       └─ entry 1, SLZ type 2: SCENE FONT = this scene's glyphs (bitmaps + widths)
```

| what | example scene (Valhalla, "How nostalgic") |
|---|---|
| directory entry | header word `0xE30` = `00630635` → BCD 35:06:63 |
| package on disc | LBA 157,863 = `VALKYRIE.BIN` sector 157,713, 125 sectors, `Setloc` + `ReadN` |
| package in RAM | DMA3 → `0x8018FCB4`..`0x801CE4B4` (12-byte sector headers go to `0x8002CF64`) |
| text container | part 5 at `0x801C8EF4` = **`VALKYRIE.BIN` offset `0x13441A40`** (sector 157,827 + `0x240`) |
| scene block | `0x800F2DE4`..`0x800F7709` (`0x4925` bytes); global `0x8007F1AC` |
| scene font | `0x800F771C`..`0x800F81B0` (`0xA94` bytes); global `0x8007F1D8` |
| text index / base | globals `0x8007F1D0` (`0x800F6884`), `0x8007F1D4` (`0x800F68FE`) |

Decompressing the container from `VALKYRIE.BIN` gives both RAM blocks byte for byte. So do 8 more
parts of the same package, of both SLZ types.

## Timeline (New Game, no input after choosing Normal)

Reproduce with `vp_newgame.replay` (Cross at the title and at the difficulty screen). **The frame
numbers in this document were measured before TinyPSX raised the SPU IRQ (D-057).** Since then the
title-screen voice ends on time, and every event after the title screen happens **153 frames earlier**,
in the same order: Valhalla loads at 6301, and record 14 is shown at 7479.

```
tinypsx --bios scph1001.bin --disc vp.cue --replay-input vp-analysis/vp_newgame.replay \
        --labels labels/bios_scph1001.labels --labels labels/valkyrie_profile_usa.labels --debug
```

| frame | event |
|---|---|
| 671 | `cd_subsystem_init` reads `VALKYRIE.BIN` sectors 0-12 (`0x6800` bytes) and decrypts `0x1900` words to `0x80090024` |
| 6454 | `Setloc 35:06:63`, `ReadN`: the scene package streams in |
| 6590-6591 | `decompress_resource` unpacks the scene block (call at `0x8006BDD8`) and the font (`0x8006BE0C`) |
| 7632 | the scene script calls the message wrapper with record 14 |
| 7750 | `Valkyrie「How nostalgic・・・` is on screen; it auto-advances after 120 frames |

## The load path in code (field-engine module at `0x8002F824`)

1. **Directory.** At boot, `cd_subsystem_init` (EXE `0x80011D38`) reads the 13-sector archive header and
   decrypts it with the sector-0 LFSR, continuing the key stream past sector 0. 5,120 of the 6,400
   words stay as decrypted; words `0x1400-0x18FF` are overwritten later (working memory).
   `bin_navigator.py` decrypts sector 0 only, and stops at the first entry type that isn't 2-4. So it
   never reaches the directory, and the "files beyond sector ~30k" come out uncatalogued.
2. **CD read.** `func_8006C49C` (scene loader) → `func_80011764` (EXE) → EXE code that sends
   `Setloc`/`ReadN`. libcd's `CdRead` (`0x8002714C`) isn't used. The report's names for that EXE code,
   `pad_send_command_no_data` (`0x800255F4`) and `pad_transfer_command` (`0x80026538`), look wrong: here
   they send CD-ROM commands.
3. **Decompress.** `func_8006B6C4` decompresses the package parts in order. For the text container it
   gets buffers from `0x8001226C` and stores them in `0x8007F1AC` (block) and `0x8007F1D8` (font).
4. **Pick a line.** The scene's script pushes the arguments and calls a library wrapper
   (`0x33C`, `0x3E2`, `0x3F5`, …). The wrapper runs op `0x92` (show message) in `func_800748EC`, which
   calls `func_800532C4(box, record)`. That computes `text_base + index[record]` and stores it at
   `box+0x328`. Message boxes live in a table at `0x8007F120`.
5. **Draw.** `func_80052C3C` makes a layout pass over the record. `func_8004BF6C` draws it each frame:
   the read is at `0x8004C208`, commands go through the jump table `0x8007DC6C`, and glyph widths come
   from `font[7 + code]`.

## Which scene is which

**A scene id picks the package: directory word = 3610 + id.** Traced with `rwatch 0x800938e4` (the
Valhalla directory word) and a watch on the loader object's file field:

1. The script runs built-in **0x9E** with the scene id in the operand's low 16 bits (stack form: the
   id is popped first). Its handler `0x80075CC8` calls `func_8006C270(id, package)`.
2. `func_8006C270` looks the id up in a **10-byte-per-scene table at `0x80080618`** (field-engine
   data, 1,118 entries for ids `0x000`-`0x45D`). It stores field +0, the directory word, in the
   loader object (`0x801EDAF8`, function `0x8006C49C`) at +0x10. Field +8 is a shared package (3605 or
   3606), used unless the 0x9E call names a different one.
3. The loader passes that word to `func_80011764`. It reads header words *n* and *n*+1 (the next
   word gives the size), then `Setloc`/`ReadN`.

Field +0 is **3610 + id for all 1,118 entries**, so the mapping is plain arithmetic. Valhalla is scene
`0x16` → word 3632 = `0xE30`. Built-in 0x9F then makes the switch to the selected scene. The other
fields drive the **automap** (they are 0 for the ids below `0x7D`, which have no map):

| field | meaning | evidence |
|---|---|---|
| +0 | scene package, 3610 + id | traced live |
| +2 | map group package, 4735-4765 | read by `0x800556F0` / `0x80055858` / `0x80055B30`, the map task, which loads it and then the map-screen overlay (file 4733, with graphics in 4734); nonzero in exactly the 923 scenes whose package has a type-17 room grid |
| +4 | first bit of this scene's rooms in the explored-rooms bitfield (`obj+0xAF2`) | `0x80055CEC`: bit = room + [+4]; within each group the ranges never overlap |
| +6 | map index within the group (likely) | equals the type-17 grid's first u16 in 922 of 923 scenes; no code reading it found |
| +8 | shared package, 3605 or 3606 | `0x8006B228`: from id `0x22D` on, it uses the byte-identical copy at +1124 (4728-4732) |

`vp_scenes.py` decrypts the header (the result matches the game's RAM copy word for word), follows
3610 + id to each package and finds its type-4 part. Every one of the 1,118 ids has a scene → 
`vp_scene_map.txt` / `vp_scene_map.json`: id, directory word, container offset, line count, place
names (from the caption built-ins, 92 scenes), exits, first line. Two scenes the old scan missed:

* **scene 0**: a Japanese **debug menu** (352-glyph font, "Face No", a list of 77 scene ids to jump
  to);
* **scene `0x19E`**: a treasure room ("Red Fire / has already been acquired"), the only scene whose
  font is SLZ type 1.

Exits are consistent: of the 2,104 scene-to-scene links (0x9E targets), 1,720 are matched by a link
back. For example, Platina's village (scene 1) leads into scenes `0x6`, `0x7` and `0x8`, and each of
them has a "To outside" exit back to 1.

## Package parts and the archive header

`func_8006B6C4` passes each part to a handler chosen through a 24-entry table at `0x8007DFB4`.
Types 1, 5-7, 9-11, 14, 15 and 19-21 go straight to return, so the field engine ignores them. A live
trace shows Valhalla's 14 parts dispatched in package order. The counts below are for all 1,118
scene packages:

| type | parts | contents | where it goes |
|---:|---:|---|---|
| 0 | 1,118 | layer / background table: 20-byte items, image-chunk table, animated records | `[0x8007F0F8]` |
| 2 | 2,952 | 8bpp TIM: background tile atlas (pieces, not a picture) | `LoadImage` to VRAM |
| 3 | 286 | 4bpp or 8bpp TIM with CLUT: sprites, effects (Valhalla: credits text, feathers) | `0x8006EB18`, VRAM |
| 4 | 1,118 | text container: script + text + font | this document |
| 8 | 1,709 | sprite animation tables (`u32 len, u16 id, u16 count, {offset, frames}`) | `0x8007EFD8`, actor code `0x80039A1C` |
| 12 | (shared only) | raw `RECT` + pixels: CLUT rows y=496-510 | `LoadImage` |
| 13 | 1,503 | sprite texture set: header, CLUTs, 4bpp pixels (each paired with a type 8 of the same id) | `0x8007F270` |
| 16 | 24 | polygon models (message window, actor models) | `0x8007FB6C` |
| 17 | 923 | automap room grid: `u16 index, 0, cellW, cellH, cols, rows, s16 room[]` | `[0x8007F118]` |
| 18 | 377 | scene MIPS overlay (61 distinct) | copied to `0x800899B0`, called at `0x8006D92C` (live) |
| 22 | 64 | movie-player module, byte-identical everywhere | `0x800BEA00` |
| 23 | 42 | sound bank: header + SPU-ADPCM | `0x8007F228` (8 slots) |

Confidence: the dispatch and destinations are from code and a live trace. Types 2, 3, 12, 17, 18, 22
and 23 are solid. The internal layouts of 0, 8, 13 and 16 are partly decoded.

**Archive header.** Every word from 1 on is a file entry: BCD position in bytes 0-2, and the high byte
of the size in sectors in byte 3. The size's low byte is in a second table at words `0x1400-0x18FF`,
encrypted bytewise. The 4,472 files lie back to back on the disc in word order, with empty words
between some of them. `docs/VALKYRIE_BIN_TABLE.md` explains the table in full. The first entries:

| word | file |
|---:|---|
| 1 | boot/title overlay (SLZ code) |
| 2 | "これはディスク１です" ("this is disc 1"), Shift-JIS |
| 4 | menu sound bank |
| 6 | 52-part menu package |
| 8 | 11,760 sectors of STR video (320×240) |
| 1490 | `overlay_engine_8002F824.bin`; 2292 = the field engine |
| 2294 | debug text (the stray text container) |
| `0x918`-`0xC3F` (the cues used) | voice clips (see below) |
| 3605-3608 | shared packages (copied at 4728-4732) |
| 3610-4727 | the 1,118 scene packages |
| 4733 / 4734 | map-screen overlay / its graphics and text |
| 4735-4765 | automap group packages |

### Voice

`docs/VOICE_CLIPS.md` explains the voice clips from the start.

Text command 12 and built-in **0xAB (`load_voice_clip`)** both call `0x8005A74C(cue)`. The cue is a header
word: a small file with a 0x48-byte header followed by raw SPU-ADPCM speech. It is read with
`Setloc`/`ReadN` into a heap buffer and played through the sound engine. There is no XA streaming:
live, the only CD commands were `Setloc 20:55:22` (cue `0x94F`), `ReadN`, `Pause`.
* `<12:cue>` at the end of a record preloads the clip for the next spoken record. The first clip of a
  sequence is loaded by 0xAB, mostly through library `func_0331(voice, face, wait)`, which also loads
  the speaker's portrait (0xA8) before nearly every spoken line. Four round-robin slots hold the
  queue, and the message box plays them in order at `<18>` (tested: see the command table).
* **154 of the 297 command-12 cues point at empty directory words** on this disc (scenes 0x18, 0x19,
  0x1D, 0x3EC …). *Checked later:* all 147 such cues have their file on disc 2
  (`docs/VOICE_CLIPS.md`).
* **Streaming.** The clip goes through a 256-byte double buffer at SPU address `0x70000`. Voice 0
  loops over it, and the IRQ address alternates between its halves (`0xE000` / `0xE010`). On each SPU
  IRQ, the driver refills the half just played by DMA and moves the IRQ address. TinyPSX didn't raise
  the SPU IRQ until D-057, so the stream stalled and the driver fell back to a time-out. Now clip
  `0x94F` streams in 133 refills and ends after 80 frames, its real length.

Sound-engine labels fixed from this trace:
* `0x8001A174` `note_on_activate_voice` → `sequence_stop`;
* `0x80019CF0` `voice_arbitrate` → `sequence_slot_arbitrate`;
* `0x80019834` `sequence_parse_events` → `sequence_start`;
* new: `0x8001A100` `sequence_is_active`.

## SLZ type 2: the game's decoder (EXE `0x80012874`)

`docs/SLZ_COMPRESSION.md` explains both SLZ types from the start, with worked examples.

Same flag/literal/back-reference scheme as type 1, but:

* **Length code 15 is a run-length fill, not a copy.** If the 12-bit offset is below 256, the next byte
  is the fill value and `offset + 19` bytes are filled. Otherwise the low 8 bits are the fill value and
  `(offset >> 8) + 3` is the count.
* **There's no end marker:** it stops after the uncompressed length from the header.

`bin_navigator.py`'s `lz77_decompress_multi` treats code 15 as a copy and offset 0 as end of stream.
So every type-2 entry with a fill comes out wrong from that point on. The "multi-stream" structure
looks like an artifact of this: `00 F0 xx`, a 19-byte fill, read as an end of stream. Anything decoded
with it (images, the "block" index) should be re-checked. `vp_scene_text.py` has the game's version.

## The scene block

`docs/SCENE_TEXT_FORMAT.md` explains the block, records, commands and fonts from the start, with a
worked example.

```
+0x00  u32 hdr[7]   (offsets count from +0x1C, the end of the header)
         hdr[0] = text index offset = size of the event script in bytes
         hdr[1] = 0
         hdr[2] = the scene's main function, a word index into the script (always opcode 0x18,
                  preceded by 0x17). It comes after the library (words 0..0xA50): first of the
                  scene's functions in 833 scenes, last in 66, in between in 219.
         hdr[3] = number of text records
         hdr[4] = total bytes of the records (all 1,118 scenes; not in the word-2294 file)
         hdr[5], hdr[6] = library entry points (word indexes; 0x1EC/0x230/… and 0x848)
+0x1C  event script: 32-bit words, opcode in the top byte. The first 0x2944 bytes (0xA51 words)
       are a shared library, identical in all 1,118 scenes. The scene's own code follows.
+0x1C+hdr[0]   text index: hdr[3] x u16, each an offset from the END of the index
...            records, each terminated by 0x00
```

Records 0-13 are the shared system strings (Artifact, Evaluation Level, World Map…).

### Records and commands

A record is glyph codes `0x01-0x7F` and commands, ended by `0x00` **in glyph position**. Arguments
can be `0x00`. A command is `b1 b2` with `cmd = ((b1 & 0x7F) | (b2 << 7)) - 0x4000`, then its argument
bytes. A 2-byte code whose value is below `0x4000` is a glyph number of 128 or more. Only scene 0's
352-glyph Japanese font needs those. The counts come from the renderer's dispatch handlers (table `0x8007DC6C`, indexed by command − 2, so commands 2-19). With them, all
19,023 records decode with 0 bad commands and no record running into the next. Every meaning
below was read from its handler and tested on an edited disc in TinyPSX, except command 2 (never used):

| cmd | args | meaning |
|---:|---:|---|
| 0 | 0 | line break |
| 1 | 0 | new page (wait for a button, clear the box) |
| 2 | 0 | never used; its handler (`0x8004C4E8`) sets box+`0x4E7` = 2 and stops the text pass |
| 3 | 1 | typing delay: N frames per character from here on; `0xFF` = the player's message-speed option. A counter at box+`0x4EC` must reach N before the next character (`0x8004C14C`), so `Lucian<3:28>,<3:ff>` pauses 40 frames before the comma |
| 4 | 1 | colour (`02` highlight … `00` normal: `<4:02>Caution!!<4:00>`) |
| 5 | 0 | **centre this line** in the box: `0x8004C530` adds (box width − the line's measured width) / 2 to its x. Used on place names and system messages. (Earlier read as "narration" from context; confirmed by an edited line in TinyPSX) |
| 6 | 2 | **queue a portrait** by number (u16): `0x8004C574` puts it in the box's 8-slot face table (box+`0x4A4`) unless it is already there. Used before speakers the script gives no face: `0x0D97` is the moustached man shared by "Man", "Max" and "Millia's father", `0x0D57` an elf. (Earlier read as an "NPC reference" from context) |
| 7 | 1 | horizontal scale (`<7:0b>-<7:01>` = a long dash) |
| 8 | 1 | vertical scale (Yumei's large "HELP!!": `<7:02><8:02>…<7:01><8:01>`) |
| 9-11, 13, 15, 16 | 0 | no-ops (handler returns to the loop) |
| 12 | 2 | **voice clip**: preload cue (u16, little-endian) for the *next* spoken record; `0x8005A74C` (see Voice below) |
| 14 | 1 | insert a value (`00` = EXP amount) |
| 17 | 2 | auto-advance after N frames (u16; `0x78` = 2 s). Read as 1 byte until the second pass: the high byte `00` then ended 72 records early, losing 212 lines of text |
| 18 | 0 | **start the next queued voice clip**: `0x8004C8F4` checks the round-robin voice slot (`0x8007FD86`, 4 slots at `0x8007F208`); a loaded clip is flagged to play, and if one is still pending the text pointer steps back so the text waits on this command. Where it sits sets when the voice starts (see the tests below) |
| 19 | 2 | **show the queued portrait** (`0x8004C638`): moves the newest queued face to slot 0, which the box then loads and draws. `0100` = bottom left facing right, `0000` = bottom right, mirrored; `ffff` = the side in global `0x8007F260` bit 27, which built-in 0xE0 sets from the box's x anchor: the box's own side (tested below). With no `<19>` there is no portrait, even with one queued. (Earlier read as "ffff = no portrait") |

**Tests of 6, 18 and 19** (2026-11-12, TinyPSX, edited copies of scene 0x1B, Frei's welcome in Valhalla,
the `left.replay` walk). Record 14 is `<19:0100>Frei` / `「<18>Welcome to Valhalla!<12:5109>`; record 15
is Valkyrie's reply, with no portrait.

| edit | result |
|---|---|
| original | Frei's portrait bottom left; her clip plays frames 14540-14640, Valkyrie's 14890-15040 |
| record 14 `<19:0000>` | Frei's portrait bottom right, mirrored |
| record 14 `<19:ffff>` | bottom left, as `0100` |
| record 14 without `<19>` | no portrait |
| record 15 `<6:970d><19:0000>` | a moustached man's portrait, bottom right, on Valkyrie's line |
| record 15 `<6:570d><19:0100>` | an elf's portrait, bottom left |
| record 15 `<19:0100>` with no `<6>` (after record 14 without `<19>`) | no portrait |
| record 14 without `<18>` | Frei's line is silent; at Valkyrie's `<18>` a 100-frame clip plays (Frei's, by its length), and Valkyrie's 150-frame clip never plays |
| record 14 `<18>` moved before "Valhalla!" | the clip starts 45 frames later (14585): "Welcome to " is 11 characters at `<3:04>`, 44 frames |

**Test of built-in 0xE0** (same scene and replay). 0xE0 sets bit 27 of `0x8007F260`, and `<19:ffff>`
copies that bit into the box; `<19:0000>`/`<19:0100>` ignore it. That is why the earlier live test,
which changed 0xE0 on Frei's `<19:0100>` line, showed nothing. Library `func_033C` calls 0xE0 from the
box's x anchor (table under "From script push to screen"). Anchors 0 and 2 both put the box at x = 24,
but 0 calls `e0 1` and 2 calls `e0 0`, so changing the pushed anchor isolates 0xE0:

| record 14 | box | portrait |
|---|---|---|
| `<19:ffff>`, anchor 0 (`e0 1`) | x = 24 | bottom left |
| `<19:ffff>`, anchor 2 (`e0 0`) | x = 24, pixel-identical to the line above | bottom right, mirrored |
| `<19:0100>`, anchor 2 (`e0 0`) | x = 24 | bottom left: the whole frame is identical to the first row |
| `<19:ffff>`, anchor 1 (`e0 0`) | right (296 − w) | bottom right, mirrored |

So `<19:ffff>` puts the portrait on the same side as the box, through 0xE0; an explicit `<19>` value
overrides it. Screenshots: `docs/notes/edit_proof/builtin_e0_portrait_side.png`.

Voice was timed by counting SPU interrupts per 5 frames in the debugger (`catch interrupt:spu`): the
streamed clip raises one about every 3 frames while it plays, and none otherwise.

### Fonts: why capitals "change"

Each scene ships its own font as entry 1 of the text container:

* `u32 n` (glyphs, 96-124);
* `u32 12` (rows);
* `n` width bytes;
* `n` × 12 × u16 row bitmaps (MSB = leftmost pixel).

The game builds the font from the glyphs the scene uses, in order of first use, so **the same code
can be a different character in another scene**. *Corrected later:* codes 1-96 never change, because
records 0-13 (the system messages, the same in every scene) come first and use 96 glyphs. Only codes
from 97 up vary; see `docs/FONTS_AND_GLYPHS.md`.

Across the 1,118 English scene fonts there are only **158 distinct glyph bitmaps**:

* letters and punctuation;
* two digit sets;
* the button symbols ○ ✕ △ □;
* arrows, ♪, ▼;
* `、` (scene 97, used as a pause: "Now、I know") and `%` (scene 125, the battle tutorial: "100%").
  Ten more kana and kanji (ド の 家 南 楼 飯 店 ボ 左 右) occur only in debug text: scene 0 and the
  file at directory word 2294.

`vp_glyphs.json` maps each bitmap (hex of its 24 bytes) to its character. Mapping by bitmap removes any
need for per-scene codepages. Check: 88% of the 131,955 decoded words are in the system dictionary, and
the rest are names (Lezard, Arngrim, Mystina…) and inflections.

## The event script VM

`docs/EVENT_SCRIPT.md` explains the script from the start, with a worked example.

**Interpreter:** field engine `func_800714DC`, loop at `0x800715B4`.
* While the script context's wait counter (`ctx+0x310`) is 0: `ins = *ip++` (`ip` = `ctx+0xF0`),
  then call `table[ins >> 24](ins)`, with the table at `0x800831D0` (256 entries, 188 distinct
  handlers).
* A handler that has to wait sets the counter or rewinds `ip` by one word to retry next frame. The
  script context pointer is at `0x8007F1EC`.

**Registers:**
* data stack pointer `ctx+0xFC` (grows down);
* call stack `ctx+0x100`;
* frame `ctx+0xF4` (word locals) and `ctx+0xF8` (bit locals);
* code base `[0x8007F1CC]`;
* globals `[0x8007F1B0]` (word variables; `G[0]` is the **accumulator**);
* flags `[0x8007F1B4]`.

**Instruction:** opcode in bits 31-24, operand in bits 23-0.
* For variables (decoder `0x80071624`): bit 23 = local, otherwise global; bits 17-16 = size (word,
  byte, bit flag); bits 15-0 = offset (a byte offset, or a bit number for flags); offset 0 with a
  size set = indirect through `G[0]`.
* For code: operand = word index from the code base.

**Core opcodes** (handlers read one by one; lengths confirmed by a live trace of the VM):

| op | words | meaning | op | words | meaning |
|---|---:|---|---|---:|---|
| 01 02 08 | 1 | pop → var | 12 | 1 | push 23-bit immediate |
| 03 04 / 05 06 | 1 | pop → halfword / byte var | 13 | 2 | push next word |
| 07 | 1 | pop → bit flag | 14 | 1 | call (saves ip and frame) |
| 09 | 1 | acc = imm × pop | 15 | 1 | jump |
| 0A | 1 | acc += imm | 16 | 1 | pop; jump if zero |
| 0B 0F / 0C / 0D / 0E | 1 | push var (word / half / byte / bit) | 17 | 1 | return (empty call stack: end of turn) |
| 10 | 2 | acc = imm + word | 18 | 2 | function entry; next word = local size |
| 11 | 1 | acc ×= imm | 19 / 1C | 2 | push (var == word) / (byte var == word) |
| 1A | 2 | var = word | 1B 1D 1E 1F | 1 | no-op |
| 21 / 22 | 2 / 1 | var = word / pop | 49 / 4A | 1 | var++ / var-- |

The binary operations 0x23-0x48 come in pairs: odd = `acc = var OP next-word` (2 words), even =
`acc = var OP pop` (1 word). In order: add, sub, mul, div, and, or, xor, (0x31/0x32 unused), shl,
shr, logical and, logical or, (0x3B/0x3C flag moves), ne, eq, gt, lt, ge, le.

**Built-ins** (0x50 and up): bit 20 set means the arguments are on the stack; otherwise N inline
words follow. N for each built-in is in `BUILTIN_ARGS` in `vp_script.py`. It was learned from the
1,034 distinct scripts and corrected from the handlers where they disagreed (0x78, 0xE2 take 0;
0xA4 takes 4; 0xA8, 0xBD take 1). With it, 293,630 call and jump targets all land on instructions,
every call lands on a 0x18, and no instruction is followed by an unexecutable word. The one
exception is a "target" inside a byte table.

The dispatch table has **0xEE entries**: opcodes 0xEE-0xFF read past its end, into data, so they
aren't instructions. The handlers for the movie built-ins 0xC4-0xC8 and 0xCA aren't in the field
engine: they're in a movie-player module shipped as scene-package part type 22 and loaded at
`0x800BEA00` in the scenes that use it.

**Every built-in is named** in `vp_builtins.json`, with confidence, argument layout and the evidence.
They were named from per-built-in dossiers: handler code with callee names, usage statistics, and
call sites with the dialogue around them. In total: 144 entries (124 real built-ins, 2 unimplemented, 18 invalid opcodes 0xEE-0xFF): 83 high, 54 medium, 7 low confidence, after a second pass that traced the uncertain ones live. I
re-checked a sample against the code myself: 0x7E, 0x95, 0xB8, the argument counts above, and the
movie handlers. The families:

| family | built-ins |
|---|---|
| text | 92 message, 93 choice, 94/98 message (box styles 3/4), CB/97 caption, CC treasure; 7E wait_message_dismissed, 91 close_message_box, 9D wait_choice (choice index in the accumulator), BE set_msgbox_option (fade time, frame, page sound), E0 set_portrait_default_side, A8 msgbox_load_portrait, AB load_voice_clip |
| actor command queue (one handler, `0x80076964`; per actor a 16-slot queue run by `0x8003A4FC`) | 70 wait_anim_end, 71 set_anim, 72 set_facing, 73 walk_by, 74 wait_move_end, 75 wait_frames, 89 despawn, 8A walk_to, A1 set_prop, A3 fade_tint, B0 wait_fade_done, B3 jump, B4 call_script, C0 scale_to, C1 wait_scale_done, DA-DD sfx and prop ops; 76 waits for an actor's queue to empty |
| actors and map | 85 spawn_actor, AD spawn_actor_simple, B5 clear queue, B6 despawn, A2 get_prop, E1-E3/EA active-actor set, 80/BC/B7 trigger zones, 88 spawn_effect, 90/8B map sprites, C2 map animation |
| camera and screen | 82 set_camera_pos, 83 camera_pan_to, 99 wait_pan, 7F camera_follow_actor, D0 set_camera_limits, 9C/7D zoom, 87 fade_screen, 7C wait_screen_transition, 96 screen_shake, A0/A4/7B palette and tint fades, CD set_light |
| sound | BB play_bgm (loads), 8D bgm_start_loaded, A9 bgm_is_loading, 8E bgm fade out, BA channel levels, 8F/E7/DE/DA play sfx, CF/E9/DC stop sfx, D3/D4/D6/D7 sound sequences, D9 ambient source, CE footstep sfx |
| flow and data | 8C wait_frames, 81 set_script_mode (brackets cutscenes / talk), BF call_script_next_frame, 95 random, B8/B9 sin/cos, 9E select_scene / 9F change_scene, 86 start_battle (78 clears its per-monster counts), C9 engine_set_param (sub 3 = scene graphics package, 0/1 battle music, 9 battle start mode), E5 has_item, E6 add_item; B2/E1-E4/EA work on the 25-record character roster at `[0x8007F1E4]` |
| event images | A5 load_event_image (full-screen story illustrations), A6 fade_event_image, 79 wait_event_image_ready |
| movies | C4 open, C5 play, C6 stop, C7 wait_end, C8 status, CA frame |

**How a line is reached:** the scene code pushes the arguments and calls a library wrapper:
`push 0x78, 0x30, 0x80, 0x1C, 0x0E, 0x28; call 0x3E2`: x 120, y 48, a 128 × 28 box, record 14 and a
40-frame wait (see "The message box"; `docs/EVENT_SCRIPT.md` walks through this example). The wrapper pops them into locals (`0x01`) and runs `0x92` in stack form.
`vp_script.py` follows argument values through wrappers (summarising which argument reaches a record)
and resolves each record at the instruction where it is a literal.

**Result: 3,359 of the 3,363 scene lines (99.9%) are tied to the instruction that shows them**,
in script order (`vp_scene_lines.txt`).

## Play order

`vp_flow.py` rebuilds each scene's control flow and prints its lines in the order they can play,
nested in the conditions that decide them (`vp_scene_flow.txt`, all 1,118 scenes):

* **Structure.** The scene code is compiled from structured source, so it nests. A forward
  `jump if !pop L` is an `if`; when the word before `L` is a forward `jump M`, the range up to `M` is
  its `else`, so chains become `elif`. Two `jump if !pop` to the same label are `a and b`. The
  compiler threads jumps to jumps, which the tool follows. So `a; jump if !pop A; jump T; A: b; …` with
  every `jump T` chained to the body is `a or b or …`, and a case that jumps straight to the end of an
  enclosing chain still nests. A backward jump closes a loop, and a `jump` to the loop's exit is a
  `break`. What still doesn't nest is printed as `goto`: 79 jumps, 23 of them among lines (11 in the
  debug menu). Most are a case whose "no match" path jumps straight into the next outer test.
* **Conditions** are rebuilt from the pushes and accumulator operations before each jump:
  `story == 0x18 and step == 0xb`, `not G0x46c.bit`, `G0xc & 0x1000`. A value a built-in
  leaves in the accumulator is shown as the call: `actor_get_prop(0, 1) <= 0x220`.
* **Choices.** `0x9D wait_choice` leaves the chosen index in the accumulator, and scripts store it in
  a local. The tool follows it there, so `L0 == 0` prints as `choice 0 «Yes»`, with the option text
  taken from the choice record.
* **Calls** into the scene's own functions are expanded in place. They're marked `[func_XXXX]` and not
  indented, since a call runs in sequence (story events are chains of up to a dozen calls). Each
  function is expanded once per entry point; later calls print `(as above)`. A call into the library
  shows the line it displays. 3,356 of the 3,359 tied lines appear in the flow.
* **Entry points.** The scene's main function comes first, then every function the code never calls
  but a built-in registers: a trigger zone (`0x80`), an actor (`0x85`), an actor script (`0xB4`).
  Those run when the player walks or talks there. Most of a town's lines are in them.

Valhalla (`--scene 0x16`) shows why code order misleads. 46 of its 47 lines (#15-#60, Frei, Lucian
and Loki, then scene 0x1D) play only when `story == 0x18` and `step` is between `0x0B` and `0x13`:
a late storyline (see Story variables). On New Game only the last branch runs: #14 "How nostalgic", then scene 0x15.

**Checked in the running game** (New Game, Cross every 45 frames, breakpoints on the message setup
`0x800532C4` and the scene switch `0x8006C270`):
* record 14 at frame 7632, then the scene ids 0x15, 0x17, 0x16, 0x17, 0x15, 0x1B;
* every one of those switches is a `→ next scene` the flow predicts;
* the second visit to 0x16 takes the other branch (`if G0xe9: → 0x17`) and shows no line, as
  predicted;
* scene 0x1B's lines are all in trigger-zone functions, and with no movement input none were shown;
* holding RIGHT reaches the zone at x 620-638, whose function has one line: record 21 ("I will go on
  ahead."), shown again each time the zone triggers;
* holding LEFT reaches Frei's zone: records 14, 15, 16, 17, 18, 19 in the predicted order, until the run
  ended at frame 17,000 (before #20 and the switch to 0x1C).

## Story variables and event order

From `story/` (scripts, `story/story_vars.md`, `story/event_order.txt`, `story/flags.json`;
`sh run_all.sh` rebuilds them in about a minute). Static analysis over all scenes plus live traces:

* **Access size comes from the opcode**, not the operand bits. `0x1C` compares bytes (handler
  `0x80073FD4`: `lbu` on both sides), `0x19` words, `0x0E` pushes a flag bit. Only the binary
  operations `0x21`-`0x4A` read the size bits. So the Valhalla test is `G0xD1.b`, and `G0xE9` is flag
  bit 0xE9. `vp_flow.py` now sizes operands this way.
* **`story` = `G0xD0.b` is the storyline number (0-30) and `step` = `G0xD1.b` the step within it**
  (1, 2, … then steps of 10 up to `0x7E`; `0xFF` = finished). They are not chapter and period.
  Scene scripts are a state machine over them. A block `if story == a and step == b` plays its lines,
  sets `step` to the next value and switches to the scene holding that next block. Following those
  writes rebuilds 28 storyline chains (`story/event_order.txt`: per storyline, its steps, scenes,
  cast and first lines).
  * **Cross-check:** the debug menu's chapter-select list jumps to exactly such (story, step, scene)
    triples, e.g. (0, `0x64`, scene `0x13`) and (13, `0x14`, scene `0xB`).
  * **Only scripts write them:** scans of the field engine, every uncompressed code block and every
    decompressed SLZ block found no native write to D0/D1. A New Game trace saw no write before
    scene 0x1C.
  * **Start state:** scene 0x1C, after Odin's briefing, sets `story = 0, step = 100` and goes to scene
    0x13 ("This is... The human world...?"), which ends the prologue (`step = 0xFF`).
* **`chapter` = `G0x68.b`** (solid). Scene `0x7D`'s tutorial explains "Chapters and Periods" and sets
  `chapter = 1`, and the Sprite's "Necessary Hero Value" answers change with it, 40 → 120 over 8
  chapters. Native code at `0x80060168` reads it to award `100 + 500 × chapter` event EXP into
  `G0x20`. **`period` = `G0x69.b`** (likely).
* **`G0x8`-`G0x1C` are controller words** copied from `0x8007E620`… every VM tick. So the very common
  `G0xc & 0x4000` / `& 0x1000` tests are D-pad directions in trigger zones (probably Down / Up).
* **Flags** (`story/flags.json`): 403 flag bits used by scene code, each with the scenes that set and
  test it and the nearest line. 174 are local to one scene, 53 cross scenes, 110 are set by the engine,
  63 only by the debug menu. 3 names are high confidence (`valhalla_intro_done` = `0xE8`), 73
  medium (mostly treasure flags), the rest placeholders.
* **Event order.** Inside a storyline the order is fixed by the `step` writes (solid). Between
  storylines it's inferred as ascending `story`: that matches the debug list, and the only two
  cross-storyline flag tests agree. Player freedom is the hubs where several scenes accept the same
  step, plus everything gated by local flags.

## Editing the dialogue: `vp_text_editor.py`

The editor puts the whole chain above to work in reverse, and the game accepts the result. It reads
the original `vp.cue`/`vp.bin`, which it never writes. It writes a new cue/bin pair with the
edited scenes re-packed.

```
python3 vp_text_editor.py export ~/Documents/vp.cue --scene 0x16 -o scene_016.txt   # edit this file
python3 vp_text_editor.py build  ~/Documents/vp.cue scene_016.txt -o vp_edit.cue [--fit-boxes]
python3 vp_text_editor.py find   ~/Documents/vp.cue "How nostalgic"
python3 vp_text_editor.py check  ~/Documents/vp.cue [--scene ID] [--compress]
```

**GUI.** `python3 vp_text_gui.py [DISC.cue]` serves the editor on http://127.0.0.1:8766/.
* **Browse:** the scene list, a search over all dialogue, and each scene's records.
* **Edit:** a text editor with command buttons, and a glyph palette. Glyphs not in the scene's font
  are marked "added on build".
* **Codes legend:** under the text, every code in the current line is explained with its actual
  value ("<17:7800>: closes by itself after 120 frames"), and an "All codes" reference is a click
  away. Clicking a code inserts it.
* **Preview:** what the game would draw (see The message box below), with the box's position, size
  and call. It plays the typing and scrolling, and flags wraps, scrolls and added glyphs.
* **Save:** edits are saved as you type, as edit files in `vp-analysis/edits/`. These are the same
  files `build` reads.
* **Build:** "Build disc" writes the cue/bin, optionally with `--fit-boxes`. "Run in TinyPSX" opens
  the built disc in the emulator window.

**Edit file.**
* `@@ N` starts record N, and the text below it is the record.
* A line break is command 0 and `<page>` is command 1.
* `<N>` / `<N:hex>` is any other command with its argument bytes, e.g. `<17:7800>`, `<12:5109>`.
* `{xx}` is a raw glyph code.
* Records left out of the file, or left unchanged, keep their original bytes.

Inside an edited record, runs of characters the edit didn't touch also keep their bytes. That
matters because 3,971 records use the second set of capitals or digits; a character typed in new
gets the code the scene uses most for it.

**What `build` does, step by step:**
1. **Find the archive.** It reads `VALKYRIE.BIN` through the ISO 9660 root directory (LBA 150), then
   decrypts the archive header.
2. **Find the scene.** It looks up directory word 3610 + scene id, which gives the package.
3. **Unpack.** From the package's part type 4 it takes the two SLZ entries: the scene block and the
   font.
4. **Encode.** It encodes the edited records. A character missing from the scene's font is added to
   the font: the bitmap and width come from `vp_glyph_db.json`, built once from all 1,118 fonts.
5. **Rebuild the block.** It recomputes the text index and `hdr[4]`.
6. **Re-compress.** `vp_slz.py` re-compresses with an optimal parse. Type 1 ends with the
   offset-0 marker.
7. **Re-pack.** It rebuilds the container (steps aligned to 4) and the package (part sizes shift).
8. **Fit check.** The package must fit the sectors between its directory word and the next one,
   because that's all the loader reads.
9. **Write.** Only the changed sectors are written into the copy, and their EDC and ECC are
   recomputed (`vp_disc.py`).

**Checked on the whole disc** (`check --compress`):
* all 1,118 scene blocks rebuild byte for byte from their records;
* all 19,015 records render to edit text and parse back to the same tokens;
* a fresh encode of every record decodes to the same text;
* every container re-compresses, decodes back exactly, and still fits its package.

`vp_slz.py` compresses better than the game's own tool. Valhalla's block comes to 6,924 bytes
against the original's 7,279, and its font to 818 against 844. Every package gets at least
248 bytes smaller.

EDC/ECC recomputed from scratch match the disc's own on sampled sectors. The edited image differs
from the original only in the sectors of the edited packages: 20 for the test below.

**Live test in TinyPSX.** I made two edit files: scene `0x16` record 14 and scene `0x1B` records 14-15.
Then I ran the edited image with `dlg/map/left.replay`, which mashes Cross and walks left in the
hall.
* Valhalla: `How nostalgic・・・ (edited♪)` shows. `(`, `♪` and `)` were not in the scene's font, so
  the editor added them.
* Frei: `Welcome back to Valhalla! / This line was edited.` is a new third line.
* Valkyrie: `Quite the welcome, Frei.` is shorter than the original, and its `Q` was added to the
  font.

Both runs went on normally through every frame checked, up to 15,600. With `--fit-boxes`, Frei's
edited box is up at frame 14,750, against 14,760 for the original line. Without it, the wrap and
scroll hold the conversation back by about 400 frames. Screenshots and the two edit files are in
`docs/notes/edit_proof/`.

**What the edits taught (new facts):**
* **A font's glyph count must be even.** The bitmaps start at `8 + n`, and the renderer reads each
  row with `lhu` (`0x80052500`, testing bit 15 for the leftmost pixel). The first build added 3
  glyphs to a 108-glyph font. The game stopped on an address error (AdEL, BadVAddr `0x800F7FBF`)
  the moment the line appeared. All 1,118 fonts on the disc have an even count; the editor pads
  with a blank glyph.
* **The box size comes from the script, not the text.** The message wrappers `0x3E2`, `0x33C`,
  `0x3F5` and `0x408` take six pushed arguments: x, y, width, height, record, and (for `0x3E2`) the frames to wait after the box closes. Record 14 of
  Valhalla is `push 0x78, 0x30, 0x80, 0x1C, 0x0E, 0x28; call func_03e2`: a 128 x 28 box at
  (120, 48), which matches the screenshot. Over the 3,417 literal calls on the disc:
  * the width is at least the widest line, measured with the font's width table, in 99%;
  * the height is exactly 14 pixels per line of the fullest page in 91%.
* **Text that outgrows its box wraps and scrolls.** A line wider than the box wraps at the box
  edge, and more lines than the box holds scroll it up a line at a time. The game does this on
  purpose in the other 9% of calls. Without `--fit-boxes`, the Valhalla edit shows
  `How nostalgic・・・` and the rest only after a wrap, and Frei's box scrolls. With `--fit-boxes`,
  the editor changes the width/height pushes (here 128 x 28 → 157 x 28 and 128 x 28 → 147 x 42),
  and the lines show the way the originals did.
* The re-packed packages loaded through the game's own path: the directory, the `Setloc`/`ReadN`
  of the directory's sector span, both SLZ decoders and the font. That confirms the package and
  container layouts, the SLZ formats and the text encoding as written up above.

## Editing menu and other non-dialogue text: `vp_menu_text.py`

`docs/MENU_AND_BATTLE_TEXT.md` explains the menu and battle tables from the start.

Everything the player reads in English that is not scene dialogue is in **13 string tables**, one per
menu module. A scan of every non-scene file in VALKYRIE.BIN for this table layout found exactly
these. The other hit, word 2294, is the debug text.

| directory word | what | messages |
|---:|---|---:|
| 6 | title screen and New Game menu (part 46 of the 52-part title package; also song titles, character names) | 305 |
| 2175 | camp menu, top level (menu items, LV / DME / EXP labels, Play time, Materialize point, Eval., character names) | 64 |
| 2176-2183 | camp sub-menus: Skill, Transfer, Ability, Use Item, Divine Item, Status, Party, Equipment (item, skill and equipment names, descriptions, help lines) | 232-2,276 each |
| 2184 | Configure | 40 |
| 2185 | Data (save / load messages) | 87 |
| 4734 | world map (controls, place names) | 41 |

**Packages.** Words 2175-2185 are 3-part packages: a type-18 code overlay (the sub-menu's code),
the type-4 text and a type-7 font. Word 6 is the title package; 4734 is the world map's graphics
and text, where the font is the text part's second SLZ entry.

**Table format.** The type-4 part is one SLZ entry (SLZ1 or SLZ2) that decompresses to:
`u32 text_offset (= 4 + 8n)`, then n `(u32 message id, u32 offset from text_offset)` pairs, then
zero-terminated strings.
* **Ids:** message ids are per module. 0x64 is "New Game" in the title table and a place name on the
  world map. The camp sub-menus share most ids: 2,412 ids are in more than one table, and an item
  name has the same id everywhere it is shown.
* **Order:** two tables list "[" / "]" (0x1A8E / 0x1A8F) twice, and their ids are not sorted, so the
  table order is kept as it is.
* **Layout:** no two entries share bytes, and none points into the middle of a string.

**Encoding** (one glyph per byte, the package's own menu font):
* `0x01-0x5F`: ASCII 0x20-0x7E, the character minus 0x1F ("Skill" = `34 4C 4A 4D 4D`). The old
  "Scheme A" rule in `codepage.py` (−1 above 0x40) read lower case as upper case.
* `0x80`: new line.
* `0x81 0x80 nn`: colour nn for what follows. Seen on screen: 07 the blue labels (LV, DME,
  Play time), 02 red ("Empty"), 01 orange ("Eval."), 03 black. 478 uses. *Corrected later:* 0x81
  appears 568 times, but 85 of those are `81 01` at the end of skill help lines in table 2179, not a
  colour (`docs/MENU_AND_BATTLE_TEXT.md`).
* `0x60-0x7F`, `0x82-0x85`: extra glyphs (icons, rules, the strips in the skill help). The fonts
  have 100 glyphs (112 on the title screen), so what these show differs between modules.

**Font.** The type-7 part has the scene-font layout: `u32 n, u32 12, n width bytes, n 24-byte
bitmaps`, with glyph b at index b − 1.

**Loading.** The game decompresses a table into a heap block of its decompressed size. In a
camp-menu RAM dump, the 2175 table is at 0x80102BC4 in a block of 1,256 + 16 bytes. So a longer
table just gets a longer block. The limit is the package's sectors on the disc:

* After re-compressing with `vp_slz`, there is room for 92 bytes (Configure) to 2.6 KB (Divine Item)
  of compressed text.
* Text compresses about 2:1.
* `build` re-compresses the code overlay and font too if the text alone does not fit.

**The editor.**
```
python3 vp_text_editor.py menus  ~/Documents/vp.cue                      # the 13 tables
python3 vp_text_editor.py export ~/Documents/vp.cue --menu 2175 -o menu_2175.txt
python3 vp_text_editor.py build  ~/Documents/vp.cue menu_2175.txt [scene files...] -o vp_edit.cue [--no-link]
python3 vp_text_editor.py check  ~/Documents/vp.cue --menus [--compress]
```
* **Edit files:** `@@ 0x0002` is a message id, then its text. A line break is 0x80, and `{81 80 07}`
  (raw hex bytes) is a command or extra glyph.
* **Linked copies:** `build` also changes every other table that holds the same id with the same
  original text, unless `--no-link`.
* **Rebuilding:** an unchanged string keeps its bytes and offset. A changed one is written over the
  old one if it fits there, or appended after the last string.
* **Width warnings:** `build` warns when an edit makes a string wider, measured with the font's
  widths.
* **Search:** `find` searches the menus too.
* **GUI:** the "Menus" tab lists the tables and messages, shows the copies an edit will change, and
  draws the disc and edited strings with the module's font against a marker at the original width.
* **`check --menus`:** all 10,674 strings round-trip and every table rebuilds byte for byte. Every
  package fits after re-compressing.

**Tested in TinyPSX** (`docs/notes/edit_proof/menu_text_edit.png`):
* **The edit:** one edit file for table 2175 changed 10 tables through linked copies.
* **What showed on screen:**
  * Skill → Skills; Use Item → Use an Item;
  * Play time → Time played; Materialize point → Materialize pts;
  * Empty → (free slot), with colour 03;
  * Eval. → Evaluation; Valkyrie → Lenneth.
* **The Skill sub-menu** loaded its rebuilt table and showed the linked "Skills" title and
  "Lenneth".
* **Fixed positions:** menu text is drawn at fixed places. "Use an Item" touches "Divine Item", and
  "Time played" and "Evaluation" overlap their values. That is what the width warning is for.

The system strings inside scene text (the Artifact prompt, item messages) are dialogue records, and
`export --scene` edits them per scene.

### Battle text

**How it was found.** A battle run with `--trace-cdrom` (the scene 0x1B fight, 2026-11-12) shows
which files the game reads:
* **At `start_battle`:** 2258, 1023, 1436, 1488, 1487, 1491, **1490** (the battle engine, SLZ1
  code at 0x8002F824), 621, **1500**, 1492, **1638** (battle code, raw MIPS).
* **During the fight:** **1806**, read repeatedly.
* **After it:** 1807, 2245, 1989 ("Levelup"), 2098, 1992, 3605, 3846.

These packages use a second layout: `u32 count, u32 0`, then count `(u32 flags, u32 size)` pairs,
then the parts.

A scan of every non-scene file for the string-table layout, whatever the encoding, found the
battle text in four tables. Each sits next to its own font:

| file | text part (flags) | font part | entries | contents |
|---|---|---|---:|---|
| 1500 (identical copy in 1501) | 7 (`0x300900`) | 6, 84 glyphs | 333 | battle menu (Menu, End Turn, Use Item, Equip, Magic, Reorder, Flee, a leftover "Debug function"), elements, skill names, attack names |
| 1500 (and 1501) | 9 (`0x300910`) | 8, 56 glyphs | 321 | character names and monster names (Stealer Robin, Beetle Giant, Ant Queen, ...) |
| 1806 | 1 (`0x300920`) | 0, 112 glyphs | 1,080 | item, equipment and spell names and descriptions (ids 2001-4023) |
| 1988 | 2 (`0x300920`) | 1, 68 glyphs | 114 | spoken battle dialogue: Lenneth's tactical hints ("Dragon Servants! They're masters of defense ... Guard Crush") and boss lines ("Why do you hate me so?" / "For Lucian!") |

**Format.** The tables use the menu layout (`u32 text_offset`, (id, offset) pairs, zero-terminated
strings, 0x80 = new line). The difference is the text bytes: **each byte is a glyph number in that
file's own font**, numbered in order of first use the way the scene fonts are (1806's font starts
E, l, i, x, r from "Elixir"). The fonts use the menu-font layout and the same typeface. So a glyph
is matched to its character by comparing its bitmap with the menu font: every glyph of 1500/8
matches, and so do all but 16 of 1500/6, 38 of 1806/0 and 3 of 1988/1. The rest are Japanese
placeholders (空欄 blank, 予約 reserved, 未定 undecided) and a few symbols. Four of the symbols are
battle-only glyphs that the editor writes as characters: the ellipsis … (1500/6, 1988/1), the corner
quote 「 that starts each hint line (1988/1), the middle dot · ("ATK 100·ATK# 3"; 1806/0, 1988/1) and
the curly apostrophe ’ (1806/0). The remaining ones are kept as raw codes. The fonts end with blank,
zero-width glyphs that no string uses; they only make the glyph count even. This encoding is why
early work called the 1806 table "binary data, not readable text".

**Live checks.**
* **In RAM mid-battle** (frames 200 and 400 of the fight), both 1500 fonts and tables are at
  0x8010DEEC-0x80111FFF, and the 1806 item table is at 0x801197DC.
* **1806 is loaded by `0x80059E14`** (callback 0x80059D50) and **1988 by its sibling
  `0x80059EE8`**. Both call the file loader `0x8004CF80` and are started by battle tasks
  (task state at object +6; the 1988 caller is at 0x8006B808).
* **Heap blocks:** each part is decompressed into its own heap block sized by its length (the 1500/7
  table at 0x8010E738 is in a block of 0x1A28 = 6,677 + 16 bytes, rounded up). So longer tables and
  fonts are fine for the heap; mid-battle there is a 720 KB free block.
* **On screen:** in the same fight the battle menu opens with SELECT (START does nothing). It shows
  the character's name from 1500/9 over the DME gauge and the selected command's label from 1500/7
  in a box at the bottom right. The 1806 and 1988 text was not seen: the item list did not open in
  this fight (the new-game party may have no items), and the hints only come with their monsters.

**Other battle text.**
* **The 1638 strings:** it holds plain ASCII ("HP %7d/%7d", "Weakness", "Exp. Pts.", "Item Name",
  "Weapon Name", skill names, "ROTX=%d", "MAT NO=%d"). This looks like a developer debug display
  printed with the system font.
* **Banners:** "Purify Completion" and the DME / Hits gauges look like images. No text table holds
  them.

The decoded text of all four tables is in `vp_battle_text.txt`.

**Editing: `vp_battle_text.py`.** The four tables are edited the same way as the menu tables, keyed
`1500.7`, `1500.9`, `1806.1` and `1988.2`:
```
python3 vp_text_editor.py export ~/Documents/vp.cue --battle 1806.1 -o battle_1806.1.txt
python3 vp_text_editor.py build  ~/Documents/vp.cue battle_1806.1.txt [menu and scene files...] -o vp_edit.cue
```
* **Encoding:** an edit is written with the table's own glyph numbers. A character the font lacks is
  added from the menu font (same bitmap and width), over the blank padding glyphs, and the font is
  padded back to an even count. Glyph numbers stop at 0x7F (0x80 is the new line), so a font holds
  at most 127 glyphs. 1806's has 112, which leaves room for 15 more characters.
* **The 1501 copy:** an edit to 1500.7 or 1500.9 is written to both 1500 and 1501.
* **Linked copies:** 1806 uses the camp menu's item ids, and 662 of its strings are also in the
  camp-menu tables with the same id and text. Linking compares the decoded text, so a battle item
  rename changes the camp menus and a camp-menu rename changes the battle table (unless
  `--no-link`). An edit that has raw codes `{..}` is not linked between a menu and a battle table,
  because a raw code means a different glyph in each; `build` says so.
* **Room:** after re-compressing, every battle file still fits with room to spare: 1500 has 1.7 KB,
  1501 2.1 KB, 1806 1.6 KB and 1988 0.7 KB. `build` re-compresses the file's other parts only if the
  edit does not fit otherwise.
* **`check --menus`** also round-trips all 1,848 battle strings and rebuilds every table and font
  byte for byte.
* **GUI:** the "Menus & battle" tab lists the four tables under "Battle text". The preview draws
  with the table's own font, including the glyphs an edit adds, and the page names them.

**Tested in TinyPSX** (`docs/notes/edit_proof/battle_text_edit.png`). One build changed all four tables
(fonts 84 → 86, 56 → 58, 112 → 114 and 68 → 68 glyphs). In the scene 0x1B fight the battle menu
showed "Valkyrie X1" (1500.9, with the added X and 1), and the command labels "End Turn 9",
"Quaff Item" and "Run away!" (1500.7, with the added 9 and Q). The labels are centred in their box,
and all of these fit. The 1806 and 1988 edits were checked by reading the built disc back, not on
screen.

## World-map text: `vp_worldmap_text.py`

`docs/WORLD_MAP_TEXT.md` explains the world-map file and its text from the start.

Freya's lesson in the sky ("Do you hear something?", the first world-map visit) is in neither the
scenes nor the 13 menu tables. `find` didn't see it, and all 1,118 scenes parse, so nothing was
being skipped.

**How it was found.**
* **Not the scene routine:** a break on `0x800532C4` never fires during the lesson. The world map
  draws its boxes itself.
* **RAM dump:** a dump taken during the lesson, searched for the lines' letter pattern (bytes equal
  exactly where the letters are equal), found "spiritual concentration" at `0x8009DB54` and "Close
  your eyes and open your heart" at `0x8009FEF8`.
* **Disc search:** every SLZ stream on the disc, decompressed and searched for those bytes, gave one
  match: directory word **4797**, byte 248,520.

**File 4797 is not a package** but an offset table of 67 SLZ streams:
* **Layout:** u32 n, then n words with the byte offset in bits 0-23 and a group number in bits
  24-31: 0 for streams 0-37, then 1, 2, 3, 4, 5, 6 and 10. Each stream is `SLZ` + kind, u32
  compressed length, u32 unpacked length, u32 0 and the payload; the next one starts 4-aligned.
* **Stream 38 (group 1), the font:** the scene-font layout, 76 glyphs.
* **Stream 39 (group 2), the strings:** the menu-table layout, u32 text offset = 4 + 8n, then 136
  (id, offset) pairs. The strings are encoded like scene records (glyph codes from stream 38,
  `80 80` a line break, `81 80` `<page>`, other commands with their arguments).
* **In RAM:** the font is at `0x8009CEE4` and the table at `0x8009D668`, each in a heap block of its
  unpacked size (the 16-byte block header in front holds the size), so a longer table or font is
  fine.
* **Room:** what limits growth is the file's 189 sectors. The streams end at byte 386,557 of
  387,072, and re-compressing gains a little.
* **Disc 2:** has the same file, built byte for byte the same way.

**The strings.**
* **0x01-0x0B:** world-map messages ("Press START button to begin spiritual concentration.",
  "This Chapter is concluded.", the 2-Periods warnings).
* **0x64-0x89:** the region descriptions.
* **0xC8-0x10A:** a list of character and place names.
* **0x12C-0x155:** Freya's first lessons (the sky, the first soul, the undead).
* **0x15E-0x16A:** her warning.

**The tool.** `export --worldmap` writes the table as an edit file. Its first line is
`# Valkyrie Profile world map text`, and the ids are hex, as in the menu tables; the text is written
as in a scene edit file. `build`, `vp_release.py` and the GUI pick such a file up.
* **Encoding:** `vp_worldmap_text.py` encodes with the scene editor's encoder. Characters the font
  lacks are added from the glyph database, and the count is kept even.
* **Rebuild:** it rewrites streams 38/39 with their own SLZ kind and rebuilds the offset table,
  keeping the group numbers. With no edits the file rebuilds byte for byte.

## Accented letters and a translation test (Italian)

The English release has no accented letters in any font. `vp_accents.py` draws them from the
game's own letters (scene, menu and battle fonts share one typeface): the base letter plus a
2-pixel accent. Lower case has free rows above the x-height (it starts at row 4), so the accent
goes in rows 1-2, where the i dot is. Capitals start at row 1, so the letter loses 2 rows and the
accent goes on top. It makes à è é ì ò ù, á í ó ú, â ê î ô û, ä ë ï ö ü and À È É Ì Ò Ù Á Í Ó Ú
(`python3 vp_accents.py` prints them).

How each kind of text gets them:
* **Dialogue:** `glyph_db` includes them, so an edit adds them to the scene font like any missing
  glyph. `open_disc` adds their bitmaps to the glyph names, so they read back as themselves.
* **Battle text:** they are in the reference glyphs (`ascii_glyphs`), so an edit adds them to the
  battle font.
* **Menus:** a menu font is a fixed ASCII code page and cannot grow. Each of `{ } \ ^ _ ` | @ < >`
  is used by only one string per table: string 0x0000, a list of the whole character set.
  * **Slots:** an accented letter is drawn over one of these glyphs, and the type-7 font part (or
    entry 1 of tables 6 and 4734) is re-compressed. A letter gets the same slot in every menu
    table: the tables of a build share one registry, because the italic title font below is one
    for all menus. So the menus hold at most 10 different accented letters between them.
  * **Reading back:** a slot is read as an accent when its bitmap is one. The battle code skips
    such slots when it takes ASCII glyphs from the menu fonts.
  * **Side effects:** the build log says which glyph each accent replaced. Changing a slot only
    changes how the character-set string draws.
* **Menu titles** ("Nuova Partita", the camp sub-menu names) use a separate italic font. The
  disc has two identical copies:
  * **Title package (word 6):** parts 50 and 51.
  * **Package 2173:** parts 39 and 40. The camp menu loads this copy: a build that patched only
    package 6 still showed the original table at `0x801027F4` in a camp-menu RAM dump.

  The parts, in package 6's numbering:
  * **Part 50 (type 19):** an SLZ-packed 4-bit TIM with no CLUT, a 256 × 128 sheet that the game
    puts at VRAM (896, 256). The glyphs fill rows 0-67.
  * **Part 51 (type 20, raw, 960 bytes):** the character table, 96 entries of 10 bytes for ASCII
    0x20-0x7F: `w, h, dx (s16), dy (s16), advance, u, v, 0`. A character with w = 0 has no glyph
    and is drawn as a 12-pixel gap. Among the slot characters only `^` had one.
  * **How a title is drawn:** `func_8003a9d8` copies the title into an ASCII buffer at
    `0x8004B500` (byte + 0x1F). `func_8003b294` looks each character up in the table, whose
    pointer is at `0x8004ABC4`; in RAM it is at `0x801027F4`. It then draws one sprite per glyph.
  * **What the build does:** when the menus use accents, it draws each accented letter into the
    free rows of both sheets, from the base letter's italic glyph plus a shaded 3-row accent
    (`vp_accents.italic_patch`). It points the slot character's table entry at it and re-packs
    packages 6 and 2173.
  * **Tested in TinyPSX:** the Ability sub-menu title shows "Abilità" in italics
    (`docs/notes/edit_proof/italian_italic_title.png`). The New Game and Continue titles are unchanged.

**Italian test** (2026-11-13; Arngrim's chapter added 2026-11-15). The 43 edit files in `edits_it/` (plus the title menu) hold 874 messages, counting the lines kept in English on purpose (wording choices: `docs/GLOSSARY.md`; how much of the game they cover: `docs/PROGRESS.md`, written by `vp_progress.py`):
* **Story:** Valhalla arrival (0x16 #14), Frei (0x1B #14-21), Odin's throne room (0x1C #14-43),
  the prologue in Midgard (0x13 #14-34: Freya and Valkyrie on the cliff, then the voices of
  Arngrim, the children and Lucian).
* **Arngrim's chapter** (storylines D0 = 1 and 2, 326 messages in 19 scenes, see below): Artolia
  (0x63-0x6D: Roland, Angela / Jelanda, Badrach, the Nan-Lou Garden), the delivery and the Ghoul
  (0x6E, 0x6F), Lombert (0x70, 0x71), the King's audience (0x72), then Lawfer (0x73, 0x74, 0x0B).
* **Title-screen prologue** (what plays when the title menu sits idle): Platina and Lucian, 46
  lines in scenes 0x3, 0x1, 0x5, 0x9, 0xC, 0x10 and 0x11 (see below).
* **World map** (`worldmap.txt`, file 4797): the START prompt and Freya's first lessons, 0x12C-0x155
  (20 messages). Seen in TinyPSX (`italian_worldmap_lesson.png`): the boxes size themselves to the text,
  and none of the lines wraps. 0x14A-0x155 come after Arngrim's chapter and were checked by read-back only.
* **System messages** (`system.txt`, records 2-13 of every scene; see System messages below):
  the Artifact prompts, "L'oggetto si è rotto.", "Tornare alla mappa?", "Battaglia".
* **Title package (6):** New Game, difficulty, Continue / load.
* **Camp menu (2175):** with its sub-menu labels in 2178, 2179 and 2182.
* **Configure, Data and world map:** 2184, 2185 (save / load messages, place names) and 4734
  (controls, place names).
* **Battle:** the battle menu and elements (1500.7), 20 consumable items with their help lines
  (1806.1), and the first battle hints (1988.2).

Linked copies carry many of these into other tables: item names into the camp sub-menus, load
messages into table 6. Each build with `--fit-boxes` fits every package, and all the messages
read back from the built disc as written (the system messages in all 1,118 scenes: 19,015 records,
0 differences).
* **Fonts:** the scene 0x1C font grew from 108 to 116 glyphs; 0x13 from 108 to 114; 1806 from 112 to 116; 1988 from 68 to 72.
* **Tight packages:** 2184 has 40 bytes to spare, 1988 has 164.

**Seen in TinyPSX** (`docs/notes/edit_proof/italian_*.png`):
* **Difficulty screen:** "Livello di difficoltà", Facile / Normale / Difficile, the help line and
  the settings table.
* **Continue screen:** "Continua", Dati 1-4.
* **Camp menu:** Tecniche, Usa oggetto, Reliquie, Gruppo, Abilità, Equipaggia, Trasferisci, Stato,
  Opzioni, Dati, Tempo, Punti materia, Vuoto, Valut.
* **Sub-menus:**
  * Options (Vel. testo, Tasti, Corsa, Ritratti, Vibrazione);
  * Data (Salva / Carica);
  * Use Item (Usa, Converti MP, Oggetti usabili, Nome oggetto, Numero, Equip.);
  * Party (Forma, Sfera esp., Prova, Nome, Livello);
  * Ability (Attacco normale, Contrattacco, Attacco speciale).
* **Battle menu:** Fine turno, Equipaggia, Riordina.
* **Dialogue:** Valhalla, Frei and the whole of Odin's audience, with the drawn accents ("È passato
  così tanto...", "Sì.", "la più grande delle tre dee del fato").
* **Prologue (0x13 #14-21):** "Questo è... Il mondo degli umani...?", "È tempo che io ti insegni chi
  sei" (`italian_prologue.png`; `dlg/map/left.replay` plus ✕ every 50 frames, frames 32700-33700).
  Records 22-34 (the voices) didn't show on screen in that run: they're checked by read-back only.
  Freya's sky lesson that follows ("Do you hear something?", "Press START button to begin spiritual
  concentration") is in neither a scene nor a string table the editor reads, so it stays English.

**The title-screen prologue.** Left idle on the title menu, the game plays Platina and Lucian's
story, then the "VALKYRIE PROFILE" title and the opening movie, and loops (about 41,000 frames).
* **Scene order** (break on the scene switch `0x8006C270`, `$a0` = scene): 0x3 (already loaded when
  the loop starts), 0x1, 0x5, 0x2, 0x9, 0xB, 0xC, 0xD, 0x12, 0x10, 0x11, 0x10, then back to 0x1.
* **Shown records** (break on `0x800532C4`, `$a1` = record): 0x3 #14-15, 0x1 #14-19, 0x5 #14-21,
  0x9 #14-26, 0xC #14-17, 0x10 #14-25, 0x11 #14-20. The scenes' other records belong to later story
  events (Lucian and Valkyrie in Coriander, Lawfer, the Weeping Lily meadow) and stay English.
* **Boxes here aren't literal calls.** The dialogue goes through scene-local wrappers (scene 0x11:
  calls to words 0x31F and 0x331, then op `0x92` with the record), so `--fit-boxes` can't see or grow
  them. A line wider than its box wraps at the edge. Lines that wrapped were shortened until they
  showed on one line: "Come puoi dire così!?", "...il Cielo?", "Sono felice di averti conosciuto,",
  "dimenticare... tutto...". A line a few pixels wider than the English can still fit (e.g. 0x5 #16,
  151 → 168), so the screen decides.
* **Seen in TinyPSX** on the whole loop (`italian_title_prologue.png`; no input, dumps every 80
  frames from 4,400). All 46 lines read back as written.

What the test taught:
* **Menu labels sit at fixed places.**
  * "Oggetti divini" ran into "Gruppo" and became "Reliquie".
  * "Velocità testo" touched its value and became "Vel. testo".
  * "Modifica" and "Schiera" ran into "Sfera esp."; "Forma" fits.
  * "Equipaggiamento" was cut off and became "Equip.".
  * The Dash help line was shortened to fit its box.
  * The build's width notes point at these; the screen decides.
* **Sub-menu titles use a separate italic font,** so at first a redrawn accent was blank there:
  the Ability title showed "Abilit". Finding that font (see Accented letters) fixed it: the build
  now draws the accents into it too, and the title shows "Abilità".
* **The first button press after opening the camp menu is dropped,** and LEFT / RIGHT wrap within a
  row of five: a capture script needs a dummy press first.
* **Not on screen:** the world map, the item help lines (a new game has no items), the battle hints,
  and "Usa oggetto" in battle. These were checked by reading the disc back. The title screen's own
  menu is pictures, not table text; it is redrawn separately (see Title screen menu).
* **Not translated:** stat abbreviations (ATK, RDM, ...) and "Back Console" are left as they are.
  (The shared system strings, records 0-13 of every scene, are now edited once in `system.txt`.)

**`# no-link`.** An edit file with a `# no-link` line before its first record changes only its own
table. Its edits are applied after the linked ones, so they win over a copy linked from another
file. `build` and the GUI both honour it, and the GUI keeps the line when it saves the file.

## Arngrim's chapter, and jumping to any story scene in TinyPSX

**What is translated** (2026-11-15): every line of storylines D0 = 1 (Arngrim I, Artolia) and D0 = 2
(Arngrim II, Lawfer), found from `story/event_order.txt`: scenes 0x63-0x74 and 0x0B #14-32, plus the
Artolia townsfolk and signposts in those scenes. Lines in the same scenes that belong to later
storylines are left for their chapters: Badrach's slaver story (0x6E #69-73), Suo (0x6F #33-45),
Jayle and Fahn (0x74 #25-36) and Kashell's group (0x0B #33-38).

**Box sizes.** Most lines go through literal `func_03e2` calls, which `--fit-boxes` grows; the widest
grown portrait box is 232 px (0x70 #16), and the game's own go up to 256 (x = 0) and 266 (x = 1).
Some calls push a variable x (`0B800000`) before literal width, height and record pushes, so
`box_calls` skips them and the build says "no literal box call found". Their widths were checked
by hand (all fit: e.g. 0x6A #15 is 171 px in a 176-px box). Signposts ("Uscita", "Cappella") are
shown by op 0x98 in a 192-px box. The only other unchecked record, 0x65 #21 "Ottieni Dragon
Slayer" (shown by `func_0a21`), is narrower than the English.

**Read-back** of the full Italian disc 1: 1,118 scenes, 19,015 records, 0 differences.

**Seen in TinyPSX** (`docs/notes/edit_proof/italian_arngrim.png`): the restaurant (0x6C), Roland's
house (0x64), the King's audience (0x72) and the Ghoul ambush with its voiced narration (0x6E).
No line wraps.

**How to start any event headless.** D0 and D1 are the bytes at globals + 0xD0 / 0xD1, so with
globals at `0x801E5BD8` the word at `0x801E5CA8` is `D1 << 8 | D0`. Op `0x9E` with an inline scene
number goes to that scene (scenes end an event with `9E0000nn, 87000000, 7C000000, 9F000000`).
With the message-box recipe (frame 6000 of `vp_newgame.replay`, break at `0x80078934`), write the
state word, keep the three words `D80000EC, 01A8013B, 0` at the script position, then write those
four ops. About 300 frames later the target scene plays its event for that (D0, D1); the states
are the bracketed steps in `story/event_order.txt` (restaurant: D0 = 1, D1 = 9 → `0x901`, scene
0x6C). Press CROSS about every 90 frames to advance. Flags the event also tests are not set, so
an event gated on a flag may not start this way.

## System messages: `system.txt`

**Records 0-13 of every scene are the same 14 system messages,** byte for byte in all 1,118 scenes
on disc 1 (and the 1,013 on disc 2): "The item has broken.", the Artifact prompts, "You can't hold
any more items.", "You earned N Event EXP.", "To the rear" / "To the front", "Exit to World Map?",
"Battle". Each scene's font has the same codes for their glyphs. Record 0 ("--") and record 1 (a
digit line) are not shown text, and record 12 is "Scenario Error 1".

**Editing them.** `export --system` writes an edit file with the header
`# Valkyrie Profile system messages`. `build`, the release script and the GUI write each message it
lists into every scene whose records 0-13 are the usual ones (`with_system` in the editor), adding
any glyph a scene's font lacks. A scene edit file that also lists one of these records wins for its
scene. The build log names only the scenes that have their own edits, and ends with one summary line.

**Cost.** Every scene package is re-packed: 10,113 sectors change on disc 1, and the patches grow
from about 1.2 MB to about 19 MB per disc (the re-compressed text containers are new bytes, which
neither xdelta nor PPF can shorten). The tightest scene, 0xD6, keeps 188 bytes free.

**Where they are shown, and their boxes.** None of them grows with its text:
* **Script library** (the first 0xA51 words of every scene script). `func_0029` shows record 8
  with pushes 3, 8, 12, 240, y, 40, 0: a 240 x 12 box at x 40. `func_00AF` shows record 11 with
  op 0x93 (a choice): 140 x 46. `func_0059` shows records 9 and 10 with op 0x98, 192 x 14 each.
  Record 13 goes through the literal call `func_03e2(4, 4, 96, 14, 13, 0)`, so `--fit-boxes` could grow it.
  `func_0001` has op 0x92 for records 0-7 with a 16 x 16 box, but nothing calls it.
* **Field engine.** Records 2-6 come from engine code (in RAM at 0x80060948: `show_system_message(record,
  x, y, width, height)`, which opens the box at [0x8007F120] and calls `func_800532C4`). The calls are at:
  * 0x8006004C: record 3, 236 x 70 at x 42;
  * 0x80060094: record 4, 172 x 56 at x 74;
  * 0x800601F4: record 6, 204 x 42 at x 58;
  * 0x800604CC: record 5, 224 x 42 at x 48;
  * 0x800661F8: record 2, 280 x 12 at x 20.

  0x8005FAB4 passes a record from the pickup actor (+0x3A) with 280 x 12: the "You found ..." lines and,
  probably, record 7. The handler table at 0x800831D0 gives op 0xCC (treasure) as 0x8006FC00, and that
  creates the pickup actor.
* **Op 0x92 inline layout:** word 1 = y << 16 | x, word 2 = height << 16 | width, word 3 = 0.

**Seen in TinyPSX** (`docs/notes/edit_proof/italian_system_messages.png`). Records 2-8, 11 and 13 were
shown in the boxes above, in place of the scene's next ops: a debug script stops at the 0x8D handler
(0x80078934) after frame 6000 of `vp_newgame.replay`, reads the script position at ctx+0xF0 (ctx =
[0x8007F1EC]), and writes ops there. The first op there, 0xD8, takes 2 argument words, so a third
word (0) is needed before the new ops. No line wraps. Records 9 and 10 were not reached; "Indietro"
and "Avanti" are 38 and 33 px wide in 192-px boxes.

## Title screen menu: `vp_title_menu.py`

`docs/TITLE_SCREEN_MENU.md` explains the title menu from the start, with the label table.

The title menu (New Game, Continue, Configuration, Sound Mode, Prologue, Opening Movie and the
hidden Seraphic Gate) is pictures of text, so it is redrawn, not edited.

**Where it is** (found 2026-11-13 by dumping RAM at the title, then watching the sheet's RAM copy
being written and the sprites being built):
* **The sheet is file 7** (directory word 7, one of the header files): one SLZ type-2 block, a 4-bit
  TIM of 256 × 256 at VRAM (896, 256) with 3 CLUTs at (16, 504). The title code
  (`func_80036cc8`) unpacks it at frame 1457. It holds each label twice:
  * thin anti-aliased text, CLUT 0x7E01;
  * a heavier, blurred glow drawn behind it, CLUT 0x7E02, colour 0x505050;
  * and the copyright line, in two pieces at (0, 109) and (108, 237).
  The italic title font later goes to the same VRAM area.
* **The title code is file 1:** one SLZ type-2 block of 274,888 bytes, loaded at 0x8002F824.
  `func_80037860` builds the menu sprites (GP0 0x66), one item per pass (t3 = 0-6). For each sprite
  a switch case (jump tables at 0x8005103C and 0x8005105C) sets its values as immediates:
  * size: `lui` + `ori`;
  * sheet position: `ori` / `addiu`;
  * x: an `addiu` added to -8, so the labels are right-aligned at x 440. Seraphic Gate's x is
    absolute, 64.
* **Glow placement:** the glow sprite goes at the thin label's x, y, ±1 for Configuration, Sound
  Mode and Prologue. Items 2, 3 and 6 have a second glow piece at a fixed x offset (code at
  0x80037F34).
* **Prologue's sheet position** is written as `sh $zero`, so its thin label must stay at (0, 0).

**Redrawing** (`vp_title_menu.py`, used by `build`):
* **Edit file:** `export --title` writes a template with a `# Valkyrie Profile title menu` header
  and `@@ 0`-`@@ 6`, one label each. An item left out keeps its English text.
* **Rendering:** each label is rendered with CoreText through `tools/render.swift` (compiled with
  `swiftc` on first use; macOS only):
  * thin layer: Times New Roman 25 pt, the closest of the system serifs to the game's;
  * glow layer: Times New Roman Bold 24 pt, blurred twice (σ 0.8 and 2.0) and merged.
  * Every item is re-rendered, so the whole menu matches.
* **Packing:** the pieces are packed into the sheet around the copyright pieces. The glow images
  lose their empty right and bottom margins, and the glow of items 2, 3 and 6 is split into two
  halves. Three placement orders are tried (tallest, widest, largest first) and the first that
  fits is used.
* **Patching:** the size, sheet position and x immediates are patched. Thin x is 448 − width, so
  the labels stay right-aligned. Files 7 and 1 are re-compressed: 12.9 KB of 18 KB, and 114.7 KB
  of 116 KB.
* **Space:** the sheet is 256 × 256, so the labels' total length is limited. The English labels use
  73 letters; the Italian ones use 79, with "Colonna Sonora" for Sound Mode. The first, simpler
  packer could not place those; the current one can.

**Tested in TinyPSX** (`docs/notes/edit_proof/italian_title_screen.png`, original on the left):
* The menu reads Nuova Partita, Continua, Opzioni, Colonna Sonora, Prologo, Filmato Iniziale,
  each with its glow, right-aligned like the original.
* The hand cursor and the green selection line are in place. The line sits one pixel lower on the
  text than in the original.
* Choosing Nuova Partita goes on to the difficulty screen as before.
* The hidden Porta Serafica (shown after the game is finished) was not seen.

## Releasing a translation patch: `vp_release.py`

```
python3 vp_release.py --disc1 DISC1.cue --disc2 DISC2.cue --edits edits_it \
    --name "Valkyrie Profile (Italiano)" --version 0.1 -o release/ [--require-redump] [--notes NOTES.txt]
```
For each disc it builds the patched image from every edit file in `--edits` (with `--fit-boxes`;
into `release/work/`, deleted afterwards unless `--keep-work`). Then it writes two patches
against the original `.bin`:
* **xdelta (VCDIFF, RFC 3284):** for Delta Patcher, xdelta UI, xdelta3 and Rom Patcher JS. It
  uses 4 MB windows, each copying its own range of the source and adding the changed bytes. Each
  window carries xdelta3's Adler-32 of its output (Win_Indicator 0x04), so a wrong source image
  is refused instead of silently giving a corrupt disc.
* **PPF 3.0:** BIN type, with the block check (the source's 1,024 bytes at 0x9320) and no undo
  data. DuckStation applies it automatically when it has the image's name and sits next to it,
  with "Apply image patches" turned on.

Both formats are written in pure Python. Before keeping a patch,
the script applies it to the original with its own decoder and checks that the result matches
the built image byte for byte. It then writes `README.txt`, with how to apply the patches and the
CRC32, MD5 and SHA-1 of each original and patched `.bin`, and zips the patches and the readme.

**Two discs.** Disc 2 has its own `VALKYRIE.BIN`, 650 MB against 635 MB:
* 859 of its files differ from disc 1's, but every scene, menu and battle table the Italian edits
  touch has the same text.
* Its title code and sheet (files 1 and 7) are identical once unpacked, but stored elsewhere.
* Its directory has empty words, 0, right after files 1 and 7. So a file's end is now the next
  word with a *later* sector (`vp_scenes.file_end`), not simply the next word; on disc 1 this
  gives the same ends as before.
* Disc 2's title menu shows only four items: no Prologue or Opening Movie. With the same edit
  files, its patched image boots to the Italian title and difficulty screens in TinyPSX.

**Redump.** Players will have the Redump dumps, so a public patch should be made from them.
The script knows their hashes (redump.org discs 655 and 656):

| disc | serial | CRC32 | SHA-1 |
|---|---|---|---|
| 1 | SLUS-01156 | dd0422d5 | fa13cbde448a47fa87b6c7829df1c5e6215f5f66 |
| 2 | SLUS-01179 | c2132829 | 7cc65766b46273b4ee166269170ecbaa433908fd |

It warns when a source does not match, and the readme says so. `--require-redump` makes the
mismatch an error instead.
* **Redump images:** both are in `~/Downloads/Valkyrie Profile (USA) (Disc 1|2)/`, and the
  release is built from them.
* **The older disc 1 dump** (`~/Documents/vp.bin`, 2005; the editor's default disc) differs from
  Redump in two sectors only, neither of which an edit touches:
  * sector 140, a Form 2 sector with 20 corrupted data bytes and a wrong EDC;
  * sector 150559, one ECC byte.

**First release (v0.1, 2026-11-13).**
* **Sources:** both discs from the Redump images, built with `--require-redump`.
* **Size:** each disc has 531 changed ranges (652 KB). The xdelta patches are 661 KB, the PPF
  patches 680 KB, and the zip is 2.2 MB.
* **Tested in TinyPSX:** both patched discs boot to the Italian title screen and difficulty screen.
* **Checked with xdelta3 3.2.2** (Homebrew, `/opt/homebrew/bin/xdelta3`):
  * `xdelta3 -d -s` on each Redump `.bin` gives exactly the README's patched SHA-1;
  * it refuses the old disc 1 dump (2 sectors differ) and the disc 2 image with the disc 1 patch.
* **PPF, tested in DuckStation** (0.1-12640): DuckStation applies a `.ppf` that has the image's
  name and sits next to it, but only with "Apply image patches" (`[CDROM] LoadImagePatches = true`)
  on.
  * **Result:** the log said "Loaded 320 replacement sectors from version 3 PPF" (the 320 sectors
    the build changes), the Redump disc 1 booted, and the title menu showed in Italian, as seen on
    screen by the user.
  * **Settings:** the setting was turned on only for the test, and `settings.ini` restored after.

## The message box: how a line is laid out and drawn

I traced this to make the GUI's preview match the game. Each point was checked against TinyPSX
screenshots.

**From script push to screen.**
* The scene script calls a library wrapper with six pushes: x, y, width, height, record, and a
  last value. `func_03e2` calls `func_033c(x, y, w, h, record, 0)`, waits for the box to be
  dismissed, then waits for the last value in frames. `func_033c` resolves small x/y values, then
  runs op `0x92`. Op `0x92` stores x, y, width and height at box+`0x4C8`/`0x4CA`/`0x4CC`/`0x4CE`.

| pushed x | x used | 0xE0 value: portrait side for `<19:ffff>` |
|---|---|---|
| 0 | 24 | 1: left |
| 1 | 296 − w | 0: right, mirrored |
| 2 | 24 | 0: right, mirrored |
| 3 | 296 − w | 1: left |
| 4 | 160 − w/2 | — |
| 8-15 | depends on that actor's property 0xC | — |
| other | as pushed | — |

* For y: 2 → 16, 3 → 192 − h, 4 → 120 − h/2; any other value is used as is.
* On screen, the frame's outline spans x − 8 .. x + w + 9 and y + 1 .. y + h + 14. Text starts
  at (x, y + 8). Checked on boxes with x = 120, 0 and 1.

**Layout** (`0x80052C3C`, which measures one line, and `0x8004DEE4`, which adds the rest of the
word):
* A glyph advances by `font[7 + code]` × the width scale (command 7).
* After each glyph the code adds the rest of the word, up to the space glyph. The line ends before
  this glyph unless both the line so far and that word end are less than the width at box+`0x4CC`.
  The test is strict: of 3,417 literal calls, none has its widest line exactly as wide as the
  box, and 45 have it one pixel narrower.
* Glyph code `0x31` is the space, width 4, in all 1,118 fonts.
* A space that lands at the start of a wrapped line isn't drawn.
* A line is 12 × its largest height scale (command 8) + 2 pixels tall, so 14 normally.
* Lines beyond the box height scroll the box up a line at a time.
* A double-height glyph in a box with no room for its 26-pixel line never appeared (an edit test).

**Drawing** (`0x80052480`):
* Each glyph is rasterised into a 16 × 14 4-bit cell:
  * the glyph's pixels get palette index (colour & 3) × 4 + 1;
  * its shadow, at (+1, +1), gets that index + 1;
  * where the shadow falls on the glyph's own pixels, the glyph stays on top.
* The cells are drawn as sprites through the GPU ordering table, so later glyphs are drawn first.
  An earlier glyph's shadow therefore covers the next glyph: the `T` of "This" shades the top of
  the `h`.
* The colours, measured from an edited line:

| colour | glyph | shadow |
|---|---|---|
| `<4:00>` | (16, 16, 16) | (140, 140, 140) |
| `<4:01>` | (115, 41, 0) | (189, 148, 99) |
| `<4:02>` | (107, 0, 0) | (189, 140, 140) |
| `<4:03>` | (0, 123, 57) | (115, 173, 132) |

**The box artwork:**
* The fill is opaque: the same pixels over the bright sky and over the dark hall.
* Under the bevel, a parchment texture shades from about (226, 222, 209) at the top to
  (176, 172, 163) at the bottom. The shading stretches with the box height.
* The frame is a (90, 66, 8) outline around a 2-pixel gold bevel, with chamfered corners.
* `vp_box_skin.py` cuts it out of a screenshot (`vp_box_skin.png`).

**The GUI's preview against the game.** I compared the dark text pixels, the shadow pixels and the
box outline, per pixel, between the GUI's canvas and TinyPSX screenshots of the same lines. They
are identical for:
* Valhalla record 14 and Frei/Valkyrie records 14-15, as on the disc;
* the fitted edits, including glyphs added to the font;
* the unfitted Frei edit in its wrapped-and-scrolled final state;
* all three extra colour sets;
* a line edited and built in the GUI, then run in TinyPSX.

Width- and height-scaled glyphs are only close. The game stretches the glyph sprite, and the
sampling repeats source columns unevenly.

## Tools

* `vp_text_gui.py` (+ `vp_text_gui.html`): the editor as a local web app with the in-game preview;
  `vp_box_skin.py` / `vp_box_skin.png` (the box artwork), `vp_bg_valhalla.png` / `vp_bg_hall.png`
  (preview backgrounds), `vp_png.py` (PNG read/write).
* `vp_text_editor.py export | menus | build | find | check` edits scene, menu and battle text and
  writes a patched copy of the cue/bin (see Editing the dialogue, Editing menu text for
  `vp_menu_text.py`, and Battle text for `vp_battle_text.py`). It uses `vp_slz.py` (the SLZ type 1 / 2 compressors),
  `vp_disc.py` (the ISO 9660 lookup, Mode 2 Form 1 EDC/ECC, the copy-and-patch) and
  `vp_glyph_db.json` (each character's most common bitmap and width, built on first use).
* `vp_scene_text.py VALKYRIE.BIN OFFSET | --all [--raw]` decodes scene text straight from the
  archive (the game's SLZ decoders, per-scene fonts, command table).
* `vp_script.py VALKYRIE.BIN OFFSET [--lib]`: an event-script listing with functions, labels, decoded
  operands, and each message call annotated with its text. `--lines` lists every scene's lines in
  script order; `--stats` gives the coverage.
* `vp_glyphs.json`: the 158 glyph bitmaps → characters.
* `vp_accents.py`: accented letters drawn from the game's glyphs (see Accented letters).
* `vp_builtins.json`: every built-in's name, confidence, argument layout and evidence.
* `vp_scenes.py VALKYRIE.BIN [--json OUT | --scene ID]`: scene id → directory word → package parts →
  text container, with place names and exits (`vp_scene_map.txt`, `vp_scene_map.json`).
* `vp_flow.py VALKYRIE.BIN --scene ID | --all [--flags]`: a scene's lines in play order, nested in
  their conditions, choices and loops (`vp_scene_flow.txt`).
* `story/`: storyline chains, flags and event order (`run_all.sh`; see Story variables).
* `docs/notes/`: detailed findings behind the sections above: `builtins_pass2.md` (second naming pass,
  0xC9 sub-ops, 0xE0, ctx+0xA4), `package_parts.md`, `voice_cmd12.md`, `sound_labels.md`,
  `rare_glyphs.json` / `.png`.
* `vp_all_scene_text.txt` (every scene's text) and `vp_scene_lines.txt` (every scene's lines in
  script order).
* In TinyPSX: `dump PATH [ADDR LEN]`; `break decompress_resource if $a1 == 0x801c8ef4`;
  `rwatch 0x800f68a0,2`; `catch cdrom:Setloc`; `watch 0x800938e4`; `break 0x800715e4` (each VM
  fetch: `$a0` = instruction, `$v0` = next ip).

## Built-ins settled by live tests (2026-11-12)

Each was run for real in TinyPSX by overwriting words of the running Valhalla script with the
debugger (method in `docs/notes/builtins_pass2.md`; the patch point, the second `0x8D` hit, is at frame
~6440 with the current build). Details in `vp_builtins.json`.

| op | name (was) | what it is | test |
|---|---|---|---|
| 0xD8 | `set_battle_return_scene` (same) | the scene the engine enters after the next battle, as a fresh entry (`ctx+0x39A` = 4) instead of resuming; the position/facing arguments are overwritten by `start_battle` | `d8 0xEC` + a battle: after the win, scene 0xEC (the sluice gate) loaded |
| 0xDF | `bgm_save_state` (`sound_engine_latch_ctx_field`) | snapshot of the music player (24 channels, data pointer, volumes) into a 2,144-byte buffer; nothing restores it in the code searched | buffer went from empty to 756 bytes matching the sound manager |
| 0xE0 | `set_portrait_default_side` (`set_msgbox_portrait_flip`) | the portrait side for `<19:ffff>` lines | see the 0xE0 test above |
| 0xE4 | `set_character_weapon` (`set_character_item`) | equips a weapon (item id) on a roster character; the item's class picks the battle graphics | `e4 1 0x106` (Elven Bow): Lenneth fights with a bow |
| 0xE8 | `add_evaluation_level` (`add_hud_gauge_0x388`) | Evaluation Level, the menu's "Eval." (starts at 100). The "gauge" routine is the Artifact prompt: offer to Odin +1 (cap 100), keep it −5 (floor 0) | `+50`, `−5`: menu shows Eval. 145 |
| 0xEA | `restore_party_after_battle` (`reset_active_actor_motion`) | for party members (role 1/2), current HP (+172) = max HP (+176) and status ailments (+214) cleared; used right after the 3 scripted battles | camp menu: HP set to 123 shows `DME 123 / 1000` (red), after 0xEA `1000 / 1000`; status 0xFF shows ailment icons on the party panel, after 0xEA none (`docs/notes/edit_proof/builtin_ea_hp_status.png`) |
| 0xEB | `add_materialize_points` (`add_ctx_counter_0xa4`) | Materialize Points (2,000 at New Game; +300 × chapter per recruit; +20,000 in the Seraphic Gate) | `+1000`, `+234`: menu shows 3234 |
| 0xD1 | `actor_set_sort_params` (same, still low) | byte 1: a one-pixel front/behind bias against the wall layer; byte 2: no reader found | no visible change on the Valhalla balcony |

## Still open

* Built-in 0xD1's second byte (actor+0x103): no reader found in the field engine or the scene code
  packages.
  (The other low-confidence built-ins are settled: see "Built-ins settled by live tests".)
* What starts each storyline (the engine, presumably from the world map; scripts never set
  `story` except to reset it), and so the exact order between storylines. Storylines 11, 20 and 28
  occur only as debug-menu presets.
* `period` (`G0x69.b`) is likely but not proven; 327 of the 403 flag names are placeholders.
* Package parts: the inner layouts of types 0, 8, 13 and 16; who uploads type 23 to SPU RAM; what
  reads scene-table field +6. Header file 5 (7 is the title menu sheet) and the menu-only part types 7, 19, 20 (in the title package, 19 and 20 are
  the italic title font's sheet and table; see Accented letters).
* Whether the 154 missing voice clips are on disc 2.
* Battle text: an on-screen check of the 1806 item text and the 1988 hints (needs a party with items,
  or a fight with a monster that has a hint); how wide a battle label can get before it leaves its
  box.
* Menu text: what the extra menu glyphs 0x60-0x7F / 0x82-0x85 show in
  each module; whether the menu code clips or wraps any string (none seen so far: wider text just
  overlaps).
* Whether the music snapshot built-in 0xDF saves is ever restored (no code sets the restore byte in
  the EXE, field engine or battle module; title, menu and world-map modules not searched).
