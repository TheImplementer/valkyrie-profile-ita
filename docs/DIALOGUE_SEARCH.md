# Story Dialogue Search — ★ CRACKED 2026-06-13 ★

**Story/cutscene dialogue is now decoded** (something the public romhacking scene never achieved).
Method that worked — *dynamic*, via PCSX-Redux:
1. Confirmed the **dialogue text format** is NOT Scheme-A: it's **7-bit glyph-index text + `0x80`
   2-byte command escapes** (interpreters in the loaded overlay at `0x8004c–0x80055xxx`; each
   text byte indexes a ~100-glyph proportional font, width table at `0x800D93C4`).
2. Dumped 2 MB main RAM during a dialog (`PCSX.getMemPtr` → file). On disc the text is
   compressed; **in RAM it's decompressed** into a buffer.
3. Found the live buffer by a **substitution-crib search**: the on-screen line
   `Watch where you're going, girl!` has a unique case-sensitive letter-repeat pattern →
   exactly one match in the dump → buffer at `0x800E18F8`, and the glyph→char map fell out.
4. Grew the codepage with more known lines (bijective consistency, dialogue-region only).

**Codepage (glyph index → char), 30 confirmed so far** (`vp_codepage.json`):
`0d=! 31=SPACE 33=h 34=e 35=i 36=t 37=m 38=a 39=s 3b=r 3c=o 3e=n 42=u 45=f 46=c 48=l 49=g
4a=p 4b=y 4c=L 4d=d 4e=O 51=v 52=w 53=W 57=N 5c=' 62=I 64=, 65=H` (remaining letters fill
trivially from context). Decoded ~40+ lines → `vp_dialogue.txt`, e.g.:
- "This item is an Artifact. All Artifacts are the rightful property of Lord Odin. Return it or…"
- "Mother Laia「Watch where you're going, girl! How dare you splash water on a guest!"
- "Lucian「One day, I found out that her parents were going to sell her. We escaped together."
- "Valkyrie「Her name was Platina."

Tools: `vpram*.bin` (RAM dumps), the crib/decode one-liners (this session), `vp_codepage.json`,
`vp_dialogue.txt`. To complete: dump more scenes, crib more lines → fill the remaining glyphs.

---

# Story Dialogue Search — Current Status (historical notes below)

## Goal
Find story/cutscene dialogue text in VALKYRIE.BIN (Valkyrie Profile, SLUS-01156).

## What We Know Works

### File Table Decryption
- Sector 0 is LFSR-encrypted using **dword-XOR** (not byte-XOR — this was a bug in earlier code)
- Correct: `words = struct.unpack('<{N}I', raw); for w in words: out.append(w ^ key); key = lfsr_step(key)`
- Key K0 = 0x64283921; step: `key ^= (key<<1)&MASK32; key = (~key)&MASK32; key ^= K0`
- File table: 503 regular entries (types 0x02/0x03/0x04) covering sectors 2–29768
- *Later corrected:* the "type" byte is the minutes of a BCD disc position, and the table has
  4,472 files over 13 sectors. See `docs/VALKYRIE_BIN_TABLE.md`.

### Text File Format (confirmed)
Decompressed SLZ data layout:
```
[4 bytes] text_section_offset
[4 bytes] offset_for_string_0   (index = position in array)
[4 bytes] offset_for_string_1
...
[text data at text_section_offset]
```
Each offset is relative to `text_section_offset`. Null-terminated strings.

> **Update 2026-11-12:** these are the 13 menu string tables, now editable with `vp_menu_text.py`
> (see "Editing menu and other non-dialogue text" in docs/VP_DIALOGUE_LOAD_PATH.md). The real rule is
> character = byte + 0x1F for all of 0x01-0x5F (lower case is 0x42-0x5B), and the header is
> (id, offset) pairs, not a plain offset array.

**VP Encoding Scheme A** (used in all text files found so far):
- First byte of string: `decoded = raw + 0x1F`
- Subsequent bytes: `decoded = raw - 1`
- Decoded 0 → SPACE; decoded 0x0A → newline
- Valid range: decoded 0x20–0x5F

**Custom font mapping**: decoded 0x21–0x3A → game glyphs A–Z

### Text Files Found

| Byte offset | Sector | Type | Comp → Decomp | Entries | Content |
|-------------|--------|------|---------------|---------|---------|
| 408,548 | 199 off 996 | SLZ2 | 5,379→7,520 | ~305 | Menu text: NEW GAME, CONTINUE, character names, song titles |
| 173,106,220 | 84524 off 1068 | SLZ2 | 1,031→1,256 | 128 | UI |
| 173,121,012 | 84531 off 1524 | SLZ1 | 32,639→62,431 | 2,104 | UI + skill help text |
| 173,159,840 | 84550 off 1440 | SLZ1 | 13,019→22,115 | 1,360 | UI, "SEND CHARACTER TO ASGARD" |
| 173,178,508 | 84559 off 1676 | SLZ2 | 5,034→9,595 | 99 | Skill names: FREYA, LUCIAN, SMASH AXE, etc. |
| 173,202,508 | 84571 off 1100 | SLZ1 | 41,321→77,775 | 2,890 | UI: items, spells, "TO ODIN THE ALL-FATHER" |
| 173,251,972 | 84595 off 1412 | SLZ1 | 34,335→64,514 | 2,380 | UI: items, descriptions |
| 173,294,112 | 84616 off 544 | SLZ1 | 17,054→29,003 | 1,524 | UI |
| 173,328,196 | 84632 off 1860 | SLZ1 | 13,561→22,926 | 1,367 | UI, "CANNOT REMOVE VALKYRIE" |
| 173,351,968 | 84644 off 1056 | SLZ1 | 28,601→55,400 | 1,813 | UI + item descriptions |
| 173,388,080 | 84662 off 304 | SLZ2 | 778→1,052 | 80 | UI |
| 173,402,596 | 84669 off 484 | SLZ2 | 1,875→2,438 | 174 | UI |
| 90,733,952 | 44303 off 1408 | SLZ1 | 21,431→40,819 | 1,541 | Battle item/spell text: glyph numbers into its own font, not Scheme A (update 2026-11-12, see "Battle text" in docs/VP_DIALOGUE_LOAD_PATH.md) |
| 167,348,640 | 81713 off 416 | SLZ1 | 2,786→4,093 | 228 | Small UI |
| ~173,000,000 | 84471–84474 | mixed | various | 55–395 | Small UI strings |

**All text found so far is game UI/system text** (menus, items, skills, status screens).  
**Story/cutscene dialogue has NOT been found.**

### 2026-06-12 archive-wide sweep with the corrected codec (`dialogue_sweep.py`, `block_sweep.py`)
Re-ran the search across the WHOLE archive using the corrected value-based `codepage.decode`:
- **SLZ containers** (13,331 scanned, pointer-table + raw null-terminated): the only genuine
  English is the known UI/item/skill/menu cluster (sec 84531/84571/84595/84644 + sec 199).
  Everything else flagged was binary noise that fooled a loose heuristic.
- **10-sector BLOCK streaming region** (8,951 structured blocks across sectors 2–30k / 44–84k
  / 108–160k): **no** readable text — only repetitive binary patterns.
- Landmark phrase search ("I WANT TO GO HOME", "GO HOME", "I WANT TO GO", "RETURN HOME"):
  **0 hits** in either container class.

So with a now-validated Scheme-A decoder, story/cutscene dialogue is **confirmed absent from
both the SLZ text files and the BLOCK streaming files**. Leading remaining hypotheses:
(1) dialogue uses **Scheme B** ("word-boundary reset" — noted but never cracked), which the
Scheme-A codec garbles to noise; (2) it lives in event-script/audio-adjacent data in a
different encoding; (3) cutscene text is tied to the voiced-audio regions. Cracking Scheme B
is the most promising next step.

## Community state of the art (web search, 2026-06-12)
Checked whether anyone has publicly cracked this. Findings:
- The compression is community-known as **SLZ01 / SLZ02** (= our SLZ-1 / SLZ-2). Tools by
  *Unknown Master* (C++) and *Pixel* (Lua, ~2009) decompress it and "dump decompressed text
  (front = text, rear = FONT)" — i.e. the **UI/menu/item/skill/name text + font** we found.
- **Story/cutscene dialogue is NOT publicly cracked.** The romhack scene states dialogue
  "appears encrypted or compressed and resists standard extraction" and its location
  "remains undiscovered" — exactly our independent result. *Pixel* reportedly made progress
  on "text decryption" but never finished/published. A French (RPG-Z) translation was never
  inserted because the dialogue hacking was never solved. No GitHub/Data Crystal page exists
  for VP-PSX dialogue extraction.
- Community advice: *"you'll be forced to analyze the game code to understand how it reads
  files and create your own tools."* → the disassembler + micro-executor route.

Sources: romhack.org t=3191 & t=3222, romhacking.net/translations/3837, gbatemp 588334,
tcrf.net/Valkyrie_Profile. **Conclusion: dialogue is an unsolved problem; our toolchain has
matched/exceeded the public state of the art (validated codepage, proven decompressor, font
located). Next: trace the event-script interpreter in code.**

## Event-script / message interpreter located (code trace, 2026-06-12)
Disassembled the field/text overlay (sector 295321 @ `0x80030000` →
`overlay_field_80030000.asm`) and found the in-game **message/text interpreter** at
`0x8003E220`. Its byte format is **NOT Scheme A**:
- reads a byte from a stream pointer (`lbu 0($s0)`); `0x00` = **terminator**;
- **high bit set (0x80+)** → a **2-byte control command** (combines `byte&0x7F` with
  `next<<7` → 14-bit code; dispatched via a 9-entry table at `0x80045658`) — i.e. color /
  speed / wait / portrait / line-break commands;
- **`0x00–0x7F`** → **text characters** (7-bit).
There's also a 9-state message-box state machine at `0x80039040` (table `0x80045564`).

**Implication:** story dialogue almost certainly uses this **7-bit-text + 0x80-command-escape**
format, fundamentally different from the UI's Scheme-A value rule.

**Exhaustive static verdict (all negative):** searched for dialogue every static way with the
fixed tooling — Scheme-A decode over all SLZ containers + all BLOCK streaming blocks; raw-ASCII
over the whole decompressed SLZ archive (`raw_ascii_sweep.py`); and **raw-ASCII over the entire
605 MB disc** (`whole_disc_ascii.py`). Result: **zero** readable dialogue, **zero** phrase hits.
Nothing on the disc is plain ASCII — every text glyph value is an encoded font index (UI =
Scheme-A; dialogue = 7-bit glyph index), so no byte-scan can reveal dialogue.

**Conclusion / next step (dynamic).** Dialogue text is loaded per-scene into the buffer the
interpreter at `0x8003E220` reads (`[s1+0..4]`), in a non-ASCII 7-bit-glyph-index encoding,
from event/cutscene data (compressed or CD-streamed) — which is exactly why every static
search (ours AND the community's) fails. To extract it: in an emulator (PCSX-Redux), set a
**breakpoint at `0x8003E220`** during a cutscene, dump the text buffer it reads = the raw
dialogue bytes, and derive the 7-bit char→glyph codepage by matching against on-screen text.
We have pinpointed the exact interpreter address — the missing piece the community never
published.

## What Was Ruled Out

| Location | Why not dialogue |
|----------|-----------------|
| Sectors 29769–40769 | Binary animation/sprite data; VP-decoded strings are repetitive patterns like "TTTCCC" |
| 10-sector BLOCK data (types 0x02/0x03) | Pure binary map/dungeon geometry; printable ratio ~2% |
| Sector 13 SLZ-2 (274KB overlay) | PSX EXE code; first bytes are MIPS instructions |
| Sectors 119–198 (thirty 3616-byte files) | Sprite animation frames, fixed binary structure |

## Disc Structure Map

```
Sectors 0–1:        File table (LFSR-encrypted)
Sectors 2–14:       Game boot data
Sector 13:          SLZ-2 PSX EXE overlay (117KB→274KB, 16 streams)
Sectors 119–198:    Sprite/animation data (thirty SLZ-2 files, each 3616B decompressed)
Sector 199 +996:    Main text archive (menu strings, names, songs)
Sectors 200–29768:  Mixed 10-sector BLOCK data + other files (file table coverage)
Sectors 29769–40769: Binary SLZ files (animation/game data)
Sectors 40770–44303: Binary SLZ files
Sector 44303:       Large SLZ-1 (not readable text)
Sectors 44304–81713: Mixed BLOCK/AUDIO/BIN
Sectors 81713–84470: Small UI text files
Sectors 84471–84474: Small UI text files
Sectors 84524–84669: Main UI text cluster (items, skills, descriptions)
Sectors 84670–108000: BIN/AUDIO mix
Sectors 108000–150000: 10-sector BLOCK data (confirmed by 0x80010160 marker)
Sectors 160000+:    Mostly binary; some "high printable ratio" sectors unexplored
Sectors 300000–310265: All zeros (empty)
```

## VP.txt Hints (from ~/Documents/VP.txt)

```
Location of pointers table: 352800
Code for interrupt 4 (CD): 10400003 00000000
psxRegs.pc == 0x80011dac
```

- `352800` — likely a byte offset (not yet decoded). Byte 352800 = sector 172, offset 544.
  Raw bytes there are high-entropy binary (audio or game data, NOT readable).
  **Unresolved**: whether this is a BIN offset, PSX RAM address, or decompressed-data offset.
- `0x80011dac` — MIPS address `li $a2, 6400` inside the CD loading/LFSR-decrypt routine.
  The routine copies 25,600 bytes from disc and LFSR-decrypts them in place.

## Next Steps to Try

1. **Scan sectors 84670–160000** for SLZ files with text pointer tables (not yet done).
2. **Investigate Scheme B files** — sectors 84524+ use Scheme B (word-boundary reset encoding).
   Not all files were checked for Scheme B; some might contain dialogue.
3. **Try decoding without pointer table assumption** — story dialogue might use a different
   format (e.g., raw null-terminated strings without an index table).
4. **Check sectors around 120000–160000** — confirmed BLOCK data at 108000–150000 but
   there may be non-BLOCK files interspersed that contain dialogue.
5. **Search raw BIN for known encoded strings** — e.g., "LENNETH" in Scheme A raw encoding:
   `[0x2D, 0x46, 0x4F, 0x4F, 0x46, 0x55, 0x49]` = bytes `2D 46 4F 4F 46 55 49`
6. **Look at type 0x03/0x04 non-BLOCK sectors** (sectors in the file table range that don't
   have the 0x80010160 marker — some are small binary files that haven't been inspected).
7. **Decompress the full sector 13 overlay** (274KB, 16 sub-streams) and search for
   string tables embedded in the game code.

## Key Code Snippets

### Correct SLZ-1 decompressor
```python
def slz1_decompress(payload, max_out=None):
    out = bytearray(); offset = 0; flag_byte = 0; bits_left = 0
    while offset < len(payload):
        if max_out and len(out) >= max_out: break
        if bits_left == 0:
            flag_byte = payload[offset]; offset += 1; bits_left = 8
        bit = flag_byte & 1; flag_byte >>= 1; bits_left -= 1
        if bit == 1: out.append(payload[offset]); offset += 1
        else:
            B1 = payload[offset]; B2 = payload[offset+1]; offset += 2
            V1 = ((B2&0xF)<<8)|B1; V2 = (B2>>4)+3
            if V1 == 0: break
            src = len(out) - V1
            for i in range(V2): out.append(out[src+i] if 0<=src+i<len(out) else 0)
    return bytes(out)
```

### Correct LFSR (dword XOR, not byte XOR)
```python
INIT_KEY = 0x64283921; MASK32 = 0xFFFFFFFF
def lfsr_step(key):
    key = key ^ ((key << 1) & MASK32)
    key = (~key) & MASK32
    key = key ^ INIT_KEY
    return key
def decode_sector0(raw_2048_bytes):
    words = struct.unpack_from(f'<512I', raw_2048_bytes)
    key = INIT_KEY; out = []
    for w in words:
        out.append(w ^ key); key = lfsr_step(key)
    return struct.pack('<512I', *out)
```

### VP Scheme A decode + font apply
```python
def decode_vp_a_with_font(raw):
    result = []
    for j, b in enumerate(raw):
        d = (b + 0x1F) if j == 0 else (b - 1)
        if d == 0: result.append(' ')
        elif 0x21 <= d <= 0x3A: result.append(chr(ord('A') + d - 0x21))
        elif 0x20 <= d < 0x80: result.append(chr(d))
        elif d == 0x0A: result.append('\n')
        else: result.append(f'[{d:02x}]')
    return ''.join(result)
```

---

## 2026-06-12 Update — the `+0x1F/-1` scheme is a tool approximation, not the game's logic

Built and **validated** a MIPS R3000 micro-executor (`micro_exec.py`): it re-runs the real
in-game type-1 decompressor `0x800123CC` and reproduces `bin_navigator.slz1_decompress`
**byte-for-byte** (336,596 bytes). So our decompression is provably exact. Then used it +
static scans to investigate how text is decoded/shown. Findings:

- **The game never runs the `+0x1F`/`-1` transform.** Scanned all ~13,000 decompressed SLZ
  overlays for `addiu rt,rs,31` applied to a freshly-`lbu`-loaded byte (i.e. `decoded =
  raw+0x1F` as an instruction): **zero hits**. `addiu rt,rs,31` with `rs != 0` is ~0
  everywhere. The stored bytes are used **directly as glyph-table indices** — the `+0x1F`/
  `-1` rules in `decode_vp_a_with_font` are a coincidental fit for Latin letters (e.g.
  `0x2D→'L'` via `+0x1F`, `0x46→'E'` via `-1`) and explain why the two rules are
  inconsistent in direction. They break for punctuation / lowercase / control / Scheme B.
- **The byte→character mapping lives in the font, not in code.** Text is converted
  encoded-bytes → glyph index → font-atlas cell → GPU sprite. The accurate "codepage" is
  therefore the font atlas ordering, not a formula.
- **Text rendering/display subsystem located: sector 295321** (`load_text_file(0x240CC800)`,
  ~153 KB, loads ~`0x80030000`). It builds glyph GPU sprite primitives + runs a message-box
  state machine (renderer at blob `+0x18FC`; per-message struct fields at `+0x60..0x73`; 56
  `lbu→sll` index sites = byte→table-index lookups). ~30 small sibling overlays
  (sec 376xx/385xx/387xx/417xx, ~13–16 KB each) carry related text/script code.
- **Runtime `0x8007D` install question** stays emulator-only: running engine entry
  `0x8002F824` in isolation diverges before any installer (needs PCSX-Redux watchpoint).

### Recommended path to ACCURATE decoding (replace the +0x1F/-1 heuristic)
The bytes are font-table indices, so build a ground-truth **codepage table (byte → char)**:

1. **Extract the font glyph atlas (TIM).** Reuse the TIM tooling (`index_images.py` /
   `bin_navigator` `scan_slz_for_tims` / `/api/image`). The US/English release uses a small
   Latin font (≈64–128 glyphs), so the atlas is one screen of cells.
2. **Determine cell size + grid order** (row-major index → cell) from the TIM dimensions.
3. **Label cells → characters** once (by eye or template-match OCR) → the codepage.
4. **Confirm the byte→index transform authoritatively with the executor**: run sector
   295321's glyph-layout routine on each probe byte with `mem_watch` on the font-table
   region and read which atlas cell it fetches — removes any guesswork about index ordering
   / a fixed base offset.
5. **Cross-validate** against the known UI corpus (NEW GAME, CONTINUE, FREYA, character
   names, "TO ODIN THE ALL-FATHER", and the `LENNETH = 2D 46 4F 4F 46 55 49` sample).

This yields an exact `byte → char` table covering punctuation/control/special glyphs that the
current `+0x1F/-1` rules get wrong, and is far more reliable for finding/decoding story text.

### FONT ATLAS FOUND — image #662, sector 38061 (and duplicate #665, sector 38097)
TIM `256×128, 4bpp, 16 CLUTs`, source slz. Rendered via `render_tim.py` (reuses
`bin_navigator` TIM pipeline) and confirmed visually. It contains **two fonts + button
glyphs**, in reading order:
- **Italic proportional font** (top ~5 rows): `0-9`, punctuation (`! " # $ % & ' = - + /`
  …), `A–Z`, `a–z`, `( ) ·`, a few special glyphs, and a large italic `0–9` numeral set.
- **Bold fixed-ish font** (lower rows): punctuation, `0–9`, `: ; < = > ?`, `A–Z`.
- **Button icons**: `△ ○ X` (triangle/circle/cross).

The italic font is **proportional** (glyphs packed at variable widths — a 16px grid does NOT
align to glyph boundaries), so `byte → glyph` is NOT a simple row/col lookup; it needs the
renderer's glyph-width/offset table or an executor trace. All ~820 TIMs are dumped to
`tim_dump/` for reference; the renderer is `render_tim.py` (`one`/`sheet`/`dump` modes) and
`font_score.py` ranks by ink-color/transparency.

### CORRECTED CODEC — the decode is VALUE-based, not position-based (`codepage.py`)
The atlas (plain-ASCII repertoire) + a corpus analysis of 20,574 strings (`codepage_build.py`)
showed the real rule is **value-based on a 0x40 threshold**:

```
ascii = (raw + 0x1F) & 0xFF   if raw <  0x40
ascii = (raw - 1)    & 0xFF   if raw >= 0x40
# then ascii -> char (printable 0x20-0x7E); raw 0x01 -> 0x20 = space;
# ascii 0x0A = newline; bytes decoding to non-printable ascii are control codes.
```

This single rule reproduces `LENNETH`, `NEW GAME`, `POINTS`, `CARD SLOT 1`, etc. Why the old
rules were wrong:
- **"first byte +0x1F, rest -1"** was a coincidence — `LENNETH`'s first letter `L=0x2D` is
  `<0x40`; the rest are `>=0x40`. It also wrongly lowercased first letters `>=0x40` (`T→t`).
- **"`0x21-0x3A -> A-Z`" remap** in `decode_vp_scheme_a` was a static stand-in for the
  `raw<0x40` case and **corrupted digits** (raw `0x12`, a `'1'`, became `[11]`/a letter).
- `A-Z` has **two encodings** (`raw 0x22-0x3B` and `0x42-0x5B`) — likely italic vs bold font
  — but both decode to the same letter; the value rule handles both.

Result: readable strings **15,568 → 17,650 (+13%)** vs the old decoder, digits/case fixed.
`codepage.decode(raw)` is the corrected codec (supersedes `bin_navigator.decode_vp_scheme_a`).

### Control codes (characterised — `control_codes.py`)
By frequency / start-position / following-byte / context over the corpus:

| byte | role | evidence |
|------|------|----------|
| `0x01` | **space** (word separator) | `NEW`·`GAME`; ×48,939, the most common byte |
| `0x80` | **line break** (doubled = blank line / paragraph) | `SKILLED.◌◌IT IS ALSO`; now decoded as `\n` |
| `0x81 0x80 0x07` | **header** at string start (window / color / font) | leads menu items; `0x81` ×384 at start |
| `0x82-0x85, 0x90, 0xF1` | **inline-variable inserts** (HP/hits/energy/name) | `…9HITS}◌ }ENERGY…`, followed by space |
| `0xCE 0xC1 0xB9 0xAF …` | **rare start-markers** (icon / string-type) | always at position 0, single-byte strings |
| `0x0E='-' 0x0F='.' 0x0B='*'` | **literal punctuation**, NOT control | appear in `'()*+,-./` punctuation runs |

`codepage.decode` maps `0x80 → \n` and `0x01 → space`; other control bytes are shown as
`<xx>` markers (their precise game semantics — color indices, which variable, etc. — are not
all mapped). The header `0x07` and `0x81` before menu text are style controls but decode to
printable `&`/marker, so they're left visible rather than risk hiding a real `&`.
