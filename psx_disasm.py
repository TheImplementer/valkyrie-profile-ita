#!/usr/bin/env python3
"""MIPS R3000 (PSX) disassembler — reads a PSX EXE, writes MIPS assembly text."""

import struct
import sys
import os

MAGIC = b"PS-X EXE"
HEADER_SIZE = 0x800  # PSX EXE header is always one CD sector (2048 bytes)

REGS = [
    "$zero", "$at", "$v0", "$v1", "$a0", "$a1", "$a2", "$a3",
    "$t0",   "$t1", "$t2", "$t3", "$t4", "$t5", "$t6", "$t7",
    "$s0",   "$s1", "$s2", "$s3", "$s4", "$s5", "$s6", "$s7",
    "$t8",   "$t9", "$k0", "$k1", "$gp", "$sp", "$fp", "$ra",
]


def sign16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def decode_special(w: int, addr: int) -> str:
    rs    = (w >> 21) & 0x1F
    rt    = (w >> 16) & 0x1F
    rd    = (w >> 11) & 0x1F
    shamt = (w >>  6) & 0x1F
    fn    =  w        & 0x3F
    R = REGS
    match fn:
        case 0x00:
            if w == 0:
                return "nop"
            return f"sll    {R[rd]}, {R[rt]}, {shamt}"
        case 0x02: return f"srl    {R[rd]}, {R[rt]}, {shamt}"
        case 0x03: return f"sra    {R[rd]}, {R[rt]}, {shamt}"
        case 0x04: return f"sllv   {R[rd]}, {R[rt]}, {R[rs]}"
        case 0x06: return f"srlv   {R[rd]}, {R[rt]}, {R[rs]}"
        case 0x07: return f"srav   {R[rd]}, {R[rt]}, {R[rs]}"
        case 0x08: return f"jr     {R[rs]}"
        case 0x09:
            if rd == 31:
                return f"jalr   {R[rs]}"
            return f"jalr   {R[rd]}, {R[rs]}"
        case 0x0C: return "syscall"
        case 0x0D: return f"break  {(w >> 6) & 0xFFFFF}"
        case 0x10: return f"mfhi   {R[rd]}"
        case 0x11: return f"mthi   {R[rs]}"
        case 0x12: return f"mflo   {R[rd]}"
        case 0x13: return f"mtlo   {R[rs]}"
        case 0x18: return f"mult   {R[rs]}, {R[rt]}"
        case 0x19: return f"multu  {R[rs]}, {R[rt]}"
        case 0x1A: return f"div    {R[rs]}, {R[rt]}"
        case 0x1B: return f"divu   {R[rs]}, {R[rt]}"
        case 0x20: return f"add    {R[rd]}, {R[rs]}, {R[rt]}"
        case 0x21:
            if rt == 0:
                return f"move   {R[rd]}, {R[rs]}"
            return f"addu   {R[rd]}, {R[rs]}, {R[rt]}"
        case 0x22: return f"sub    {R[rd]}, {R[rs]}, {R[rt]}"
        case 0x23: return f"subu   {R[rd]}, {R[rs]}, {R[rt]}"
        case 0x24: return f"and    {R[rd]}, {R[rs]}, {R[rt]}"
        case 0x25:
            if rt == 0:
                return f"move   {R[rd]}, {R[rs]}"
            return f"or     {R[rd]}, {R[rs]}, {R[rt]}"
        case 0x26: return f"xor    {R[rd]}, {R[rs]}, {R[rt]}"
        case 0x27: return f"nor    {R[rd]}, {R[rs]}, {R[rt]}"
        case 0x2A: return f"slt    {R[rd]}, {R[rs]}, {R[rt]}"
        case 0x2B: return f"sltu   {R[rd]}, {R[rs]}, {R[rt]}"
        case _:    return f".word  0x{w:08X}  ; unknown SPECIAL funct=0x{fn:02X}"


def decode_regimm(w: int, addr: int) -> str:
    rs     = (w >> 21) & 0x1F
    rt     = (w >> 16) & 0x1F
    target = addr + 4 + (sign16(w & 0xFFFF) << 2)
    R = REGS
    match rt:
        case 0x00: return f"bltz   {R[rs]}, 0x{target:08X}"
        case 0x01: return f"bgez   {R[rs]}, 0x{target:08X}"
        case 0x10: return f"bltzal {R[rs]}, 0x{target:08X}"
        case 0x11: return f"bgezal {R[rs]}, 0x{target:08X}"
        case _:    return f".word  0x{w:08X}  ; unknown REGIMM rt=0x{rt:02X}"


def decode_cop0(w: int) -> str:
    rs = (w >> 21) & 0x1F
    rt = (w >> 16) & 0x1F
    rd = (w >> 11) & 0x1F
    fn =  w        & 0x3F
    R = REGS
    if rs == 0x00:   return f"mfc0   {R[rt]}, ${rd}"
    if rs == 0x04:   return f"mtc0   {R[rt]}, ${rd}"
    if rs == 0x10 and fn == 0x10: return "rfe"
    return f".word  0x{w:08X}  ; cop0 rs=0x{rs:02X}"


def decode_cop2(w: int) -> str:
    # GTE (Geometry Transform Engine)
    if w & (1 << 25):
        return f"cop2   0x{w & 0x1FFFFFF:07X}"
    rs = (w >> 21) & 0x1F
    rt = (w >> 16) & 0x1F
    rd = (w >> 11) & 0x1F
    R = REGS
    match rs:
        case 0x00: return f"mfc2   {R[rt]}, ${rd}"
        case 0x02: return f"cfc2   {R[rt]}, ${rd}"
        case 0x04: return f"mtc2   {R[rt]}, ${rd}"
        case 0x06: return f"ctc2   {R[rt]}, ${rd}"
        case _:    return f".word  0x{w:08X}  ; cop2 rs=0x{rs:02X}"


def disassemble_word(w: int, addr: int) -> str:
    op   = (w >> 26) & 0x3F
    rs   = (w >> 21) & 0x1F
    rt   = (w >> 16) & 0x1F
    imm  = sign16(w & 0xFFFF)
    immu = w & 0xFFFF
    # J-type: high 4 bits from PC, low 28 bits from instruction × 4
    jt   = (addr & 0xF0000000) | ((w & 0x3FFFFFF) << 2)
    bt   = addr + 4 + (imm << 2)
    R = REGS
    match op:
        case 0x00: return decode_special(w, addr)
        case 0x01: return decode_regimm(w, addr)
        case 0x02: return f"j      0x{jt:08X}"
        case 0x03: return f"jal    0x{jt:08X}"
        case 0x04:
            if rs == 0 and rt == 0:
                return f"b      0x{bt:08X}"
            return f"beq    {R[rs]}, {R[rt]}, 0x{bt:08X}"
        case 0x05: return f"bne    {R[rs]}, {R[rt]}, 0x{bt:08X}"
        case 0x06: return f"blez   {R[rs]}, 0x{bt:08X}"
        case 0x07: return f"bgtz   {R[rs]}, 0x{bt:08X}"
        case 0x08: return f"addi   {R[rt]}, {R[rs]}, {imm}"
        case 0x09:
            if rs == 0:
                return f"li     {R[rt]}, {imm}"
            return f"addiu  {R[rt]}, {R[rs]}, {imm}"
        case 0x0A: return f"slti   {R[rt]}, {R[rs]}, {imm}"
        case 0x0B: return f"sltiu  {R[rt]}, {R[rs]}, {immu}"
        case 0x0C: return f"andi   {R[rt]}, {R[rs]}, 0x{immu:04X}"
        case 0x0D:
            if rs == 0:
                return f"li     {R[rt]}, 0x{immu:04X}"
            return f"ori    {R[rt]}, {R[rs]}, 0x{immu:04X}"
        case 0x0E: return f"xori   {R[rt]}, {R[rs]}, 0x{immu:04X}"
        case 0x0F: return f"lui    {R[rt]}, 0x{immu:04X}"
        case 0x10: return decode_cop0(w)
        case 0x12: return decode_cop2(w)
        case 0x20: return f"lb     {R[rt]}, {imm}({R[rs]})"
        case 0x21: return f"lh     {R[rt]}, {imm}({R[rs]})"
        case 0x22: return f"lwl    {R[rt]}, {imm}({R[rs]})"
        case 0x23: return f"lw     {R[rt]}, {imm}({R[rs]})"
        case 0x24: return f"lbu    {R[rt]}, {imm}({R[rs]})"
        case 0x25: return f"lhu    {R[rt]}, {imm}({R[rs]})"
        case 0x26: return f"lwr    {R[rt]}, {imm}({R[rs]})"
        case 0x28: return f"sb     {R[rt]}, {imm}({R[rs]})"
        case 0x29: return f"sh     {R[rt]}, {imm}({R[rs]})"
        case 0x2A: return f"swl    {R[rt]}, {imm}({R[rs]})"
        case 0x2B: return f"sw     {R[rt]}, {imm}({R[rs]})"
        case 0x2E: return f"swr    {R[rt]}, {imm}({R[rs]})"
        case 0x30: return f"lwc0   {R[rt]}, {imm}({R[rs]})"
        case 0x32: return f"lwc2   {R[rt]}, {imm}({R[rs]})"
        case 0x38: return f"swc0   {R[rt]}, {imm}({R[rs]})"
        case 0x3A: return f"swc2   {R[rt]}, {imm}({R[rs]})"
        case _:    return f".word  0x{w:08X}  ; unknown opcode=0x{op:02X}"


def parse_header(data: bytes) -> tuple[int, int, int]:
    if data[:8] != MAGIC:
        raise ValueError(f"not a PSX EXE (bad magic: {data[:8]!r})")
    if len(data) < HEADER_SIZE:
        raise ValueError(f"file too small ({len(data)} bytes), expected at least {HEADER_SIZE}")
    pc        = struct.unpack_from("<I", data, 0x10)[0]
    load_addr = struct.unpack_from("<I", data, 0x18)[0]
    text_size = struct.unpack_from("<I", data, 0x1C)[0]
    return pc, load_addr, text_size


def disassemble(input_path: str, output_path: str,
                raw_base: int | None = None) -> None:
    with open(input_path, "rb") as f:
        data = f.read()

    if raw_base is not None:
        # Raw mode: treat the whole file as a header-less code blob loaded at
        # raw_base (e.g. a decompressed overlay).  No PSX-EXE header expected.
        pc, load_addr = raw_base, raw_base
        code = data
        text_size = len(data)
    else:
        pc, load_addr, text_size = parse_header(data)
        code = data[HEADER_SIZE : HEADER_SIZE + text_size]
        if len(code) < text_size:
            print(f"warning: header says {text_size} bytes, file only has {len(code)}", file=sys.stderr)
            text_size = len(code)

    lines = [
        f"; PSX disassembly: {os.path.basename(input_path)}",
        f"; Load address : 0x{load_addr:08X}",
        f"; Entry point  : 0x{pc:08X}",
        f"; Text size    : {text_size} bytes ({text_size // 4} instructions)",
        "",
    ]

    for offset in range(0, text_size & ~3, 4):
        addr = load_addr + offset
        (word,) = struct.unpack_from("<I", code, offset)
        mnemonic = disassemble_word(word, addr)
        ep = "  ; <-- entry" if addr == pc else ""
        lines.append(f"0x{addr:08X}:  {word:08X}    {mnemonic}{ep}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"wrote {text_size // 4} instructions to {output_path}")


def main() -> None:
    args = sys.argv[1:]
    raw_base = None
    if "--base" in args:
        bi = args.index("--base")
        raw_base = int(args[bi + 1], 0)          # accepts 0x... or decimal
        del args[bi:bi + 2]
    if "--raw" in args:                          # --raw is implied by --base
        args.remove("--raw")

    if not args:
        print(f"usage: {sys.argv[0]} <input> [output.txt] "
              f"[--base 0xADDR (raw header-less blob)]", file=sys.stderr)
        sys.exit(1)

    inp = args[0]
    out = args[1] if len(args) > 1 else os.path.splitext(inp)[0] + ".asm"

    try:
        disassemble(inp, out, raw_base=raw_base)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
