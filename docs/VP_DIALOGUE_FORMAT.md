# Valkyrie Profile (PSX, SLUS‑01156) — Dialogue Text Format

How story/cutscene dialogue is stored and encoded in `VALKYRIE.BIN`, and how to decode it.
Reverse‑engineered 2026‑06; this is the format the romhacking scene historically could not crack.

> **Note on two different text systems.** The game has **two** text encodings:
> - **UI / menu / item / skill text** ("Scheme A") — a *value‑based* rule
>   (`ascii = raw+0x1F if raw<0x40 else raw‑1`). Documented in `docs/DIALOGUE_SEARCH.md`.
> - **Story dialogue** — the *glyph‑index* format described in this document. Different and
>   unrelated to Scheme A. This is why dialogue never showed up in Scheme‑A scans.

---

## 1. Where it lives

- Dialogue is stored in **SLZ‑compressed containers** (the game's `SLZ01`/`SLZ02` =
  single/multi‑stream LZ). It is **not** listed in the sector‑0 file table (that table only
  covers the first ~30k sectors); containers are found by the `"SLZ"` magic and loaded
  per‑scene via the engine's runtime resource tables.
- **~249 dialogue‑bearing containers**, concentrated in **sectors ~155,000–170,000**, with
  per‑scene dialogue scattered out to ~290,000. Each scene's container holds that scene's
  lines **plus** the shared system strings (Artifact/Odin, item messages), so the system text
  recurs in many containers.
- On disc the text is **compressed**; at runtime it is **decompressed into a RAM buffer**,
  which is what the in‑game interpreter reads. (A 2 MB RAM dump during a dialog shows the
  decompressed buffer directly.)

A decompressed container is a sequence of **null‑terminated text records** (one record ≈ one
message/box, plus location names and system strings). [The exact record index — likely a
u32 pointer table at the head of the container, as in the UI format — was not fully verified;
records were extracted by `0x00` termination.]

### 1.1 The runtime load path (reverse‑engineered from the EXE + engine overlay)

The full pipeline, with addresses (EXE is resident at `0x80010000–0x8002f800`; the engine is an
overlay loaded at `0x8002F824`):

```
CD sectors ──libcd CdRead (EXE 0x8002714c)──► resident compressed buffers (~0x800e_0000)
   └─ called *indirectly* via a runtime function‑pointer table; the sector/resource lookup is
      only resolvable live (set a BP on 0x8002714c and read $ra). This is the one hop static
      analysis can't pin down.
engine resource dispatcher (overlay 0x80058c10)
   ├─ masks a type code from the resource word, indexes a global resource table (~0x8007C4CC)
   ├─ allocates a destination buffer via the game's malloc (EXE 0x80013104 → 0x80013204),
   │  sized from the SLZ header (+8)   ← this is the ~0x800c_0000 text buffer in RAM dumps
   └─ decompresses into it:
        open_resource_stream  (EXE 0x8001ab48)  — lazy/streamed: fills a descriptor struct
        decompress_resource   (EXE 0x800122c0)  — one‑shot
interpreter (overlay 0x8004c210 family) reads the decompressed buffer.
```

**`decompress_resource(dstA, container, dst, index)` @ `0x800122c0`** — `memcmp(container,"SLZ",3)`
(EXE `0x80023fa0`); walk `index` concatenated entries (advance by header `+12`); dispatch on the
type byte:

**SLZ entry header** (16 bytes, then payload at `+16`):

| off | field |
|----:|-------|
| +0  | `"SLZ"` magic |
| +3  | **type**: `0`=stored, `1`=single‑stream LZ, `2`=multi‑stream LZ |
| +8  | uncompressed length (u32) |
| +12 | entry size (u32) — step to next entry |

| type | codec | routine |
|----:|-------|---------|
| 0 | `memcpy` | EXE `0x80023ff0` |
| 1 | single‑stream LZ | EXE `0x800123cc` |
| 2 | multi‑stream LZ | EXE `0x80012874` |

The LZ matches `bin_navigator.py` exactly (flag bits LSB‑first, `flag&1`=literal, **offset 0 =
EOS**, 12‑bit offset, length = code+3 via jump table `0x8002a8d8`). The boot path also uses this:
EXE `0x800105dc` decompresses the **engine overlay itself** (it too is an SLZ resource).

> **Caller cross‑ref:** `decompress_resource` is called from EXE `0x800105dc` (boot) and overlay
> `0x80059234 / 0x8005ed30 / 0x8005ed50 / 0x8006c184`; `open_resource_stream` from overlay
> `0x800591d4`. To find which disc sector a scene's text comes from, BP `0x800122c0` (inspect
> `a1`=container `~0x800e_0000`, `dst`=text buffer `~0x800c_0000`, and `$ra`=the overlay loader),
> then BP `CdRead 0x8002714c` and read `$ra`/args for the sector + resource‑table lookup.

---

## 2. The byte‑stream encoding

The interpreter walks a record one byte at a time. Each byte is either a **glyph** or the
start of a **control command**, decided by the **high bit**:

```
b = next_byte()
if b == 0x00:            -> END OF STRING (terminator)
if (b & 0x80) == 0:      -> GLYPH:   b (0x00–0x7F) is a font glyph index → draw character
else:                    -> COMMAND: high bit set (0x80–0xFF), a 2‑byte (+optional args) escape
```

### Glyphs (high bit clear, `0x00–0x7F`)
The byte value is a **direct index into the font** — *not* ASCII, not Scheme‑A. The font is a
**~100‑glyph proportional font** (glyph atlas = TIM image **#662**, sector 38061; per‑glyph
**width table** at runtime address `0x800D93C4`, header count `0x64`=100). Index → font cell →
character. Because it's a raw cell index, the mapping is an arbitrary lookup table (the font's
own cell order), with no formula — see the codepage in §3.

> **⚠ The codepage is per‑font, not global.** The glyph index is a cell index into *whichever
> font atlas the scene loaded*, and different scenes load different atlases with different cell
> orders. So the same byte means different characters in different dumps. Example: a Freya
> monologue (RAM dump `vpram11.bin`, scene ~11) maps `space=0x05, T=0x11, e=0x0e, o=0x14`,
> whereas the §3 table below maps `space=0x31, T=0x32, e=0x34, o=0x3c` — and the difference is
> **not a constant offset**. Treat §3 as *the codepage for one font*. To decode an arbitrary
> dump, re‑crib its font first (see §4a / `crib_codepage.py`). Per‑font tables recovered so far:
> `vp_codepage.json` (font A) and `vp_codepage_scene11.json` (font B, the Freya/Chooser font).

### Commands (high bit set, `0x80–0xFF`)
A command is **two bytes** `[b1][b2]` (b1 has the high bit set), decoded as a 14‑bit code:

```
cmd = ((b1 & 0x7F) | (b2 << 7)) - 0x4000
```

`cmd` indexes a small dispatch table (jump table; ~9–20 entries observed). When `b2 == 0x80`
this simplifies to `cmd = b1 & 0x7F`. Some commands consume **additional argument bytes** after
the two (e.g. a value to insert). Confirmed:

- **`80 80` → command 0 = line break** (a doubled break = blank line / paragraph). This is the
  most common command and was rendered as `\n` when decoding.
- **`83 80 XX` → command 3 = a 3‑byte command** that consumes **one argument byte** `XX`. Very
  common; precedes most lines and appears inline (observed args `03 04 0a 0c 14 …`). Likely
  indent / color / per‑line style — semantics TBD, but the arg count (1) is confirmed.

Other commands (e.g. `92 80`, `91 80`, `8c 80`, `b4 80`, …) handle color, speed/wait, portrait,
speaker, and inline‑variable inserts (HP/level/name). Their exact semantics are **only partially
decoded** — they appear as `<xx>` markers if you keep them visible.

> **⚠ Argument bytes look like glyphs.** A command's trailing argument bytes are low‑valued
> (high bit clear), so a naïve decoder — or a crib that scans for glyph runs — will mistake them
> for characters. The interpreter doesn't, because it consumes them as part of the command; any
> decoder must walk the command stream and consume the same bytes. (This bit the first crib pass:
> the arg byte of a `83 80 XX` got mis‑mapped as the glyph 「. `crib_codepage.py` is now
> command‑aware and consumes args per the `ARG_BYTES` table.) Note also that **`「` is often not a
> glyph at all** — in some fonts it is drawn by a command, so it never appears in the glyph stream.

> The interpreter routines themselves were observed at (runtime, overlay‑dependent) addresses
> `0x8004c210 / 0x8004def8 / 0x80050fa8 / 0x80052ca0 / 0x8005497c` — each is the
> `lbu → andi 0x80 → (2‑byte combine) → dispatch` loop above. (A structurally identical routine
> exists in the field overlay at offset `+0xE220`.)

---

## 3. Codepage (glyph index → character) — *font A*

> This table is for **font A** only (the atlas loaded in the dump it was cribbed from). Other
> scenes use other fonts with different indices — see the warning in §2 and `crib_codepage.py`
> (§4a). A second table, `vp_codepage_scene11.json` (font B), is in the repo.

Empirically recovered (46 glyphs so far). `0x800D93C4` width table proves ~100 glyphs total;
the rest are digits, second‑font (bold) variants, and symbols not yet mapped.

| idx | ch | idx | ch | idx | ch | idx | ch |
|----:|:--:|----:|:--:|----:|:--:|----:|:--:|
| 0x0d| !  | 0x3f| .  | 0x4e| O  | 0x57| N  |
| 0x31| ␠  | 0x41| C  | 0x4f| R  | 0x58| V  |
| 0x32| T  | 0x42| u  | 0x50| E  | 0x5b| P  |
| 0x33| h  | 0x44| A  | 0x51| v  | 0x5c| '  |
| 0x34| e  | 0x45| f  | 0x52| w  | 0x5d| x  |
| 0x35| i  | 0x46| c  | 0x53| W  | 0x5e| M  |
| 0x36| t  | 0x48| l  | 0x54| ?  | 0x5f| S  |
| 0x37| m  | 0x49| g  | 0x56| Y  | 0x61| 「 |
| 0x38| a  | 0x4a| p  | 0x3a| b  | 0x62| I  |
| 0x39| s  | 0x4b| y  | 0x3b| r  | 0x64| ,  |
| 0x3c| o  | 0x4c| L  | 0x3d| k  | 0x65| H  |
| 0x3e| n  | 0x4d| d  |     |    |     |    |

Machine‑readable copy: `vp_codepage.json`.

**Not yet mapped** (the remaining ~10% of characters): digits `0–9`, a leading indent/marker
glyph, some capitals (`B`, `F`, `D`, `G`, `K`, `J`, …), lowercase `j q z`, and the bold/second
font. The dialogue's *prose* is essentially fully readable without them; they appear as `·`.

---

## 4. Decode algorithm (reference)

```python
ARG_BYTES = {3: 1}                          # command -> trailing arg bytes (default 0)

def decode(record_bytes, codepage):
    out = []
    i = 0
    while i < len(record_bytes):
        b = record_bytes[i]; i += 1
        if b == 0x00:                       # terminator
            break
        if b & 0x80:                        # command (2 bytes + optional args)
            b2 = record_bytes[i]; i += 1
            cmd = ((b & 0x7F) | (b2 << 7)) - 0x4000
            if cmd == 0:
                out.append('\n')            # line break
            else:
                out.append(f'<cmd {cmd}>')  # color/speed/var/etc (semantics TBD)
            i += ARG_BYTES.get(cmd, 0)      # consume arg bytes (e.g. cmd 3 = `83 80 XX`)
        else:                               # glyph
            out.append(codepage.get(b, '·'))
    return ''.join(out)
```

---

## 4a. Auto‑cribbing a font's codepage (`crib_codepage.py`)

Because the codepage is per‑font (see warning in §2), each new dump may need its own table.
`crib_codepage.py` recovers it automatically from a 2 MB RAM dump plus a transcript of what was
on screen — no manual byte hunting:

```bash
# lines exactly as shown on screen, separated by '|' (one display line each, no line breaks)
python3 crib_codepage.py vpram11.bin \
  --text "「That is your power.|The power to hear the sorrow, anger|and hopes of humans near death." \
  --out vp_codepage_scene11.json
# or: --textfile screen.txt  (one on-screen line per row)
```

How it works: it **tokenizes each record's glyph stream first** — walking the command escapes so
command bytes *and their argument bytes* are consumed, never mistaken for glyphs (see the arg‑byte
warning in §2). Then for each line it builds an **equality pattern** (which character positions
repeat, e.g. `sorrow → 0 1 2 2 1 3`) and slides it over the per‑line glyph segments. A line with
enough internal repetition matches in exactly one place, which pins `glyph_byte → char` for that
whole line. It merges all anchored lines (conflict‑checked), then decodes the enclosing record so
any glyph still unmapped shows up as `[xx]` in context — usually trivial to fill in
(e.g. `sou[07]s → 0x07=l`, `[32]t is → 0x32=I`). Give it the longest, most repetitive lines you can.

**Caveats** (learned the hard way):
- **Omit command‑drawn marks** from crib lines. `「` in particular is a *glyph* in some fonts but
  *command‑drawn* in others — if it's command‑drawn it isn't in the glyph stream, so a crib line
  that includes it won't match. Drop it and crib the rest of the line.
- **The high plane (`~0x60+`) is font‑specific, even when lowercase matches.** Two dumps that share
  the entire lowercase plane (`0x31–0x5f`) can still disagree above `0x60`: e.g. `0x62` = `I` in the
  Platina scene but `「` in the Jelanda scene; `0x66` = `F` vs `j`. So treat lowercase as the stable
  shared base and **re‑crib caps / digits / punctuation per scene**. Digits, for reference, were
  recovered from a `"10,000"` line (`0x03`=`0`, `0x04`=`1` in that font).
- A wrong `ARG_BYTES` entry desyncs the rest of a record; the tell is an unmapped glyph appearing
  mid‑word. Extend `ARG_BYTES` in `crib_codepage.py` as commands are decoded.

This is the same substitution‑crib that originally cracked the format (§5.2), packaged for reuse.

---

## 5. How it was reverse‑engineered (so it's reproducible)

1. **Format** — disassembled the message interpreter (the `lbu → andi 0x80 → 2‑byte combine →
   jump‑table` loop). This revealed glyph vs command and the 14‑bit command encoding.
2. **Codepage** — dialogue is compressed on disc but **decompressed in RAM**, so dumped 2 MB of
   PSX RAM (`PCSX.getMemPtr`) during a dialog with known on‑screen text. Found the buffer by a
   **substitution‑crib**: a known line ("Watch where you're going, girl!") has a unique
   case‑sensitive letter‑repeat pattern → exactly one matching byte run → glyph→char map fell out.
   Grew the table with more known lines (bijective, consistency‑checked) and a published
   transcript for confirmation.
3. **Disc location** — searched every decompressed SLZ container for the buffer's glyph‑index
   byte run → the source sector; then scored all containers by common‑word byte patterns to map
   the full dialogue region.

Tools: `vpram*.bin` (RAM dumps), `vp_codepage.json`, `script_dump/` (decoded per‑sector text),
and the crib/rip scripts in the repo. See `docs/DIALOGUE_SEARCH.md` "★ CRACKED" for the play‑by‑play.

---

## 6. Open items
- Map the remaining glyphs (digits, leading marker, a few caps, bold font) — easiest via a save
  state showing exact numbers on screen, then crib them with `crib_codepage.py` (§4a).
- **Per‑font codepages**: figure out *which* font a scene loads (font‑id / atlas pointer in the
  resource tables) so the right table can be selected automatically, instead of re‑cribbing each
  dump. So far two fonts are known (`vp_codepage.json`, `vp_codepage_scene11.json`).
- Enumerate the **command table**: what each `cmd` does (color/speed/portrait/wait/variable) and
  how many argument bytes each consumes — read the dispatch‑table handlers. *Known so far:* cmd 0
  = line break (0 args); **cmd 3 = `83 80 XX`, 1 arg byte** (indent/style?, args `03 04 0a 0c 14 …`).
  Tracked in `ARG_BYTES` in `crib_codepage.py`.
- Confirm the per‑container **record index** (pointer table vs pure null‑termination).
