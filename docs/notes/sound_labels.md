# EXE sound label fixes around 0x8001A174

Evidence comes from the RAM image `ram_interp.bin` (disassembled with `rdis.py`), the callers found by a
scan of `0x80010000-0x800C0000`, and a live TinyPSX trace (frames 7505-7741 of `vp_newgame.replay`,
scene 0x16 "How nostalgic", scripts `v12c.dbg`/`v12e.dbg` in this directory).

Terminology used by the engine (sound manager `mgr = [0x8007FD9C]` = `0x800964B4`):
* 8 **sequence slots** of 52 bytes at `mgr + 0x112C + 52*(handle-1)`; a "handle" is the slot number 1..8.
  `+0x112C` = play pointer (0 = slot free), `+0x1130` = data pointer, `+0x113E/+0x113F` priority bytes.
* 24 **hardware voices** of 56 bytes: `mgr + 0xBBE + 56*v` = active flag, `+0xBBF` = owner id,
  `+0xB98` = flags (bit 0x20000 = "no queued key-off").

## Changes

| address | old name | new name | confidence |
|---|---|---|---|
| 0x8001A174 | `note_on_activate_voice` | `sequence_stop` | high |
| 0x80019CF0 | `voice_arbitrate` | `sequence_slot_arbitrate` | high that it works on sequence slots (name is mine) |
| 0x8001A100 | (none) | `sequence_is_active` | medium-high |
| 0x80019834 | `sequence_parse_events` | `sequence_start` | medium (outside the +-0x400 window) |

Not an error: `0x8001A144 push_voice_event_queue` (see the end). No other label lies within +-0x400.

Paste-ready lines for `labels/valkyrie_profile_usa.labels`:

```
0x80019CF0  sequence_slot_arbitrate   # picks/evicts one of the 8 sequence slots for a new sequence, calls sequence_stop on the victim; returns handle 1..8, 0 = refused (priority too low)
0x8001A100  sequence_is_active        # returns the slot's data pointer (non-zero) while sequence `handle` plays, else 0; used by the dialogue voice poll and built-in 0xD6
0x8001A174  sequence_stop             # sound-manager a0, sequence handle a1 (1..8): key off every voice of that sequence, free the slot
```

## 0x8001A174 `note_on_activate_voice` -> `sequence_stop`

`a0` = sound manager, `a1` = sequence handle (0 = nothing to do). Disassembly (`rdis.py ram_interp.bin 8001A174`):

1. `0x8001A19C` returns at once for handle 0; otherwise it sets the busy byte `mgr+0x87` and addresses the
   slot as `(handle-1)*52 + mgr + 0x112C` (`0x8001A1A8-0x8001A1C4`). If the slot's play pointer
   (`+0x112C`) is 0 it does nothing (`0x8001A1D0`).
2. It reads the slot's owner id (`+0x1143`) and clears the matching bank byte `mgr + 88*id + 0xB6`.
3. Loop over all **24 voices** (`0x8001A20C-0x8001A290`, stride 56). For every voice whose owner id (`+0xBBF`)
   equals that id and whose active byte (`+0xBBE`) is set it:
   * clears the active byte (`0x8001A248`),
   * unless voice flag `0x20000` is set, pushes the voice number to the free-voice queue
     (`jal 0x8001A144`, `0x8001A240`),
   * decrements the instrument's reference count (`+0xAC`, `0x8001A27C`),
   * ORs the voice into a mask `s3`.
   This is the same body as the single-voice `voice_note_off` (`0x800184E8`: clear `+0xBBE`, push to
   `0x8001A144`, decrement `+0xAC`), run once per voice of the sequence.
4. Afterwards the mask is key-offed: if the slot's `+0x1134` is non-zero (start code stores there the result of
   the transfer-list builder `0x800193A0`, so probably "this sequence carries sample data") the mask is only
   ORed into `mgr+0x3260` for the streamer to apply; otherwise it goes at once through
   `0x8001D720(0, mask)` (`spu_keyon_keyoff`, command 0 = key off) (`0x8001A2B0-0x8001A2DC`).
5. Finally the slot's play pointer is zeroed (`sw $zero, 4396(slot)` at `0x8001A300`: the slot is free)
   and `mgr+0x87` is cleared.

Nothing here allocates a voice, writes a pitch or key-on mask, so it is not a note-on handler. Everything it
does is release.

Callers (all 14 `jal 0x8001A174` in the loaded images) are stop paths:
* `0x80014BA8`: `for h = 1..8: sequence_stop(mgr, h)`, then spin on `is_sequence_playing` = "stop all sequences".
* `0x800199B8`, `0x80019ED4`, `0x80019F70`, `0x80019FA0`, `0x80019FD4`, `0x8001A0A4`: inside the
  sequence-slot arbitration/start code, evicting the sequence that is about to lose its slot.
* `0x8004DB98`: the message box's voice-clip end (`0x8004DB34`), `a1` = handle stored by the clip start.
* `0x80055EDC`, `0x80059330`, `0x800593B4`: sound-handle table clear / scene teardown and "stop sfx handle"
  (built-in 0xE9, `func_80059370`).
* `0x8006FF5C`: built-in 0xCF `stop_sfx` (pop id, stop it); `0x800703EC`: built-in 0xD4 before it restarts a
  sequence; `0x800705F4`: built-in 0xD7 `sound_seq_stop` (kills the d3 task and stops its sequence).
  `vp_builtins.json` already describes these built-ins with "stop".

Live check (TinyPSX, `v12c.dbg`): the Valkyrie voice clip for "How nostalgic" is started at frame 7658 with
`0x8003E204 -> 0x80019834` on buffer `0x800FE9D0` (the returned handle is kept at slot byte `0x8007F20C`).
At frame 7738 (= 80 frames later, 29,596 samples at 22,050 Hz = 1.34 s, the clip length) the box code
`0x8004DB34` runs and calls `0x8001A174` with `a0 = 0x800964B4`, `a1 = 8`: the handle of that sequence.

## 0x80019CF0 `voice_arbitrate` -> `sequence_slot_arbitrate`

Disassembly `0x80019CF0-0x8001A0F8`: the first loop (`0x80019D58-0x80019DD8`) walks the **8 sequence slots**
(stride 52, `+0x112C` = in use, `+0x113E` = priority, `+0x114A` = state 5/6, `+0x113F` = group), remembers the
free slot (`a3`), the lowest-priority slot, and the slots in state 5 and 6. The rest compares the new
sequence's priority (`a1 >> 24` = 0x70 or 0x6F, the `a2` header word) with them and, where one must give way,
calls `0x8001A174` (`sequence_stop`) on the victim: `0x80019ED4`, `0x80019F70`, `0x80019FA0`, `0x80019FD4`,
`0x8001A0A4`. It returns a handle (`slot + 1` at `0x8001A010`), 0 (new sequence refused) or -1. It never
touches a hardware voice, so "voice" in the old name is wrong.

## 0x8001A100 (no label) -> `sequence_is_active`

```
if (a1 == 0) return 0;
slot = mgr + 52*(a1-1);
return slot[0x112C] ? slot[0x1130] : 0;      // data pointer while playing
```
Callers: message-box voice poll `0x8004D8AC` (a1 = handle at `0x8007F20C+8*slot`; when it returns 0 the box ends
the voice), built-in 0xD6 `sound_is_playing` (`0x80070554`), the BGM task (`0x8005A20C`). It is not the labelled
`is_sequence_playing` (`0x80018F70`), which tests manager-wide bytes `+0x10DC..+0x10DE`.

## 0x80019834 `sequence_parse_events` -> `sequence_start` (outside the window, medium)

It takes (`mgr`, data pointer, volume/pan word, priority mode `a3` 0/1/2, out pointer), calls
`sequence_slot_arbitrate`, fills the chosen slot (`+0x1130` data pointer, `+0x112C` play pointer = data +
header size + 4, priorities `+0x113E/+0x113F`, `+0x114C`, `+0x1140`...), builds the SPU transfer list
(`0x800193A0`) and **returns the handle** (`0x80019B38: move $v0,$s3`). Live: `0x8003E204` (voice start,
`0x8004D854`) and built-in 0xD4 reach it with the freshly loaded buffer. No note events are parsed here: the
event stream is walked per tick by `sequence_event_dispatch` (`0x800188BC`) and `sequence_track_tick`. So
`sequence_start` (or `sequence_open_and_play`) describes it better.

## Left alone

* `0x8001A144 push_voice_event_queue`: writes the byte `a1` at the write pointer `mgr+0x48`, advances it with a
  wrap back to `mgr+0x1530`. In the RAM image the ring is the 24 bytes at `0x800979E4` and holds voice numbers
  (`02 11 0d 08 14 0a 0f 0b 0c 17 13 0e 01 12 ...`, all below 24). Callers push **voice numbers**
  (`0x8001853C`, `0x8001A240`, `0x800147F4`, `0x8001A094`). The reader is the voice allocator around `0x80017FF8`:
  it takes the byte at the read pointer `mgr+0x44`, and if that voice's SPU key-off has completed (`0x8001EEA8`)
  hands it out again. So it is a FIFO of released voices. The name is vague but not wrong;
  `voice_free_queue_push` would be more exact (optional).
* The other labels near 0x8001A174 (`spu_keyon_keyoff` 0x8001D720, `spu_voice_flag_set_bits` 0x8001A8D8,
  `voice_note_off` 0x800184E8) fit their code.
* Unlabelled neighbours I did not rename (no direct callers in the images): `0x8001A330` (arms a per-sequence
  volume ramp: `+0x115A = 127`, `+0x1154 = 30720 / frames`), `0x8001A398` (per-sequence tempo fields
  `+0x114E/+0x1150/+0x1152`).
