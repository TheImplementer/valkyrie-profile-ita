#!/usr/bin/env python3
"""Auto-crib a Valkyrie Profile dialogue codepage from a RAM dump + on-screen text.

The story-dialogue glyph stream is a raw font-cell index, and the font atlas is
loaded *per scene* — so the glyph->char map is font-dependent, not global (see
VP_DIALOGUE_FORMAT.md). This tool recovers the codepage for whatever font is in a
given 2 MB PSX RAM dump, using only a transcript of what was on screen.

Method (the "substitution crib"):
  1. Tokenize each null-terminated record into its *glyph* stream, walking the
     command escapes so command bytes — and their ARGUMENT bytes — are consumed,
     never mistaken for glyphs (see ARG_BYTES). Line-break commands split the
     stream into per-line glyph segments; other inline commands are skipped
     transparently.
  2. For each on-screen line, build its equality pattern (which character
     positions repeat, e.g. 'sorrow' -> [0,1,2,2,1,3]) and slide it over the glyph
     segments. A line with enough internal repetition matches in exactly one place,
     which pins glyph_byte -> char for that whole line.
  3. Merge the mappings from all anchored lines (conflict-checked), then decode the
     enclosing record so any glyph still unmapped shows up as [xx] in context.

Note: only *glyphs* are cribbable this way. Marks the engine draws via a command
(e.g. a leading 「 in some fonts) are not in the glyph stream — omit them from the
crib line, or it will fail to match. The high plane (caps / digits / punctuation,
~0x60+) and the command set both vary per font, so re-crib per scene.

Usage:
  python3 crib_codepage.py dump.bin --text "Line one|Line two|Line three"
  python3 crib_codepage.py dump.bin --textfile screen.txt [--out cp.json]

Lines are separated by '|' (--text) or newlines (--textfile). Give the lines exactly
as they appear on screen, one display line each; do NOT include line-break commands.
"""
import argparse
import io
import json
import sys

# Command index -> number of ARGUMENT bytes it consumes *after* its 2 escape bytes.
# Commands default to 0 args. Known so far (see VP_DIALOGUE_FORMAT.md §2):
#   cmd 0  = line break (`80 80`), handled as a segment separator below.
#   cmd 3  = `83 80 XX`, a 3-byte command that consumes one argument byte.
# Extend this as more commands are decoded; a wrong count desyncs the rest of a
# record, so an unmapped glyph suddenly appearing mid-line is the tell.
ARG_BYTES = {3: 1}


def load(path):
    with open(path, "rb") as f:
        return f.read()


def equality_pattern(s):
    """Map each char to the index of its first appearance: 'sorrow'->[0,1,2,2,1,3]."""
    seen, out = {}, []
    for c in s:
        out.append(seen.setdefault(c, len(seen)))
    return out


def walk(rec):
    """Tokenize a record. Yields, in order:
        ('glyph', value, local_offset)  for a glyph byte (high bit clear)
        ('sep',)                        for a line-break command (cmd 0)
        ('cmd', cmd, args_bytes)        for any other command, args already consumed
    """
    i, n = 0, len(rec)
    while i < n:
        b = rec[i]
        if b & 0x80:                              # command: high bit set on byte 1
            b2 = rec[i + 1] if i + 1 < n else 0
            cmd = ((b & 0x7F) | (b2 << 7)) - 0x4000
            i += 2
            if cmd == 0:
                yield ("sep",)
            else:
                k = ARG_BYTES.get(cmd, 0)
                yield ("cmd", cmd, rec[i:i + k])
                i += k
        else:                                     # glyph: high bit clear
            yield ("glyph", b, i)
            i += 1


def segments(rec):
    """Per-line runs of (glyph_value, local_offset), split on line-break commands;
    other commands are skipped transparently (they don't break a line)."""
    seg = []
    for ev in walk(rec):
        if ev[0] == "sep":
            if seg:
                yield seg
            seg = []
        elif ev[0] == "glyph":
            seg.append((ev[1], ev[2]))
    if seg:
        yield seg


def iter_records(data):
    """Yield (abs_start, record_bytes) for every null-terminated chunk."""
    start = 0
    while start < len(data):
        end = data.find(b"\x00", start)
        if end == -1:
            end = len(data)
        yield start, data[start:end]
        start = end + 1


def _matches(pat, win):
    """True if byte window `win` follows equality pattern `pat` bijectively."""
    fwd, rev = {}, {}
    for a, b in zip(pat, win):
        if a in fwd:
            if fwd[a] != b:
                return False
        elif b in rev:                            # two different chars sharing a byte
            return False
        else:
            fwd[a], rev[b] = b, a
    return True


def find_line(data, line):
    """Locate `line` in the glyph stream. Returns list of (abs_offset, glyph_bytes),
    one per unique place its repeat-pattern matches within a single display line."""
    pat = equality_pattern(line)
    n = len(pat)
    hits = []
    for base, rec in iter_records(data):
        for seg in segments(rec):
            vals = [v for v, _ in seg]
            for s in range(len(vals) - n + 1):
                win = vals[s:s + n]
                if _matches(pat, win):
                    hits.append((base + seg[s][1], win))
    return hits


def decode_record(rec, cp):
    """Decode a null-trimmed record; returns (text, set_of_unmapped_bytes)."""
    out, unmapped = [], set()
    for ev in walk(rec):
        if ev[0] == "sep":
            out.append("\n")
        elif ev[0] == "cmd":
            cmd, a = ev[1], ev[2]
            out.append(f"<{cmd}:{a.hex()}>" if a else f"<{cmd}>")
        else:
            b = ev[1]
            out.append(cp.get(b, f"[{b:02x}]"))
            if b not in cp:
                unmapped.add(b)
    return "".join(out), unmapped


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", help="2 MB PSX RAM dump (vpram*.bin)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--text", help="on-screen lines separated by '|'")
    g.add_argument("--textfile", help="file with one on-screen line per row")
    ap.add_argument("--out", help="write derived codepage JSON here")
    args = ap.parse_args()

    data = load(args.dump)
    if args.text:
        lines = [l for l in args.text.split("|") if l]
    else:
        with io.open(args.textfile, encoding="utf-8") as f:
            lines = [l.rstrip("\n") for l in f if l.strip()]

    cp = {}
    anchors = []
    for line in lines:
        hits = find_line(data, line)
        if len(hits) != 1:
            print(f"  skip (matched {len(hits)}x, not unique): {line!r}")
            continue
        off, glyph_bytes = hits[0]
        anchors.append(off)
        for c, b in zip(line, glyph_bytes):       # glyph_bytes excludes command args
            if b in cp and cp[b] != c:
                print(f"  CONFLICT 0x{b:02x}: {cp[b]!r} vs {c!r} (line {line!r})")
            else:
                cp.setdefault(b, c)
        print(f"  anchored @ 0x{off:05x}: {line!r}")

    if not anchors:
        print("No line could be uniquely located. Provide a longer line with more "
              "repeated characters, omit command-drawn marks (e.g. a leading 「), or "
              "check the transcript.")
        sys.exit(1)

    # Decode the record that contains the first anchor, to surface remaining glyphs.
    a = anchors[0]
    start = data.rfind(b"\x00", 0, a) + 1
    end = data.find(b"\x00", a)
    text, unmapped = decode_record(data[start:end], cp)

    print(f"\nrecord @ 0x{start:05x}  ({end - start} bytes)")
    print("-" * 40)
    sys.stdout.buffer.write(text.encode("utf-8", "replace"))
    print("\n" + "-" * 40)
    print(f"glyphs mapped: {len(cp)}")
    if unmapped:
        print("still unmapped (infer from the [xx] context above):",
              ", ".join(f"0x{b:02x}" for b in sorted(unmapped)))

    if args.out:
        obj = {f"0x{k:02x}": v for k, v in sorted(cp.items())}
        json.dump(obj, io.open(args.out, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
