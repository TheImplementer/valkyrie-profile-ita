# Text command 12 (u16 cue) -> `0x8005A74C`: a voice clip

**Answer.** It is a **voice clip**, not a sound effect and not streamed XA. The u16 cue is an **index into the
archive's package directory** (the same table that gives scene packages their position: RAM `0x80090024 + 4*cue`,
BCD MM SS FF). Each cue names one small file on the disc: a 0x48-byte header plus raw **SPU-ADPCM speech**.
The game reads that file into a heap buffer with `Setloc` + `ReadN` (plain data sectors, DMA3 to RAM) and plays it
through the sound engine on the SPU. No CD-ROM XA command is issued: no `SetFilter` (0x0D), no `ReadS` (0x1B), no
`Play`, no mode change in the traced window.

Sources: RAM image `ram_interp.bin` (`rdis.py`), the 258 command-12 records of `vp_scene_text.py --all --raw`, and
live TinyPSX traces (scripts `v12b.dbg`, `v12c.dbg`, `v12d.dbg`, `v12e.dbg`, `v12f.dbg` in this directory, with
`v12.replay` = `vp_newgame.replay` + Cross every 45 frames from frame 6300 + LEFT from 12950).

## 1. How the command reaches the function

* Renderer dispatch: `0x8004C23C-0x8004C248` computes `cmd - 2`, so jump-table `0x8007DC6C` is indexed by
  **cmd - 2**. Entry 10 = `0x8004C8C8` = command 12: it reads two bytes (`lbu`, `lbu << 8`, `or`) and does
  `jal 0x8005A74C` at `0x8004C8E4`. So the cue is the **little-endian u16** (`<12:5109>` = `0x0951`).
* The only other caller is script built-in **0xAB** (handler `0x80070814`: cue from the stack if bit 20 is set,
  else the operand's low 16 bits; `jal` at `0x80070850`). `vp_builtins.json` calls it `start_cd_effect` (low
  confidence). That is wrong: **0xAB is `play_voice`**, same function, same cue space (its 5 scene uses pass
  0x912, 0x913, 0x91C, 0x91D, 0x94F, plus one use in the shared library; live: it was the call at frame 7512, `$ra = 0x80070858`).

## 2. What `0x8005A74C(cue)` does (static)

```
a1 = [0x8007F25C]                       ; game flag word
if ((a1 & 0x18) == 0) return 0          ; game flag bits 0x18 (set in all three RAM images; meaning not identified)
if (hu[ctx+0x326] & hu[0x8007E620]) && (hu[ctx+0x31A] & hu[0x8007E620]) && !(a1 & 0x01000000):
        counter = (counter+1) & 3 ; return 0         ; skipped, but the slot counter still advances (condition not identified)
slot = counter ; counter = (counter+1) & 3           ; counter = byte 0x8007FD85 (the "load" index)
t = 0x8002FC18()                                      ; first free of the 144 task slots (236 bytes each, [0x8007F0E8])
0x8005A5D4(slot)                                      ; kill/clear whatever still sits in this voice slot
t.fn = 0x8005A864 ; t.+0x10 = cue ; t.+0x30 = slot ; byte[0x8007F20D + 8*slot] = 5
```
Four voice slots (table `0x8007F208`, 8 bytes each): `+0` heap buffer, `+4` sequence handle, `+5` flags
(bit 0 = still loading, bit 1 = playing, bit 2 = a clip is queued). Teardown of a scene frees them
(`0x8006D3C0`).

**Task function `0x8005A864`** (state byte at `+4`):
* state 0 (start): needs `[0x8007F25C] & 0x40 == 0`, `[0x8007F260] & 0x20000008 == 0`, CD driver idle
  (`0x80011C90 == 0`) and an empty slot buffer. Then
  `size = 0x80011CA0(dir, cue)` = `((byte[0x80095024+cue]) | (hu[dir+4*cue+6] & 0xFF00)) << 11` (sectors x 2048),
  `buf = malloc(size)` (`0x80013104`) into the slot, `0x8001175C(dir, cue)` = **Setloc** to the BCD position of
  directory word `cue`, `0x8001190C` = start **ReadN**; sets `[0x8007F264] |= 0x100`. State becomes 1.
* state 1 (wait): when the CD driver is idle again: clear `0x100`, clear slot flag bit 0 ("loaded"), task ends.
* states 0xFE/0xFF: abort, free the buffer.

`dir = [0x8007FD90] = 0x80090020`: the decrypted archive header, word `cue` at `dir + 4 + 4*cue`.

## 3. How the line uses the clip

* `<18>` (start of speech, handler `0x8004C8F4`): if slot `[play]` has flag bit 2 the box gets flag
  `0x04000000` (wait for voice); otherwise the play index just advances (`[0x8007FD86]`, mod 4). So `<18>`
  consumes the **next queued clip in order**.
* Each frame (`0x8004D7A0-0x8004D870`): with that flag, once the clip is loaded (bit 0 clear) the box calls
  **`0x8003E204(buf, 0x7F40, 2, 0)`** and stores the returned handle at `slot+4`, sets slot bit 1 and box flag
  `0x08000000`. `0x8003E204` is `0x80019834` (sequence start) with volume scaled by **`ctx+0x3C3`**, a master volume
  of its own: positional SFX (`0x8003E0D0` -> `0x80019B70`) and sequences started by built-in 0xD4 (`0x8003E134` -> `0x80019834`)
  scale by `ctx+0x3C2` (built-in 0xD4 starts its sequence through `0x8003E134`).
* With `0x08000000` set the box polls `0x8001A100(mgr, handle)` (`sequence_is_active`, `0x8004D8AC`). When it
  returns 0, or the box is dismissed, `0x8004DB34` runs: `0x8001A174(mgr, handle)` (stop sequence, see
  `labels_fix.md`), `0x8005A5D4` (free the buffer, clear the slot), advance the play index.

## 4. Live trace (TinyPSX, New Game, scene 0x16 "How nostalgic")

| frame | event |
|---:|---|
| 7512 | `0x8005A74C(0x94F)` from built-in 0xAB (`$ra = 0x80070858`) |
| 7512 | CD `Setloc 20:55:22` (= directory word `0x94F` = `0x00225520`), then `ReadN` (inside the IRQ handler) |
| 7542 | CD `Pause`: the 9 sectors are read. Setloc, ReadN and Pause are the **only** CD commands until the scene change at frame 7908 (`Setloc 35:05:08`) |
| 7512-7542 | DMA3 (CD): `MADR 0x000FE9D0`, 512 words = one sector into the heap buffer `0x800FE9D0` (I stopped after the first) |
| 7632 | message record 14 shown ("How nostalgic") |
| 7658 | `0x8003E204(0x800FE9D0, 0x7F40, 2, 0)` -> `0x80019834(mgr 0x800964B4, buf, 0x7E40, 2)` |
| 7659 | two DMA4 (SPU) slices, `MADR 0x000FEA18` and `0x000FEA98` = buffer + 0x48 and + 0xC8, 32 words each; SPU transfer address ends at `0x70C00` |
| 7663-7741 | SPU voice 11 has `start = loop = 0xE160` (byte `0x70B00`, where the two slices were written), pitch `0x085F` (~23 kHz), volume `0x2BCE`; `IRQ address 0xE170` (byte `0x70B80`); SPUCNT has irq9 on. I take voice 11 to be the clip's stream voice (not seen before 7656) |
| 7738 | `0x8004DB34`, then `0x8001A174(0x800964B4, 8)` (80 frames after the start) |

The clip is 29,596 samples: 1.34 s at 22,050 Hz or 1.28 s at the voice's pitch register (0x085F = 23.1 kHz); the
observed start-to-stop time is 80 frames = 1.33 s, i.e. the clip plays through at roughly 22-23 kHz, in line with
the other SPU-ADPCM regions of the archive (mono, 22,050 Hz per `CLAUDE.md`).

Second voiced scene, 0x1B (Frei's talk zone, `v12b.dbg`; frame / event / argument):

```
14594 voice 0x950   (built-in 0xAB, ra 0x80070858)       80 frames before the line shows
14674 message 14    "Welcome to Valhalla!"
14774 voice 0x951   (command 12, ra 0x8004C8EC)          100 frames after record 14 appeared
15013 message 15    "... you're waiting for someone."
15181 voice 0x952   (command 12)
15472 message 16 ; 15940 voice 0x953 ; 16239 message 17 ; 16412 voice 0x954 ; 16586 message 18 ;
16627 voice 0x955 ; 16916 message 19 ; 17087 voice 0x956 ; 17264 message 20
```
So `<12:cue>` at the **end** of a record **preloads the clip for the next spoken record**; the first line of a
sequence is preloaded by built-in 0xAB before it is shown. The cue numbers are consecutive in story order (0x950 for
record 14, 0x951 for record 15, ...), which is why the cue in a record is "one ahead" of the line that plays.

## 5. The cue and the data

* `cue` -> directory word `W[cue]` (BCD MM SS FF in bytes 0-2; byte 3 = high byte of the sector count, low byte at
  `0x80095024 + cue`) -> `VALKYRIE.BIN` sector = MSF frames - 300 (150 pregap, archive starts at LBA 150; `vp_scenes.sector`). Examples:
  `0x94F` -> 20:55:22, sector 93,847, 9 sectors (18,432 B); `0x950` -> 20:55:31, 11 sectors; `0x951` -> 20:55:42,
  16 sectors; `0x918` -> 25 sectors; range 4-72 sectors for the 136 distinct clips that exist.
* File layout (checked on 0x918, 0x94F, 0x950): word 0 = `0x1C`, word 1 = `0x80000004`, word 2 = `0x14`,
  further sequence/tone parameters, `0xFFFFFFFF` at `+0x44`, then **SPU-ADPCM from `+0x48`** (first block all zero)
  to the end flag; word `+0x1C` is the end of the sample data. It decodes to continuous speech-like audio
  (RMS envelope with bursts of syllable length). The decoded clips are
  `voice_0x94f.wav` ("How nostalgic...", 1.34 s) and `voice_0x950.wav` (1.67 s), 22,050 Hz mono, written with a
  plain ADPCM decoder in `adpcm.py` (the 22,050 Hz rate is assumed); I could not listen to them.
* It is **not** an XA file/channel number and **not** a sound id: sound ids (built-ins 0x8F/0xDE/0xDA) go to
  `0x80019B70` and never touch the CD.

### Which cues exist on this disc

All 258 command-12 records use cues `0x918-0xC3F` (255 distinct). `W[cue]` is non-zero for 136 of them. Every cue
below `0xA55` exists. Above it the directory is sparse (non-zero runs 2722-2819, 2840-2841, 2870-2877, ...):

| scenes | cues | directory entry |
|---|---|---|
| 0x1, 0xC, 0x10, 0x11, 0x1B, 0x32, 0x37, 0x3D, 0x56, 0x63, 0x71, 0x72, 0x74, 0x122, 0x124, 0x45A-0x45C | 0x918-0xB01 | all present |
| 0x1C, 0x54, 0x6E, 0x6F | 0x958-0xC3F | partly (18/27, 8/12, 30/34, 2/6) |
| 0x18, 0x19, 0x1D, 0x38, 0x15C, 0x16E, 0x286, 0x3EC-0x3EE, 0x458, 0x459 | 0xA5A-0xC2F | **zero**: no clip on this disc |

Hypothesis (not verified): the directory is shared by the two discs and an entry is only filled on the disc that
holds the file (the late-story Valhalla scenes 0x18/0x19/0x1D and the 0x3EC+ / 0x458+ scenes are late chapters).
The task code does not test for a zero entry (it would `malloc(0)` and `Setloc 00:00:00`); I did not try what the
game does then.

## 6. Notes for the emulator

* The clip is streamed through a **256-byte ring in SPU RAM (`0x70B00-0x70BFF`)**: two 128-byte DMA4 slices at
  start, SPUCNT irq9 enabled and IRQ address `0xE170` in the middle of the ring. (Inference: the refills are driven
  by that SPU IRQ; I did not trace the trigger.) TinyPSX's SPU never raises IRQ9 (`src/spu.rs`: "IRQA is never
  compared"), and `catch interrupt:spu` over frames 7600-7760 never stopped; the only SPU DMA during the 80
  frames of playback were those two slices. So in TinyPSX voice clips will not get past their first 256 bytes.
  The game logic still ends the clip on time (the sequencer, not the SPU, decides), as the trace shows.
* `vp_builtins.json`: change `0xAB` to `play_voice` (high). `0xD3/0xD4/0xD7` use
  `sequence_start/sequence_stop` of the same engine for non-voice sequences (volume byte `ctx+0x3C2`).
