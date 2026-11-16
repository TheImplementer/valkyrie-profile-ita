# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

PSX reverse-engineering toolkit targeting **Valkyrie Profile** (SLUS-01156). Standalone Python scripts with no external dependencies.

## Commands

```bash
# Disassemble a PSX EXE
python3 psx_disasm.py SLUS_011.56 [output.asm]

# Analyze a disassembly file
python3 analyze.py SLUS_011.asm

# Launch the BIN archive navigator (web UI on localhost:8765)
python3 bin_navigator.py [/path/to/VALKYRIE.BIN]

# Edit story dialogue, menu and battle text, write a patched cue/bin copy (GUI: vp_text_gui.py, port 8766)
python3 vp_text_editor.py export DISC.cue --scene 0x16 | --menu 2175 | --battle 1806.1 | --worldmap | --system | --title -o EDIT.txt
python3 vp_text_editor.py build DISC.cue EDIT.txt -o OUT.cue
# Accented letters (à è é ...) work in all three; edits_it/ holds an Italian test translation.
# edits_it/GLOSSARY.md records its names, terms and shortened lines: keep new Italian text consistent with it.
# --worldmap: the world map's own text (file 4797: Freya's lessons, region notes; vp_worldmap_text.py)
# --system: records 0-13 of every scene (Artifact prompts, "Exit to World Map?"): build writes them into all 1,118 scenes;
#   their boxes are fixed by the engine, so the export header lists each one's width
# --title: the title-screen menu labels, re-rendered as pictures (vp_title_menu.py; needs macOS swiftc)

# Translation progress: how much of the disc's text the edit files cover (disc 1: it has every scene)
python3 vp_progress.py DISC1.cue [--edits edits_it] [-o edits_it/PROGRESS.md]
# Counts distinct English texts per area, weighted by letters; re-run it after changing edit files.
# A line that needs no translation is marked done by listing it unchanged in its edit file.

# Make a release (xdelta + PPF patches per disc, README with hashes, zip) from a directory of edit files
python3 vp_release.py --disc1 DISC1.cue --disc2 DISC2.cue --edits edits_it --name "NAME" --version 0.1 -o release/

# Index every TIM image in the archive → JSON (reuses bin_navigator's TIM logic)
python3 index_images.py [/path/to/VALKYRIE.BIN] [--out images_index.json] [--start S --end E]
```

Default BIN path (when no argument given): `~/Documents/VALKYRIE.BIN`

## Testing

No automated tests. Verify changes manually by running `bin_navigator.py` and interacting with the web UI. The server must be stopped and restarted to pick up Python changes.

## Architecture notes

### bin_navigator.py

The main active development file (~1200 lines). A self-contained web app: embedded HTML/CSS/JS served by Python's `http.server` on port 8765. All state lives server-side; the browser fetches JSON from `/api/*` routes.

Key internals:
- **LFSR XOR cipher** — sector 0 decryption; key `K0=0x64283921`, period 32. Step: `key ^= (key<<1); key = ~key; key ^= 0x64283921`
- **File table** — sector 0 decodes to 4-byte entries `[type, sec_lo, sec_mid, sec_hi]`; 503 regular + 6 special entries
- **10-sector streaming blocks** — marker `0x80010160` at offset 0 of each sector header; sectors 8–9 are 0xFF padding
- **LZ77 multi-stream** — flag byte LSB-first; EOS marker = offset 0; multiple streams share one output buffer
- **SPU ADPCM audio** — 128 × 16-byte blocks per 2048-byte sector; byte 0 = header (shift bits 3-0, filter bits 7-4), byte 1 = flags; filter coefficients `SPU_F0 = [0,60,115,98,122]`, `SPU_F1 = [0,0,-52,-55,-60]`; mono, 22,050 Hz
- Audio regions span 10,000+ consecutive sectors; clips are separated by 1–3 near-silent sectors (amplitude < ~3000)

### index_images.py

Standalone CLI that `import`s `bin_navigator` to reuse its TIM detection. Walks the whole archive three ways and writes a JSON index (each entry includes an `/api/image` URL openable in a running navigator):
- **raw** — uncompressed TIMs, found by scanning the file in overlapping 8 MB chunks
- **block** — TIMs inside 10-sector LZ77 blocks (decompress each block, scan)
- **slz** — TIMs inside SLZ-compressed files (`scan_slz_for_tims`)

TIM detection lives in `bin_navigator.py` (`parse_tim_header`, `find_tims_in_data`, `decode_tim_rgba` BGR555→RGBA, `encode_png` pure-zlib, `scan_slz_for_tims`); served via `/api/scan_images`, `/api/scan_range`, `/api/image`.

### psx_disasm.py

Decodes MIPS R3000. PSX EXE header: magic `b"PS-X EXE"` at offset 0, entry PC at 0x10, load address at 0x18, text size at 0x1C, fixed 0x800-byte header. Pseudo-instructions: `move` (addu with $zero), `li` (addiu or ori depending on value).

### analyze.py

Parses the disassembly text output of `psx_disasm.py`. Function boundaries are detected via JAL targets — this is a heuristic, not exhaustive.
