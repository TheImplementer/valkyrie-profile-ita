# Valkyrie Profile (SLUS-01156): scene package parts, archive header words 1-8, scene table fields +2/+4/+6

Everything here was derived from `VALKYRIE.BIN` (all numbers are computed by the scripts in this
directory), from the field-engine code in `ram_interp.bin`/`ram6440.bin`, and from live traces in TinyPSX
(`loader.dbg/.out`, `loader0.dbg/.out`, `t18.dbg/.out`). Confidence: **high** = code and data agree (or live
trace), **medium** = code or data clearly show it but the meaning is inferred, **low/guess** = marked.

Scripts: `common.py` (loads the archive, directory, byte table, SLZ decode, TIM header), `enum_parts.py`,
`survey.py`, `stats.py` (-> `type_stats.json`), `render.py`/`render13.py` (PNG), `t17*.py`, `scenetab.py`,
`hdr.py`, `allpk.py`, `modscan.py`, `xref.py` (lui/lw pair finder in a RAM image). Run with `python3 -I`.

---------------------------------------------------------------------------------------------------

## 1. Package parts

Package = `u32 count, u32 header size (8+8*count), count x (u32 type, u32 stored size)`, then the parts back
to back. A part is either an **SLZ chain** (`'SLZ'`+kind byte, `u32 clen @+4, u32 ulen @+8, u32 step @+12`,
data @+16; kind 0 = stored, 1 = `slz1`, 2 = `slz2`; `step` = distance to the next entry in the chain, 0 = last)
or **raw** bytes. `decompress_resource` (EXE `0x800122C0`, `a3` = entry index in the chain) and its sizing twin
`0x8001226C` (malloc of the `ulen` of entry `a2`) are what the loader calls.

### 1.1 The loader dispatch (solid, read from code, confirmed live)

`func_8006B6C4(a0 = &(type,size) entry, a1 = pointer to the part's bytes in the loaded package, a2 = 0 scene /
1 shared package)` reads `type = [a0]` and jumps through a **24-entry table at `0x8007DFB4`**
(`sltiu type,24`). Every type not in the list below goes to `0x8006C254` = return, i.e. the field engine
**ignores** types 1, 5-7, 9-11, 14, 15, 19-21 and >= 24.

| type | case | what the case does |
|---:|---|---|
| 0 | `0x8006B710` | alloc + decompress -> **`[0x8007F0F8]`** (kept for the scene); builds `(n+7) x 60`-byte layer records -> `[0x8007F100]`, `n` -> byte `0x8007FD77` |
| 2 | `0x8006BAA0` | alloc + decompress; parse as TIM; `LoadImage` (EXE `0x80021058`) CLUT then image; mark VRAM region bit in `0x8007FD75`; free |
| 3 | `0x8006BC14` | alloc + decompress; `0x8006EB18` (TIM load, CLUT-bank bookkeeping at `0x8007FD50`, in shared mode records the rect at `0x8007FD08`+16*n); free |
| 4 | `0x8006BDAC` | decompress entry 0 -> `[0x8007F1AC]`, entry 1 -> `[0x8007F1D8]`; sets `[0x8007F1D0]/[1D4]/[1DC]`, clears the glyph cache |
| 8 | `0x8006BC60` | alloc + decompress -> table **`0x8007EFD8`** (8-byte entries: ptr, u16 id = first word after the length at data+4, flag byte at +6: 0 scene, 1 shared), count `0x8007FD79` |
| 12 | `0x8006BE90` | `0x80056358(rect)` then `LoadImage(RECT = part+4, pixels = part+12)` (raw part, no decompress) |
| 13 | `0x8006BEB0` | alloc + decompress -> table **`0x8007F270`** (36-byte entries, id kept at entry-4, flag at +0x1C), count `0x8007FD74`; then `0x8006E4B4` uploads its CLUTs into the VRAM CLUT bank (y 496..504) and records their position |
| 16 | `0x8006C000` | alloc + decompress -> table **`0x8007FB6C`** (8-byte entries: ptr, flag@+4), count `0x8007FD76` |
| 17 | `0x8006C128` | malloc(size) + memcpy of the **raw** part -> **`[0x8007F118]`** |
| 18 | `0x8006C154` | memcpy of the raw part to **`0x800899B0`**, `[0x8007F25C] |= 0x100000`, flush (`0x8001054C`) |
| 22 | `0x8006C190` | malloc(0x13880) -> `[0x8007F1E8]`, memcpy of the raw part to **`0x800BEA00`**, `[0x8007F25C] |= 0x40000000`, flush |
| 23 | `0x8006C1E8` | malloc(size) + memcpy of the raw part -> table **`0x8007F228`** (max 8 slots, count byte `0x8007FD84`) |

Callers of `0x8006B6C4`: `0x8006B528` (scene (re)load function `0x8006B444`, a2 = 0), `0x8006CF30`
(scene change, a2 = 0), `0x8006E2E4` (the shared package, a2 = 1: entries are flagged "keep"; the clean-up
`0x8006D038` frees table entries whose flag bit 0 is clear, so scene-local 8/13/16 go away on a scene change and
the shared ones stay). Mode byte `obj+0x39A` = 3/6 (returning from a menu/battle, map mode) skips the leading type-2
parts (`0x8006B2A8`).

**Live check** (`loader.out`, `loader0.out`, break on `0x8006B6C4`, `x/2wx $a0`): the Valhalla load (frames
6576-6591) dispatches exactly its 14 parts in package order (types `2,3,3,0,18,4,13,8,13,8,13,8,13,8`) with
data pointers `0x8018FD2C ... 0x801CE114` (= package at `0x8018FCB4` + `0x78` + running sum of sizes). The shared
package (frame 2153-2156, a2 = 1, `ra=0x8006E2EC`) dispatches `3,3,13,8,8,8,16,16,16,12 x7` = package 3606's
parts. A `break 0x8006C178` (the type-18 memcpy) fires at frame 6590 with `a0=0x800899B0, a2=0x1154` (the
type-18 part's size), and `0x800899B0` is then **called** at frame 6592 with `ra=0x8006D934` (the `jal` at
`0x8006D92C`, guarded by flag `0x100000`). RAM at frame 7600 shows the type-0 buffer byte for byte equal to the
decompressed part (12 bytes differ, runtime-modified), the type-8 table holding Valhalla's ids `0x233C, 0xBB9,
0x233E, 0x2360` (flag 0) plus shared ids `3, 0xFAC, 0xFAD` (flag 1), five type-13 entries, three type-16 entries
(ids 0, 1, 2, flag 1).

### 1.2 Population (scene packages = directory words 3610..4727, plus shared 3605/3606)

| type | parts | in packages | stored size min / avg / max | uncompressed avg | storage |
|---:|---:|---:|---|---|---|
| 0 | 1118 | 1118 (all) | 496 / 28,150 / 234,348 | 47,005 (max 426,656) | SLZ2 930, SLZ1 188 |
| 2 | 2952 | 1118 (1-4 each: 1 x241, 2 x208, 3 x381, 4 x288) | 52 / 74,057 / 216,784 | 119,700 (max 280,608) | SLZ2 2942, SLZ1 10 |
| 3 | 290 | 263 | 236 / 7,248 / 25,044 | 15,860 | SLZ2 282, SLZ1 4, stored(0) 4 |
| 4 | 1118 | 1118 (all) | 5,044 / 5,778 / 15,252 | 15,369 | SLZ chain (1,2) x1117, (1,1) x1 |
| 8 | 1717 | 749 | 56 / 334 / 10,884 | ~800 (max 22,518) | SLZ2 1310, SLZ1 406, stored 1 |
| 12 | 14 | 2 (3605, 3606; also the copies/variants) | 44 / 181 / 524 | same | **raw** |
| 13 | 1505 | 721 | 96 / 6,032 / 99,052 | ~14,900 (max 196,524) | SLZ2 1156, SLZ1 349 |
| 16 | 30 | 26 (24 scenes + 3605, 3606) | 152 / 242 / 512 | 172, 384, 492, 668 | SLZ2 |
| 17 | 923 | 923 (= every scene with a map group, see section 3) | 20 / 36 / 108 | same | **raw** |
| 18 | 377 | 376 | 236 / 6,060 / 12,316 | same | **raw** |
| 22 | 64 | 64 | 78,812 (constant) | same | **raw**, all 64 byte-identical |
| 23 | 42 | 40 | 11,208 / 28,454 / 49,608 | same | **raw** |

Types that occur **only outside the scene packages** (menu/boot/map resource packages, file-table entries
6, 2173, 2175-2186 and the group packages 4735-4765): 1 (raw tiny TIM, 448 B), 7 (SLZ2, a font-width table: `u32 n,
u32 12 rows, n width bytes`, same header as a scene font), 19 (SLZ2, a 16,404-byte 4bpp TIM, font bitmap
sheet: guess), 20 (raw 960-byte table, unknown). These four are ignored by the field engine loader.

### 1.3 What each type is

**Type 2: 8-bpp TIM, background "chip atlas" (high).** All 2,952 decompress to a valid TIM (magic 0x10, flags
`0x9` = 8bpp + CLUT in 1,118 parts, `0x1` = 8bpp without CLUT in 1,834). The first type-2 part of every scene
carries the CLUT (VRAM `(768,480)`, 256 colours x 2..40 palettes); the rest share it. Image rects (VRAM
16-bit-word coordinates): x=384 with 768 px wide strips `192+192+128` rows (503 scenes, three parts), 256 px x 512
(211), `230+230+52` x 640 (152), 512 px `288+224` (128), 384 px `384+128` (74) = the scene background split in
bands; and x=768, 128 px wide, various heights (the sprite/object atlas column). Uploaded with `LoadImage` and
freed. Rendering shows scrambled rectangular pieces, i.e. a tile/chip atlas that the layer system (type 0)
composes, not a ready picture: `valhalla_type2_bg.png` (scene 0x16, 384x512, own CLUT),
`scene0x1f_type2_strips_composite.png` (three bands of scene 0x1F joined).

**Type 3: TIM, mostly 4bpp with a 16-colour CLUT row at y=504 (high).** 290 in scene/shared packages: flags
`0x8` (4bpp + CLUT) 238, `0x9` (8bpp + CLUT) 52. Image at VRAM x=320 (y 256/384; also 768, 704, 512), CLUT row `(0|16|...,
504)`. Examples decoded: Valhalla (scene 0x16) part 1 = feather/petal sprites 256x72 (the particles of its type-18
effect code, plausible), part 2 = the **staff-credits bitmap** ("Character Graphics Map Graphics / Music Composer /
Motoi Sakuraba ..." 256x256): `valhalla_type3_part1.png`, `valhalla_type3_part2.png`. In the group packages (4735+)
type 3 is a 4bpp TIM without CLUT at VRAM (832, 0): the automap line art (`group4745_type3_tileset_grey.png`).

**Type 4: text container (high, from the earlier work).** SLZ entry 0 = scene block (script + text), entry 1 = font.

**Type 12: raw VRAM block (high).** `u32 tag (0x300), RECT{x,y,w,h}, pixels`: `0x8c` = 4+8+128 = 16x4 halfwords,
`0x2c` = 16x1, `0x20c` = 16x16 halfwords. `LoadImage` target: CLUT rows `x=0x100, y=0x1F0..0x1F5` (the CLUT bank) and
`(0, 510)`. Only in the shared packages (7 per variant): the common 16-colour palettes.

**Type 13: sprite texture set (CLUTs + 4bpp tile pixels), paired with a type 8 (medium-high).** Header
`u16 id, u16 ?, u16 nCLUT, u16 pixel offset, nCLUT x {u16 byte size, u16 offset}`, then the CLUT data (16 colours x
2 B each), then 4-bpp pixels. `0x8006E4B4` finds free 16-colour slots in the CLUT bank (VRAM y 496..504, one bit per
slot in `0x8007FD50`), uploads each CLUT there and records its position in the table entry; the pixel data stays in RAM.
The sprite drawer `0x80039D50` indexes the entry (`lhu 6(data)` = base, then a per-block offset table) and a VRAM tile cache (`[0x8007F268]`), so the tiles appear to be streamed to VRAM on demand (read, not traced).
Pairing: **every type-13 id has a type 8 with the same id in the same package (1,118/1,118)**; 169 scenes
also have type-8 ids with no type 13 of their own (they use shared pixels). Plain 4-bpp rendering of the first
type-13 part of scene 0x16 (id 0x233C) with its own CLUT shows a recognisable standing character:
`valhalla_type13_raw4bpp_w32.png` (the sheet is not a plain raster; other frames look like noise at that width).

**Type 8: sprite animation / frame tables (high on role, medium on layout).** `u32 total length, u16 id (= the type-13
id), u16 nAnim, nAnim x {u32 offset, u32 frame count}`, then frames = `{u16 length, ...part records}` (sprite parts:
u/v, 30x30 sizes, offsets). Consumer: actor code `0x80039A1C` (lookup of the table by id: `lhu +4`; sets actor
`+0xBC` = id, `+0xCC` = table) and `0x80039A98` (frame pointer: `lw 8(table+8*anim)`, then walks `lh 0(frame)` sizes);
the actor commands (probably set_anim 0x71, A1 set_prop) select the table and animation. Shared packages carry the common ones (e.g. ids 2/3/4, 0xFAC/0xFAD,
0x7FFF/0x7FFE).

**Type 16: polygon models (medium).** `u16 id, u16 1, u32 0, 4 offsets..., triangle table (8-byte entries: 3 vertex
indices, id), vertex table (s16 x,y,z,pad: 13 vertices), face normals (8 B each)`. Four distinct models: id 0 (492 B,
13 vertices, 22 triangles), id 1 (668 B), id 2 (172 B), all in the shared packages (and copies), id 3 (384 B) in 24 scenes
(ids 0x1FB-0x213 region). Consumers: the message-box constructor (`0x80053030..0x800530C4`: stores the entry whose
first halfword is 0 at `box+0` and 1 at `box+4`) and the actor property handler `0x8003ACD0` (`lh 0(entry) == id`, stored at actor
`+0xC8`, id at `+0xBC`; set_prop A1). So the models draw the message window frames and can be attached to actors.
The "message window" reading is inferred from the two lookups, not seen on screen.

**Type 0: layer/background table (container high, field semantics medium-low).** `u32 hdr[7]` (here `2, 0x1C, 0x444,
0x35C, 0x444, 0x13A4, 0x808080`; the loader writes `0x80` into the three bytes at `+0x18` = clear colour), then
`(n+7)` 20-byte layer items from `+0x1C` (n = `u16` at `+0x24`, e.g. 9 -> 16 items): `u16 w, u16 h, s16, s16 (0x100 = 1.0 in most items: scroll
factors X/Y, guess), u16, u8, u8 (depth/priority?, guess), s32 off A, s32 off B` (offsets from `hdr[1]`/`hdr[4]`,
-1 = none). Items 1-4 feed globals (`0x8007FD42/FD00/FD44/FD46`), the last one sets the screen size globals
`0x8007FC74-7C`. Then: per-layer lists of small rect/sprite records, a table of 34 tagged image chunks
(`u32 tag, u16 w, u16 h, u32 offset`, data = 8-bit pixels, RAM-resident BG pieces) and a table of 16-byte animated
chunk records (3 bytes per record are rewritten at runtime). Consumers: `[0x8007F100]` is referenced from >= 40
sites (layer drawing/scroll; e.g. `0x80039AE8` returns record `i`, `0x80055B74` the camera layer `n+6`). Not reversed in
detail.

**Type 17: automap cell grid (high).** Raw: `u16 index (= scene table +6), u16 0, u16 cell width (160 typ.), u16 cell
height (120 typ.), u16 columns, u16 rows, columns*rows x s16 room number (-1 = none)`. 923 scenes. Consumer `0x80055AF4`
(per frame, guard: `[0x8007F118] != 0` and scene-table `+2 != 0`): from the camera layer position it walks the
on-screen cells and sets bit `(room + table[+4])` in the byte bitfield at `obj+0xAF2` (`[0x8007F1EC]+2802`; 1,176 bytes cover all
9,401 bit indices used). So the grid divides the background into 160x120 cells and records **which rooms have been visited**
(fog-of-war for the dungeon map). Section 3 verifies it on all scenes.

**Type 18: scene code overlay (high).** Raw MIPS, 61 distinct blobs for 377 scenes (one blob in 100 scenes, others in 32, 30...).
Copied to `0x800899B0`; the field engine calls `0x800899B0` once at scene setup (`0x8006D92C`, flag `0x100000`); the entry
creates tasks (`0x8002FC18`) whose functions live in the overlay (Valhalla's creates 8 tasks with positions 0,50,100,...,
then more tasks; I believe the feather effect, guess; scene 0x30A's blob does `scene id - 778` arithmetic = a per-room logic). A second entry `0x80089C28` is used by a
built-in that loads such code from a file (`0x800792B0`).

**Type 22: movie-player module (high).** Raw 78,812 bytes, identical in all 64 scenes that use movies; contains the libpress
strings `MDEC_rest:bad option(%d)`, `MDEC_in_sync`, `MDEC_out_sync`. Loaded to `0x800BEA00`, flag `0x40000000`.
What it plays: the movie built-ins' first inline word (e.g. `sys_c4 0xf 0xdfa 0x00f00140`) is a **file-table entry index**
(0xDB3..0xDFA = 3507..3578); every one of those entries starts with the STR video header `0x80010160` (e.g. entry 3578: 2,260 sectors, 320x240;
3525: 160x... small ones) so each movie is its own entry. (Header word 8 is another STR area, see section 2.)

**Type 23: sound bank / one-shot sample (format high, consumer partial).** Raw: `0x1C-byte header (u32 0x1C, 0x80000004,
0x14, ...), u32 size (= vag size + 60) @0x1C, tone record(s), SPU-ADPCM at 0x68 (or later), vag size (u32 at 0x38) bytes`.
Verified on all 42: every 16-byte block from `0x68` has a valid filter/shift/flag header, with exactly one end-flag block
(flag 1) at the end. Same format as the standalone sound files that built-in **0xD3** loads through task creator `0x8005A014`
(file index = operand, e.g. 0x937 = entry 2359 starts with the same `1C 00 00 00 04 00 00 80 14 00 00 00` header) and as
file-table entry 4 (a multi-tone bank, 32-byte tone records with `00 7F 00 7F` note ranges). The loader keeps up to 8
buffers in `0x8007F228`; `0x8006DD94` makes one task per buffer (task function `0x8005A098`, state 2, data at `task+8`, handle byte
`+15 = 0xFF`) which polls the sound engine (`0x8001A100`). Which call actually uploads to SPU RAM and which scenes play which
slot was not traced.

---------------------------------------------------------------------------------------------------

## 2. Archive header words 0-8 (and the rest of the table)

Source: `cd_subsystem_init` (EXE `0x80011D38`, object `a0`). It sets `obj+4 = 0x400` (placeholder), starts a read
of 26,624 bytes (13 sectors) into a malloc buffer, copies **6,400 words** (`0x1900`) to `obj+4..`, then in one loop
(`0x80011DFC-0x80011E34`) decrypts **words 0..5119** with the key stream `key = ~(key ^ (key<<1)) ^ 0x64283921`
(start `0x64283921`; word 0 gives the magic `0x19563824`) **and, with the same loop, the 5,120 bytes at `obj+0x5004`
(= words 0x1400-0x18FF) bytewise: byte ^= low byte of `(key ^ (key<<1))` (the value *before* the `nor`, `0x80011E10` to
`0x80011E24`), not of the key itself.** (`vp_scenes.directory` XORs those words with the continued word key, which is
wrong for them but irrelevant for the directory.)

**Every header word n >= 1 is a file-table entry, not a count or an offset of something else:**

* bytes 0-2 = BCD disc position `MM SS FF` (sector = `MSF - 300` = VALKYRIE.BIN sector, as `vp_scenes.sector`);
* byte 3 = high byte of the **sector count**; the low byte is **byte n of the byte table** (the words 0x1400..0x18FF region);
* size in bytes = `((word>>24)<<8 | bytetable[n]) << 11`. This is `func_80011CA0(obj, n)` (`0x80011CA0-0x80011CC0`:
  `lhu 6(entry) & 0xFF00 | lbu 0x5004(obj+n)`, `sll 11`) and what `func_8001175C(obj, n)` stores as the read length
  (`0x80011800-0x80011824`; it copies the 4 BCD bytes as the Setloc parameter);
* word 0 = the magic (its "size" field reads `0x1900` = the table length in words, probably a coincidence of the magic);
* **verified on the disc: for all 4,472 non-empty entries n = 1..4859, the file starts exactly where entry n-1 ended (zero
  gaps), the last (4859) ends at sector 296,765; VALKYRIE.BIN has 310,265 sectors, the remaining 13,500 are unindexed
  (no `0x80010160` markers).** Empty slots (word 0, size 0): 3, 2646-3129 (sparse), 3581-3603.

So the earlier "next word gives the size" is true only because the table is contiguous; the real size is in the entry.
Words 1-8 (everything before the 503-entry block at 9..511 that `bin_navigator.py` shows):

| n | sector | sectors | content (decoded) | who reads it |
|---:|---:|---:|---|---|
| 1 | 13 | 58 | SLZ2 code overlay, 274,888 B, `SLUSP01156`, `%d:%02d:%02d`: the **boot/title/menu module**, loaded to `0x8002F824` by the boot code `0x8001071C` (`li a1,1`) | EXE boot `0x80010718` |
| 2 | 71 | 1 | Shift-JIS text "ヴァルキリープロファイル / これはディスク１です。(This is disc 1) / 心の痛みを知らぬ者め!! by ﾍﾞﾘﾅｽ": the **disc-1 identification file** | disc check (not traced) |
| 3 | - | 0 | unused (word is 0) | - |
| 4 | 72 | 42 | sound bank: u32 0x14F90 (its own length) + 32-byte tone records + ADPCM (menu SE) | loaded by module 1 at `0x8004D84C` (`li a2,4`) |
| 5 | 114 | 5 | SLZ1, 13,256 B: `u32 0x283C, u16 offsets (0x1A8, 0x1A8, 0x1B4, ...)` = a text/string index table + data (menu text: guess) | module 1 `0x8004D8B8` (`li a2,5`) |
| 6 | 119 | 127 | a **package** (count 52, hsize 0x1A8): 46 type-3 TIMs, 1 each of types 4, 7, 12, 19, 20, 23 (title/menu graphics, text, font, sound) | module 1 `0x8003CC00` (`li a2,6`) |
| 7 | 246 | 9 | SLZ2 -> one 4bpp TIM (VRAM (896,256) 256x256, CLUT (16,504) x3) | module 1 `0x8003BDC4` (`li a2,7`) |
| 8 | 255 | 11,760 | a contiguous **STR video area**: every sector starts `0x80010160` (MDEC video sector header, 320x240; 24 MB). Not the entries the movie built-ins name (3507-3578); probably the long cut-scene movies (guess) | not found in the field engine |
| 9.. | 12,015 | ... | the file table proper (entry 9 follows entry 8 exactly) | many |

(Callers were found by scanning the decompressed module 1 for `jal 0x80011BC4 / 0x8001175C` with `li` arguments: `modscan.py`.)
Other code modules in the table (all SLZ, all start `addiu sp,sp,-0x18` and load at `0x8002F824`): entries 1490 (336,596 B =
`overlay_engine_8002F824.bin`), 2174, **2292 (369,036 B = the field engine: 325,699 of the first 327,680 bytes equal `ram_interp.bin` at `0x8002F824`; the rest is runtime-patched)**, 4796, 4806; entry 4733 is
the map-screen overlay (section 3).

**Guesses:** "7/5 are title screen image and menu text" and the exact role of entry 4 (sound bank for the menus) are inferred from
format only; entries 2, 4-7 were not traced to a screen.

---------------------------------------------------------------------------------------------------

## 3. The scene table at `0x80080618` (10 bytes per scene, 1,118 entries)

Layout (all s16; read from RAM and cross-checked against the disc, `scenetab.py`):

| field | meaning | evidence |
|---|---|---|
| +0 | directory word of the scene's package = `3610 + id` (all 1,118) | `0x8006C3F8/0x8006C270` -> `obj+0x10`, `0x8006B480/0x8006CFA0` -> `obj+0x312` |
| +2 | **map group package** word (4735..4765, 30 distinct values referenced, 0 for ids < 0x7D); non-zero for 923 scenes | `0x800556F0`, `0x80055858`, `0x80055B30` |
| +4 | **base bit index** into the explored-rooms bitfield at `obj+0xAF2` | `0x80055CEC` |
| +6 | **map index** inside the group (the `u16` at the start of the scene's type-17 grid; the group's map table is indexed by it) | data; **no reader in the field engine** |
| +8 | shared package word 3605 (948 scenes), 3606 (156), 3607 (12), 3608 (2) | `0x8006C2A8`, `0x8006B280/0x8006B294` |

**+8 (shared package) and the duplicate copies.** The shared packages 3604-3608 are duplicated byte for byte at 4728-4732
(`+1124`; checked, `identical bytes: True`). `0x8006B228` returns `table[+8]` or `table[+8] + 1124` depending on whether the scene's
directory word is closer to 3604 or to 4728 (`|w-3604|` vs `|w-4728|`, boundary: word >= 4167, id >= 0x22D): the loader uses the copy that is
physically near the scene package (a seek optimisation); `0x8006C640` subtracts 1124 again (`sltiu w,3610`) when it compares with the already loaded
shared package. Package 3604 (15 sectors, no type 13) is never referenced by +8; 3609 (type 8 + 13 only) is loaded by `0x80060618`
(`li a1,3609`). Scene id **0x800** is special-cased in `0x8006C3C0` (`dir word = 4797`, flag `0x800`).

**+2, +4, +6: the dungeon/area auto-map.**
* `0x80055650` (per frame): if a button mask from `0x8007E622` is set in `obj+0x322`, no blocking flag (`0x8000`, `0x8441C`) is set and
  `table[id].+2 != 0`, it fades out, creates a task (`0x8005577C`) and, in its state 0, sets `obj+8 = table[id].+2` (the group word;
  **4735 is replaced by 4760 while the byte `obj+0x3BF` is 0**) and reads that package into `obj+0` (size from the directory, read via
  `0x8001175C/0x8001190C`). State 2 sets `obj+0x39A = 6` (mode 6) and finally loads **file 4733** (SLZ2, 88,092 B, an overlay containing libgte
  `Can't push matrix` strings) and starts it with `0x800105B0` (the boot routine that loads an overlay); `obj+0x14` holds **package 4734** (type 3 TIM, type 12
  CLUT, type 4 text). So 4733 = the map-screen code, 4734 = its shared graphics/text, the group package = the map data. (Confidence: medium-high; the map
  module itself was not run.)
* the group package is `[type 17 (SLZ1), type 3 (SLZ, 4bpp TIM, VRAM (832,0), 256 px wide, 72-184 rows, no CLUT)]`, 2-12 sectors. Decoded type 17: `u32 number of maps` (17 for 4735,
  21, 18, ...), `u32 0x5000E8`, `u32 0x90070`, `u32 0x2EEC`, then N per-map offsets (`0x54, 0x358, 0x770, ...`) and the map data (not decoded). The scenes' +6 values are
  exactly `0..N-1` in 23 of the 30 groups; in 4750-4752 (37 maps) and 4763 (43 maps) only some maps are used (25 / 37); in 4737, 4753 and 4765 some scenes carry an index
  >= N (18 for 4737; 35 and 36 for the two 35-map groups, where indices 25 and 33 are unused), so the "index into the group's map table" reading is solid for the
  23 exact groups and only approximate for the other 7. Type 3 is the line-art room-outline tileset (`group4745_type3_tileset_grey.png`). In map mode 6 the loader puts these in
  `[0x8007F118]` (type 17, copied as stored, i.e. still compressed) and VRAM.
* the per-scene **type-17 grid** (section 1.3) is what makes the connection: **for all 923 scenes with `+2 != 0`, the scene package has a type-17 part, and for all
  of them (and no other scene)** the grid's leading u16 equals `+6` (922 of 923; the one exception is scene 0x182: +6 = 18, grid index 3). Within each group
  (scenes sorted by `+4`) `+4[i] + (max room number + 1) <= +4[i+1]` holds for **all 869 adjacent pairs** (no overlap), i.e. `+4` is the running start of a scene's block
  of room bits (`0x80055CF4-0x80055D1C`: `bit = room + [+4]; byte[obj+0xAF2 + bit>>3] |= 1 << (bit&7)`). The largest bit used is 9,400, so the bitfield is 1,176 bytes
  (saved with the game, probably: guess).
* examples: id 0x7E = (3736, 4735, 16, 1, 3605): package 3736, group 4735, rooms start at bit 16, map #1 in the group, shared package 3605. Group 4735 has 17 maps for
  19 scenes (scenes 0x86/0x8E and 0x8A/0x8F share a map: identical +4/+6).
* groups 4750/4751/4752 (and 4753/4765) have identical +4/+6 values and the same type-17 length: variants of one map (guess: different story states).

**Not found / guesses:** nothing reads `+6` through an absolute address: I scanned all seven code modules in the table (entries 1, 1490, 2174, 2292 = field engine,
4733, 4796, 4806) for loads of `table+0..+8` after a `lui 0x8008` and found only the field engine's 14 sites listed above (none for +6, none in 4733). So +6 is either
consumed through a pointer (map module walking the table) or unused; "map index" rests on the data match with the type-17 grid and the group's map count. The layout
inside the group's type 17 beyond the map count and offset table is not decoded.

---------------------------------------------------------------------------------------------------

## 4. Confidence summary

Solid: the dispatch table and every case's destination (code + live trace); types 2, 3 (TIM), 12 (raw VRAM block), 17 (automap grid, verified against the scene table on all scenes),
18 (code overlay, live), 22 (MDEC module), 23 (SPU-ADPCM bank format; every block valid), 4; the pairing of type 13 with type 8 by id; the header words (BCD + byte-table size, contiguity
of all 4,472 entries, entry 1/2/6/7/8 identification); the shared-package duplicate at +1124 and the +8 selection rule; +2/+4 of the scene table (code reads) and +6 (data).

Medium: type 8/13 internal layout and how the sprite drawer streams tiles; type 16 as polygon models used for the message windows/actors; type 0 field semantics; the map-screen interpretation
(4733/4734/group) - the consumer code was read but the map module was not run; entry 4/5 roles.

Guess: type 19 = font sheet, type 20 unknown; which sound code uploads type 23 to SPU RAM; that the bitfield is saved.

---------------------------------------------------------------------------------------------------

## 5. Files in this directory

* PNGs: `valhalla_type2_bg.png`, `scene0x1f_type2_strips_composite.png` (type 2 atlases), `valhalla_type3_part1.png`, `valhalla_type3_part2.png` (type 3: feathers,
  staff credits), `valhalla_type13_raw4bpp_w32.png/w64/w128` (type 13 raw pixels), `group4735_type3_tileset_grey.png`, `group4745_type3_tileset_grey.png` (automap line art, no CLUT: grey).
* Live traces: `loader.dbg/.out` (frames 6440-6600), `loader0.dbg/.out` (from frame 0: scene, shared package and scene dispatches, parse with `parse_loader.py`), `t18.dbg/.out` (type-18 copy and call).
* Disassembly dumps: `f6B6C4.txt` (the part dispatcher, 400 words from `0x8006B6C4`), `loader_B2A8.txt` (`0x8006B2A8..`, package walking and the scene-change flow).
* Data: `type_stats.json` (per group/type counts and sizes, including group and shared packages), plus the scripts listed at the top.
