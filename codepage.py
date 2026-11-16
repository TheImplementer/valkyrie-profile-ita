#!/usr/bin/env python3
"""Accurate VP "Scheme A" text codec.

The decode is VALUE-based (not position-based as previously thought):
    ascii = (raw + 0x1F) & 0xFF   if raw < 0x40
    ascii = (raw - 1)    & 0xFF   if raw >= 0x40
then ascii -> char (printable 0x20-0x7E). This single rule reproduces the whole
corpus: LENNETH, "NEW GAME", "POINTS", etc. The old "first byte +0x1F, rest -1"
was a coincidence (LENNETH's first letter L=0x2D happens to be <0x40); the old
"0x21-0x3A -> A-Z" remap was a static stand-in for the raw<0x40 case that corrupted
digits. A-Z has two encodings (raw 0x22-0x3B and 0x42-0x5B) — likely italic vs bold
font — but both decode to the same letter.

Bytes whose decoded ascii is non-printable are control codes (formatting / inline
variables); raw 0x01 -> 0x20 is a normal space.
"""

# Control codes that override the value rule (characterised from the corpus):
#   0x80 = line break (doubled => blank line);  0x01 stays a space via the value rule.
# Header (0x81/0x80/0x07 at string start = window/color/font) and inline-variable
# inserts (0x82-0x85, 0x90, 0xF1) and rare start-markers (0xCE/0xC1/...) are shown as
# <xx> markers — their exact game semantics are not all mapped.
RAW_CTRL = {0x80: "\n"}


def decode_char(raw_byte: int) -> int:
    """raw byte -> decoded ASCII code value."""
    return (raw_byte + 0x1F) & 0xFF if raw_byte < 0x40 else (raw_byte - 1) & 0xFF


def decode(raw: bytes, control_as="<{:02x}>") -> str:
    out = []
    for b in raw:
        if b in RAW_CTRL:
            out.append(RAW_CTRL[b]); continue
        d = decode_char(b)
        if 0x20 <= d <= 0x7E:
            out.append(chr(d))
        else:
            out.append(control_as.format(b))     # control code: show RAW byte
    return "".join(out)


def _buggy(raw):                                  # the previous bin_navigator rule
    out = []
    for j, b in enumerate(raw):
        d = (b + 0x1F) & 0xFF if j == 0 else (b - 1) & 0xFF
        if d == 0:
            out.append(" ")
        elif 0x21 <= d <= 0x3A:
            out.append(chr(ord("A") + d - 0x21))
        elif d == 0x0A:
            out.append("\n")
        elif 0x20 <= d < 0x80:
            out.append(chr(d))
        else:
            out.append(f"[{d:02x}]")
    return "".join(out)


if __name__ == "__main__":
    import bin_navigator as bn
    for name, hexs in [("LENNETH", "2d 46 4f 4f 46 55 49")]:
        got = decode(bytes.fromhex(hexs))
        print(f"{hexs} -> {got!r}  (want {name})")
        assert got == name

    bn._fh = open(bn.BIN_PATH, "rb")
    CORPUS = [408548, 173106220, 173121012, 173159840, 173178508, 173202508,
              173251972, 173294112, 173328196, 173351968, 173388080, 173402596,
              167348640]
    n = ok_new = ok_old = 0
    samples = []
    for off in CORPUS:
        dec = bn.load_text_file(off)["data"]
        for s in bn.parse_text_file(dec):
            raw = bytes.fromhex(s["raw_hex"]) if s["raw_hex"] else b""
            if not raw:
                continue
            n += 1
            new, old = decode(raw), _buggy(raw)
            ok_new += bn._looks_readable(new)
            ok_old += bn._looks_readable(old)
            if len(samples) < 14 and new != old and any(c.isdigit() for c in new) \
                    and bn._looks_readable(new):
                samples.append((s["raw_hex"][:30], old.replace("\n", "\\n"),
                                new.replace("\n", "\\n")))
    print(f"\nstrings={n}  readable(OLD)={ok_old}  readable(NEW)={ok_new}")
    print("\nstrings with digits where the rules differ (OLD had the digit bug):")
    for h, old, new in samples:
        print(f"  {h:<30} OLD {old!r:<26} NEW {new!r}")
