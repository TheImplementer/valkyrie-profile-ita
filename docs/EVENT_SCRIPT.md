# The event script

Everything that happens in a Valkyrie Profile scene is run by a small program in that scene: who
stands where and walks to where, the camera, the music, which line of text appears and when, the
choices, and which scene comes next. This document explains those programs and the virtual machine
that runs them.

The counts below were checked on all 1,118 scenes of disc 1. Where the script sits in a scene's data
is in `docs/SCENE_TEXT_FORMAT.md`: it's the part of the scene block between the header and the text
index.

## The machine

The script is a list of 32-bit instructions. The game runs it in an interpreter in the field engine
(`func_800714DC`, loop at `0x800715B4`):

* **One step:** fetch the instruction at the instruction pointer and move it on. Then call one of 256
  handlers, chosen by the instruction's top byte (the table is at `0x800831D0`).
* **Waiting:** an instruction that has to wait, for a walk to end or a button press, either sets a
  wait counter or moves the instruction pointer back one word, so it runs again on the next frame.
  Each frame the script runs until something waits, so it moves along with the game.

The machine has:

| part | what |
|---|---|
| a data stack | values are pushed and popped; it grows down |
| a call stack | return addresses, for calls into functions |
| a frame | the current function's local variables (words and bits) |
| global variables | words shared by all scenes; global 0 is the **accumulator**, where results go |
| flags | single bits shared by all scenes, such as "this chest is open" |

## An instruction

```
bits 31-24   opcode
bits 23-0    operand
```

Some instructions are followed by extra words. How many depends on the opcode, so a disassembler has to
know every opcode's length: `vp_script.py` does.

**Variables.** An operand that names a variable is read this way:

* bit 23: 1 = local variable, 0 = global;
* bits 17-16: its size, word, half, byte or bit;
* bits 15-0: its offset (a bit number for flags).

The scripts also refer to global *bytes*, such as the story counters below.

## Core instructions: 0x01 to 0x4A

These are the stack machine's basics:

| op | words | does |
|---|---:|---|
| `12` | 1 | push the 23-bit number in the operand |
| `13` | 2 | push the next word |
| `0B` `0C` `0D` `0E` `0F` | 1 | push a variable (word, half, byte, bit) |
| `01`-`08` | 1 | pop into a variable |
| `14` | 1 | call the function at the operand's word |
| `15` | 1 | jump |
| `16` | 1 | pop; jump if it was 0 |
| `17` | 1 | return (with nothing to return to, the script's turn ends) |
| `18` | 2 | function entry; the next word is the size of its locals |
| `19` / `1C` | 2 | push whether a variable equals the next word (word / byte compare) |
| `1A`, `21` | 2 | set a variable to the next word |
| `22` | 1 | pop into a variable |
| `49` / `4A` | 1 | add / subtract 1 |

Arithmetic and comparisons, `0x23` to `0x48`, come in pairs that put their result in the accumulator.
The odd opcode uses the next word (2 words), and the even one pops the stack (1 word). In order: add,
subtract, multiply, divide, and, or, xor, shift left, shift right, logical and, logical or, not equal,
equal, greater, less, greater or equal, less or equal.

## Built-ins: 0x50 and up

Everything else is a **built-in**, an instruction that calls the engine to do something. Bit 20 says
where its arguments are:

* **Bit 20 clear:** a fixed number of words follow the instruction, the "inline" form. The count for
  each built-in is in `BUILTIN_ARGS` in `vp_script.py`.
* **Bit 20 set:** the arguments are popped from the stack.

The scene code on disc 1 uses 113 different built-ins, mostly in the inline form (91% of calls).
Opcodes `0xEE` and up aren't instructions: the handler table ends at `0xED`. All the built-ins have
names in `vp_builtins.json`, each with a confidence and the evidence for it. By family:

| family | examples |
|---|---|
| text | `92` show a message, `93` a choice, `9D` wait for the choice, `7E` wait until a message is dismissed, `A8` load a portrait, `AB` load a voice clip |
| actors | `85` spawn an actor, `8A` walk to, `71` set an animation, `A1` / `A2` set / get a property, `89` remove |
| camera and screen | `82` set the camera, `83` pan, `87` fade the screen, `96` shake |
| sound | `BB` load music, `8D` start it, `8F` play a sound effect |
| flow | `8C` wait N frames, `81` cutscene mode, `95` random number, `86` start a battle, `9E` / `9F` choose / change the scene |
| triggers | `80` register a trigger zone: run a function when the player walks or talks there |
| story pictures and movies | `A5` show a full-screen illustration, `C4`-`C8` play a movie |

The most used, in scene code: register a trigger zone (9,386 times), set an actor property (8,347),
fade the screen (4,844), wait (4,646).

## The shared library

The first `0xA51` words (10,564 bytes) of every scene's script are the same: a library of functions
the scenes call. Among them are the wrappers that show text:

* **`func_03e2`:** shows a message box and waits. It takes six arguments: x, y, width, height, the
  record number, and how many frames to wait afterwards.
* **`func_0331`:** loads a speaker's voice clip and portrait.

The scene's own code follows the library. The scene block's header gives its **main function**
(hdr[2]), which runs when the scene starts. It's the first function after the library in 833
scenes, but not always. The 1,118 scenes hold 10,540 functions of their own in all. Some scenes
share their code, so there are 1,035 different scene programs.

## An example: the first line of the game

When a new game starts, Valhalla (scene `0x16`) shows Valkyrie's line "How nostalgic・・・". Here is
that part of its main function, as `vp_script.py` lists it (word number, the instruction, what it
does):

```
0aa9: ab00094f  load_voice_clip 0x94f (inline)
0aaa: 8c000000  wait_frames 0x0 (inline)  00000078
0aac: be000100  set_msgbox_option 0x100 (inline)
0aad: 12000078  push 0x78
0aae: 12000030  push 0x30
0aaf: 12000080  push 0x80
0ab0: 1200001c  push 0x1c
0ab1: 1200000e  push 0xe
0ab2: 12000028  push 0x28
0ab3: 140003e2  call func_03e2    ; message 14: Valkyrie / 「How nostalgic・・・
0ab4: be000000  set_msgbox_option 0x0 (inline)
0ab5: 2103000e  set var, word G0xe.bit  00000001
0ab7: 21030008  set var, word G0x8.bit  00000001
0ab9: 9e000015  select_scene 0x15 (inline)  00000000
```

* **`ab00094f`:** loads voice clip `0x94F`, Valkyrie's spoken line.
* **`8c000000 00000078`:** waits 120 frames, two seconds.
* **The six pushes and `call func_03e2`:** a box at x 120, y 48, 128 wide and 28 high, showing record
  14, then a 40-frame wait. The record's own `<18>` starts the voice clip.
* **`2103000e 00000001` and `21030008 00000001`:** set flags `0x0E` and `0x08`. `21` is "set a variable
  to the next word", and the operand `03000e` says: global (bit 23 clear), bit size (bits 17-16 = 3),
  number `0x0E`.
* **`9e000015`:** chooses scene `0x15` as the next one, and the script goes on to fade out and switch.

This is how the translation's box sizes work: the width and height are pushed as plain numbers, so
`vp_text_editor.py build --fit-boxes` can raise them when an Italian line is longer.

## Story progress

Two global bytes drive the story:

* **story** (global byte `0xD0`): which storyline is playing, 0 to 30;
* **step** (global byte `0xD1`): how far along it is (1, 2, … then steps of 10; `0xFF` = finished).

Scene code is full of blocks like "if story is 1 and step is 9: play this event, set step to the next
value, and switch to the scene where it goes on". Following those writes rebuilds the order of every
storyline. The game's own debug menu jumps to exactly such (story, step, scene) points. That's also
how the translation's checks jump straight to a story event: set the two bytes and switch scene.

## How the code was compiled

The scripts were compiled from structured source, so the jumps nest. A forward "jump if 0" is an
`if`; a jump just before its target makes an `else`; a backward jump closes a loop. A tool can rebuild
the conditions (`story == 0x18 and step == 0xb`) and print each scene's lines in the order they can
play, nested in the conditions that decide them.

## Checks

On disc 1, with the instruction lengths above:

* all 40,295 call and jump targets in scene code land on an instruction;
* all 14,876 calls land on a function entry (`18`);
* every scene's main function comes after the library;
* 3,359 of the 3,363 lines in scene code are tied to the instruction that shows them.

## Not known yet

* **Some built-ins:** 7 have low-confidence names, and some arguments are unclear. See
  `vp_builtins.json`.
* **hdr[5] and hdr[6]:** two entry points in the shared library. What the game uses them for hasn't
  been traced.
