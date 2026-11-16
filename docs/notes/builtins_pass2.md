# Built-in naming follow-up (agent a): notes

Result file: `names_update.json` (29 entries: the 27 low-confidence ops, plus 0xC9 and 0xE0). Old -> new
confidence: 5 high, 17 medium, 7 low (was 27 low, 2 medium). Everything below was either read from the
code in `ram_interp.bin` / `ram6440.bin` / `ram_newgame.bin` or run live in TinyPSX; each place says which.

Live runs (all in `live/`, scripts `run*.dbg`, outputs `run*.out`, screenshots `*.png`):
* A: breakpoints on the handlers of 0xAB / 0x8D, on 0x8005A74C (voice loader) and 0x8003E204 (sound start).
* E2/E3/E4/E5: scene 0x1B (Valhalla hall) reached in about 600 frames by patching the running
  scene-0x16 script (see "Method" below), then holding LEFT into Frei's zone: breakpoints on 0xA8 / 0xAB / 0xE0,
  screenshots (`e_hit*.png`), and the 0xE0 experiments.
* G2: 0x8D handler and the sound-start call it makes. H/I: write watch on ctx+0xA4.

## 0xC9 sub-operations (handler 0x8006F8A0, jump table 0x8007E0A4)

Script usage counts include 2 library uses (`func_09E7`, stack form). ctx = [0x8007F1EC] = 0x801F5EDC.

| sub | writes | scripts | what it is |
|---:|---|---:|---|
| 0 | halfword [0x8007FD68] | 12 | a **BGM directory word** (script values 2249..2282 = 0x8C9..0x8EA, the same id space as `play_bgm`; scene-reset default 0x8C4). The preload task (0x800434D4 / 0x80043C4C) loads that file into a buffer when it differs from ctx+0x38C (the id 0xBB last loaded). Library `func_09E7(battle, bgm, bgm2, flag)` sets it and sub 1 right before `start_battle`, so it is the battle music preload. |
| 1 | ctx+0x38E (halfword, default -1) | 3 | a second music id for the same preloader (values 0, 0x8E0). When it is -1 the preloader does the 0xDF latch (ctx+0x18 -> sound engine +0x1C, +5 = 1). Medium-low. |
| 2 | ctx+0x398 bit 0x0002 | 2 | flag; no reader in the field overlay (battle module?). |
| 3 | ctx+0x38A + starts task 0x80043C4C, sets ctx+0x398 \|= 0x210 | 845 (49 ids, 0x59E..0x5CE) | **scene tile/graphics package**: package id (directory word). The task loads it (with the fixed packages 1487 and 1488/1489) into ctx+4 / +0xC / +0x10 and frees the old one when the id changes. I decompressed 0x59E and 0x5C9: 5-part package = small table, an SLZ block that decompresses to **MIPS code** (jal targets 0x80081D8C, 0x80082368, 0x80060044, 0x80021C80 = field engine functions), a ~200 KB 8bpp TIM (wood planks, bookshelves: interior tile textures, see `bg_59e.png`) and a 4bpp TIM. |
| 4 | halfword [0x8007FD6A] | 0 | alternative id compared with ctx+0x38A by the preloader; never used. |
| 5 | byte ctx+0xFF7 | 0 | never used, no reader found. |
| 6 | ctx+0x398 bit 0x0200 | 156 (all value 1) | flag set at the start of dungeon scenes (`play_bgm 0x8B3; c9 6 1; set_footstep_sfx; register_trigger_zone...`); no reader in the field overlay. |
| 7 | byte [0x8007FD82] | 0 | never used; cleared at scene reset. |
| 8 | ctx+0x398 bit 0x0100 | 27 | flag, written in the cluster before `start_battle`; no reader in the field overlay. |
| 9 | byte [0x8007FD89] (values 1, 3) | 13 | **battle start mode**, read inside `start_battle` (0x8004227C, 0x800423E8): clears ctx+0x398 bits 4/8 and sets one by this value (0 = decide from the situation, 2 -> bit 4, 3 -> bit 8, 1 = none). |
| 10 | ctx+0x398 bit 0x1000 | 14 | flag, same cluster; no reader in the field overlay. |
| 11 | ctx+0x398 bit 0x2000 | 3 | flag; no reader in the field overlay. |

Summary: {0, 1, 2, 8, 9, 10, 11} are battle parameters (they appear together just before `start_battle` and
are never read by the field engine, except 9 inside start_battle itself and 0/1 by the music preloader);
3 (and 4) select the scene's graphics/code package; 6 is a dungeon-scene flag; 5 and 7 are unused. The built-in
keeps the name `engine_set_param`; if a finer split is wanted: `set_battle_bgm` (0), `set_scene_tile_package`
(3), `set_battle_start_mode` (9). The meaning of the 0x398 flag bits is not established (the readers are in
another module).

## 0xE0 (set_msgbox_flag -> set_msgbox_portrait_flip)

* Handler sets/clears bit 27 of [0x8007F260]. Used by library `func_033C` (message placement): anchors 0 and 3
  `e0 1`, 1 and 2 `e0 0`, anchors 8-15 (actor-relative) pick it from the actor's facing byte (+0xF6).
* Reader: the text renderer `0x8004BF6C` at 0x8004C860 copies that bit into message-box flag bit 0 (box+0x338)
  when a line slot is armed with mode value -1. Bit 0 is tested in the portrait drawing routine (0x8004E6F4;
  the picture buffer at box+0x32C is what 0xA8 loaded): **set = quads with x = width - tile x (mirrored), clear =
  plain rectangles**.
* Live (Frei's first line in scene 0x1B; box at 0x800FCDB8, flags 0x08220001): changing the e0 value at the call
  changed nothing (0 differing pixels after 90 frames); clearing box bit 0 by hand changed nothing; setting it
  again moved Frei's portrait from bottom-left to bottom-right and mirrored it (`montage_e5.png` in `live/`).
  So the bit is the portrait side/mirror flag, but I could not reproduce the visual change through 0xE0 itself;
  the renderer apparently samples it only when a line slot is (re)armed. Medium.

## 0xEB and ctx+0xA4

* `ctx` (0x801F5EDC) is **not just a script context**: it is the persistent game-state block. The New Game
  initialisation (title/new-game module, frame 2055, code at 0x8003C770..0x8003C830 in `ram_newgame.bin`)
  sets ctx+0xA4 = 2000 and ctx+0xA8 = 0, ctx+0x3BE = 2, clears the 1,176 bytes at ctx+0xAF2 (the map-exploration
  bitmap, see 0xEC), 4 bytes at +0xFF2, 192 bytes at +0x1004, sets the party slots ctx+0x39C = {1,0,0,0} and
  initialises the 25 roster records. (Live: `watch 0x801f5f80 if value == 0x7d0` stopped at 0x8003C77C.)
* Writers of ctx+0xA4 from scripts: library `func_030A` (party member joins: += G0x68.b * 300, except roster
  entries 3 and 4) and one `eb 0 0x4E20` (+20000) in scene 0x447 (Seraphic Gate), right after its caption.
* **Nobody reads it in the field engine** (no instruction loads offset 0xA4 from the ctx pointer; the loads at
  0xA4 in the overlay are actor fields) and a read watch over frames 2055..9000 (title, intro, Valhalla credits)
  found no reader. The consumer must be another module (menu, shop or battle result), which is not in any RAM
  image I have. Guess (unconfirmed): a currency or points total (initial value 2000).

## Other findings worth knowing

1. **Resource id spaces** (directory words, all verified by decoding the data): 0x59E-0x5CE scene tile/code
   packages (0x5CF-0x5D1 fixed), BGM sequences 0x8A4-0x8F3 (`play_bgm`, c9 sub 0), event illustrations 0x8F8-0x902
   (`a5`), voice clips 0x912-~0xBB8 (`ab`), sound sequences 0xDB3-0xDFD (`d3`), face portraits 0xCE3-0xD98 (`a8`).
2. **Library wrapper `func_0331(voice_id or -1, face_id or -1, wait_frames)`** (via `func_031F`) = `ab` + `a8` + wait;
   scenes call it before almost every spoken line (6,500 calls to 0x331/0x31F in the corpus). Voice ids 2326..3000,
   face ids 3299..3480. In scene 0x1B the first Frei line is `func_0331(0x950, 0xD7D, 0x50)`: 0xD7D is Frei's portrait
   (112x136, a 16x16-tile table plus an 8bpp TIM; I rebuilt it: `img_d7d.png`), 0xD39 a young man (`img_d39.png`).
3. **Text decoder bug: command 17 (auto-advance) has a 16-bit argument**, not 1 byte (`vp_scene_text.py`
   `ARG_BYTES[17] = 1`). The renderer's handler reads two bytes (signed halfword; 0x8004C638). In the data:
   702 uses, in 72 of them the high byte or the following bytes are not 0 (`2c 01` = 300 frames; `78 00 80 80 92 80` =
   120 frames, newline, start of speech...). The decoder takes the high byte 0x00 as the end of the record, so
   **the rest of the line after the auto-advance command is dropped** (or, for `2c 01`, glyph 0x01 is emitted).
   Examples: scene 0x9 records 16/19/20/22/26, 0x11 rec 22/27, 0x14 rec 14, 0x18 rec 24. Setting ARG_BYTES[17] = 2 is
   the fix; the doc's "0 bad commands" check cannot see this because the 0x00 looks like a terminator.
4. **0x8D / 0xBB**: 0xBB loads a music file, 0x8D starts it (with an optional fade-in), 0x8E fades out. The old
   name `bgm_fade_in` for 0x8D was right about the operand but not the role.
5. **0xAB is not a "CD effect"**: the sin/cos in the old dossier came from a different callee. The same loader is
   called by the text command 12 (u16 cue), so voiced lines can start their clip from the text as well as from the script.
6. **0xA5/0xA6/0x79 are a trio** (create / fade / wait) that use hooks at [0x8007F248/4C/50] which the field
   engine leaves NULL; in the ram6440 snapshot they point at code in the scene package (0x8008A16C / 0x8008A284 /
   0x8008A3CC). The 0xA5 pictures are full-screen story illustrations (`img_8f8.png`, `img_8fa.png`).
7. **[0x8007F1E4] is the 25-record character/party roster (236 bytes each)**, not the actor table: `b2`, `e1`,
   `e2`, `e3`, `e4`, `ea` all work on it (byte +217 = 0 absent / 1 acquired / 2 in an active slot). The earlier names
   `set_active_actors` / `reset_active_actor_motion` for e1/ea say "actors"; "party members" is more accurate.
8. **Reader of F260 bit 27 (0xE0) and of ctx+0xAF2 (0xEC) are both in the text renderer / per-frame map code that
   I could read; neither is the final consumer** (portrait drawing is; the exploration bitmap's consumer is not
   found).
9. Scene table fields +2/+4/+6 of 0x80080618 (open item in the main doc): the per-frame routine 0x80055AF4 uses
   field +2 (non-zero = dungeon map present), field +4 (offset added to the cell tile index) when setting bits in the
   exploration bitmap at ctx+0xAF2, and ids >= 0x7D are the ones with non-zero values, so they are dungeon-map
   bitmap parameters (consistent with the doc's guess).
10. 0xE8's gauge: ctx+0x388 is a 0..100 bar the HUD routine at 0x800601C8 fills by 1/frame and drains by 5/frame
    (bar drawn at (58,32)); that routine also adds G0x68.b*500+100 to G[0x20] (capped at 999,999,999). Unused by scripts.

## Method note (live scene jump)

To reach scene 0x1B without playing 13,000 frames, at the first `0x8D` hit in Valhalla (frame 6592 then; ~6440 with the current build, the second hit overall), inside the scene-0x16
intro function) the script words from index 0xA5F on were overwritten with
`9E00001B 0 | 87000000 0000B401 | 7C000000 | 9F000000 00B20244 0 | 17000000` (select scene 0x1B, fade out, wait,
change scene with the arrival position the real flow uses). The hall then runs normally; hold LEFT from frame ~6992
to reach Frei's zone (first `a8` at 7055). Scripts: `live/runE2.dbg`.
Note: from frame 12,960 the unpatched replay is still in the opening credits (scene 0x16/0x15/0x17 with the staff
roll), not in scene 0x1B; the "frame ~12,950 = scene 0x1B" statement in the task text needs button presses.

## Update 2026-11-12: 0xE0 settled

The bit only matters on lines whose text command 19 is `ffff`; `0000`/`0100` set the portrait side
themselves, which is why the test above (Frei's `<19:0100>` line) showed nothing. With that line changed to
`<19:ffff>`, `e0 1` puts the portrait bottom left and `e0 0` bottom right, mirrored. Renamed
`set_portrait_default_side`, high confidence. Details in `docs/VP_DIALOGUE_LOAD_PATH.md` (Test of built-in 0xE0).

## Update 2026-11-12: the 7 low-confidence built-ins

Run live by three follow-up agents (scratchpad `bi/d8df`, `bi/ebe8`, `bi/actor`). Results and new names are in
`vp_builtins.json` and the table "Built-ins settled by live tests" in `docs/VP_DIALOGUE_LOAD_PATH.md`: 0xD8 confirmed
(battle return scene), 0xDF `bgm_save_state`, 0xE4 `set_character_weapon`, 0xE8 `add_evaluation_level`,
0xEA `restore_party_after_battle`, 0xEB `add_materialize_points`; 0xD1 stays low (its second byte has no reader).
Correction: ctx+0x18 is not zero; it points at the 2,144-byte buffer 0xDF fills.

## Update 2026-11-12: 0xEA HP and status seen on screen

Camp-menu test (scratchpad `bi/eahp`): Lenneth's roster record (0x801DFE9C) +172 set to 123 shows `DME 123 / 1000`
in red; with the real `ea` patched in before the scene change it shows `1000 / 1000`. +214 = 0xFF shows status-ailment
icons on her panel; after `ea` the field is 0 and the icons are gone. 0xEA is now high confidence:
+172 current HP, +176 max HP, +214 status ailments. Proof: `docs/notes/edit_proof/builtin_ea_hp_status.png`.
