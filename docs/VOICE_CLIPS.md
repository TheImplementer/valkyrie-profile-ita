# Voice clips

Many of Valkyrie Profile's story lines are spoken. This document explains how a spoken line finds its
recording, where the recordings are on the two discs, and how they're played.

The counts below were checked on both discs. The text side (`<12>` and `<18>`) is in
`docs/SCENE_TEXT_FORMAT.md`, and the script side (built-in `0xAB`) in `docs/EVENT_SCRIPT.md`.

## The short version

* Each recording is a small file in `VALKYRIE.BIN`. A line refers to it by its file number, its
  **cue**.
* The recording is **loaded ahead of time**: the previous line's `<12:cue>`, or the script, asks for
  it.
* The line's **`<18>`** says where in the text the voice starts.
* The sound chip plays it as raw PlayStation sound data (SPU-ADPCM). It isn't CD audio or XA
  streaming: the drive only reads a few sectors into memory.

## The cue is a file number

A cue is a 16-bit number, and it's simply a word number in the file table at the start of
`VALKYRIE.BIN` (`docs/VALKYRIE_BIN_TABLE.md`). Cue `0x94F`, Valkyrie's "How nostalgic・・・", is word
`0x94F`. That word gives the disc position 20:55:22 and a size of 9 sectors.

The game's function `0x8005A74C(cue)` starts the load:

* **A slot:** it picks the next of **four voice slots**, in turn.
* **A task:** it starts a background task that allocates a memory block of the file's size.
* **The read:** the task points the drive at the file with `Setloc` and reads it with `ReadN`.

While the first line of the game plays, the only CD commands are `Setloc 20:55:22`, `ReadN` and
`Pause`.

## Loading one line ahead

Two things call `0x8005A74C`:

* **Text command 12**, `<12:cue>`, placed at the **end** of a record. It preloads the clip for the
  *next* spoken record, so it's ready when that line appears.
* **Script built-in `0xAB`** (`load_voice_clip`), for the first line of a conversation, which has no
  line before it. It's usually called through the library function `func_0331`, which also loads the
  speaker's portrait.

The cue numbers run in story order. In Frei's welcome in Valhalla (scene `0x1B`), cue `0x950` is
loaded by the script before her first line, and that line ends with `<12:5109>`, cue `0x951`, for the
next one. A cue in a record is always one ahead of the line it belongs to.

In a record, `<18>` starts the oldest loaded clip that hasn't played yet. The message box waits until
that clip has finished loading, then hands it to the sound engine. Where `<18>` sits in the text sets
when the voice starts. Moving it after "Welcome to " in Frei's line delayed the voice by 45 frames, about
the 44 frames those 11 characters take to type.

## Two discs, one table

Each disc has its own file table, but the cue numbers are shared. A clip is filled in only on the disc
that has it:

| | disc 1 | disc 2 |
|---|---:|---|
| `<12>` commands in scene text | 297 | 297 |
| different cues, with the 5 used by `0xAB` | 295 | 295 |
| cues with a file on this disc | 148 | 203 |

* **Every cue is somewhere:** each of the 147 cues with no file on disc 1 has one on disc 2. 56 clips
  are on both discs.
* **Disc 1 still has them in its scenes:** disc 1 has the late-story scenes too (Valhalla in later
  chapters, for example), with `<12>` commands pointing at empty table words. Presumably those scenes
  only play from disc 2.

## The file

A voice file is 3 to 72 sectors long. It's a sound-engine data file: a short header, then SPU-ADPCM
sound data. The header starts with one of four patterns:

| first words | files (disc 1 / 2) |
|---|---|
| `1C 80000004 14` | 84 / 97 |
| `20 80000004 18` | 51 / 82 |
| `24 80000004 1C` | 13 / 23 |
| `28 80000004 20` | 0 / 1 |

In the `1C` layout (checked on three clips), the sound data starts at byte `0x48` and runs to the end
flag. Decoded as 22,050 Hz mono, clip `0x94F` lasts 1.34 seconds, and the game ends it after 80
frames, the same length. What the longer headers add hasn't been decoded.

**SPU-ADPCM** is the PlayStation's sound format: blocks of 16 bytes, each holding 28 samples as 4-bit
values plus two header bytes (the prediction filter and shift, and loop flags). The sound chip decodes
it itself.

## How it plays

The message box starts the clip through the sound engine (`0x8003E204`), at the voice's own volume
setting. It doesn't copy the whole clip into sound memory. It **streams** it through a 256-byte ring:

* **The ring:** the driver writes two halves of 128 bytes to sound memory, and one voice of the sound
  chip loops over them.
* **The interrupt:** the chip raises an interrupt when playback reaches a set address. The driver then
  refills the half just played and moves the address to the other half.
* **The end:** the message box asks the sound engine each frame whether the clip is still playing.
  When it stops, or the box is closed, the box stops it and frees its slot.

Clip `0x94F` streams in 133 refills. This was traced in the TinyPSX emulator, after the emulator was
fixed to raise that interrupt: before the fix, the stream stalled after its first 256 bytes.

## What the translation does with them

The translation keeps the original voices. Every `<12>` and `<18>` stays in place, next to the words it
times (`docs/GLOSSARY.md`). When a line is rewritten, moving `<18>` changes when the voice starts.

## Not known yet

* **The longer headers:** what the `20`, `24` and `28` layouts hold besides the sound data.
* **The sample rate:** 22,050 Hz fits the measured lengths. The voice's pitch register (`0x085F`, about
  23 kHz) is close but not equal.
* **A skipped clip:** `0x8005A74C` can skip a load and still advance the slot counter. Under which
  condition hasn't been identified.
