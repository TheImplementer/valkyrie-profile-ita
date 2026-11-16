# Valkyrie Profile in Italiano

An Italian fan translation of Valkyrie Profile (PlayStation, USA release, SLUS-01156 / SLUS-01179),
and the tools and notes that made it possible. *Traduzione amatoriale in italiano: i file di
traduzione sono in `edits_it/`.*

The translation is a work in progress: see [`edits_it/PROGRESS.md`](edits_it/PROGRESS.md).
This repository holds no game data. To build or explore anything you need your own copy of the
game as BIN/CUE, the Redump dumps of the USA discs.

## What's here

| Where | What |
|---|---|
| [`edits_it/`](edits_it/) | The Italian text, one edit file per scene, menu or battle table. [`GLOSSARY.md`](edits_it/GLOSSARY.md) records the names, terms and forms of address chosen, and why. |
| [`VP_DIALOGUE_LOAD_PATH.md`](VP_DIALOGUE_LOAD_PATH.md) | The main write-up: how the game finds, decompresses, decodes and draws its text, from disc sector to message box, and everything learned while changing it. |
| [`VP_DIALOGUE_FORMAT.md`](VP_DIALOGUE_FORMAT.md), [`DIALOGUE_SEARCH.md`](DIALOGUE_SEARCH.md) | The earlier notes: the archive, its encryption and compression, and the search for the dialogue. |
| [`notes/`](notes/) | Shorter notes: the scene package parts, the script's built-in commands, voice clips, sound labels. |
| `vp_text_editor.py` and the other `vp_*.py` | The tools (Python 3, no dependencies). |
| [`CLAUDE.md`](CLAUDE.md) | A one-page list of every command. |

## Building the Italian discs

```bash
python3 vp_release.py --disc1 "Valkyrie Profile (USA) (Disc 1).cue" \
    --disc2 "Valkyrie Profile (USA) (Disc 2).cue" \
    --edits edits_it --name "Valkyrie Profile (Italiano)" --version 0.1 --keep-work -o release/
```

This builds both discs from the edit files and writes xdelta and PPF patches with a README of
hashes. `--keep-work` keeps the patched images in `release/work/`. The first run reads every scene
font on the disc to build `vp_glyph_db.json`, which takes a minute.

To build one disc directly:

```bash
python3 vp_text_editor.py build DISC.cue edits_it/*.txt -o OUT.cue --fit-boxes
```

The title-screen labels are drawn as pictures and are re-rendered with a small Swift program, so
building `title_menu.txt` needs macOS with `swiftc`.

## Editing

```bash
python3 vp_text_editor.py export DISC.cue --scene 0x16 -o scene_0x16.txt
python3 vp_text_gui.py DISC.cue --edits edits_it
```

`export` writes a scene's English text as an edit file. `--menu`, `--battle`, `--worldmap`,
`--system` and `--title` do the same for the other kinds of text. The GUI at
http://127.0.0.1:8766/ edits scenes and tables with a preview of each message box. It shows each
scene's place names if `vp_scene_map.json` exists, made with
`python3 vp_scenes.py VALKYRIE.BIN --json vp_scene_map.json` from the disc's `VALKYRIE.BIN`.
`python3 vp_progress.py DISC1.cue --edits edits_it -o edits_it/PROGRESS.md` updates the progress
table.

## What isn't here

The game's executable, the disassembly, the text dumps, the disc images, the patched images and
screenshots stay out, and so does the font. The tools find each character by its glyph bitmap,
and `vp_glyph_names.json` keys that table by a hash of each bitmap, not by the bitmap itself.

The notes mention TinyPSX, the PlayStation emulator the game was traced in, and some files under
`notes/edit_proof/`: neither is part of this repository.
