"""Tiny throwaway MIPS R3000 disassembler over EXE + overlay, for the loader trace."""
import struct

IMG = {
    "exe": ("SLUS_011.56", 0x80010000, 0x800),
    "ovl": ("overlay_engine_8002F824.bin", 0x8002F824, 0),
}
DATA = {k: open(p, "rb").read() for k, (p, _, _) in IMG.items()}
R = ['zero','at','v0','v1','a0','a1','a2','a3','t0','t1','t2','t3','t4','t5','t6','t7',
     's0','s1','s2','s3','s4','s5','s6','s7','t8','t9','k0','k1','gp','sp','fp','ra']


def which(ram):
    for k, (_, base, hdr) in IMG.items():
        d = DATA[k]
        if base <= ram < base + len(d) - hdr:
            return k, base, hdr
    return None


def word(ram):
    w = which(ram)
    if not w:
        return None
    k, base, hdr = w
    o = ram - base + hdr
    return struct.unpack('<I', DATA[k][o:o + 4])[0]


def dis(ram):
    x = word(ram)
    if x is None:
        return '(unmapped)'
    op = x >> 26; rs = (x >> 21) & 31; rt = (x >> 16) & 31; rd = (x >> 11) & 31
    sa = (x >> 6) & 31; f = x & 0x3f; imm = x & 0xffff
    si = imm - 0x10000 if imm & 0x8000 else imm
    jt = 0x80000000 | ((x & 0x3ffffff) << 2); bt = ram + 4 + si * 4
    if x == 0:
        return 'nop'
    if op == 0:
        m = {0x00: f'sll {R[rd]},{R[rt]},{sa}', 0x02: f'srl {R[rd]},{R[rt]},{sa}',
             0x03: f'sra {R[rd]},{R[rt]},{sa}', 0x04: f'sllv {R[rd]},{R[rt]},{R[rs]}',
             0x06: f'srlv {R[rd]},{R[rt]},{R[rs]}', 0x07: f'srav {R[rd]},{R[rt]},{R[rs]}',
             0x08: f'jr {R[rs]}', 0x09: f'jalr {R[rd]},{R[rs]}', 0x10: f'mfhi {R[rd]}',
             0x12: f'mflo {R[rd]}', 0x18: f'mult {R[rs]},{R[rt]}', 0x19: f'multu {R[rs]},{R[rt]}',
             0x1a: f'div {R[rs]},{R[rt]}', 0x1b: f'divu {R[rs]},{R[rt]}',
             0x20: f'add {R[rd]},{R[rs]},{R[rt]}', 0x21: f'addu {R[rd]},{R[rs]},{R[rt]}',
             0x22: f'sub {R[rd]},{R[rs]},{R[rt]}', 0x23: f'subu {R[rd]},{R[rs]},{R[rt]}',
             0x24: f'and {R[rd]},{R[rs]},{R[rt]}', 0x25: f'or {R[rd]},{R[rs]},{R[rt]}',
             0x26: f'xor {R[rd]},{R[rs]},{R[rt]}', 0x27: f'nor {R[rd]},{R[rs]},{R[rt]}',
             0x2a: f'slt {R[rd]},{R[rs]},{R[rt]}', 0x2b: f'sltu {R[rd]},{R[rs]},{R[rt]}'}
        return m.get(f, f'.word 0x{x:08x}')
    if op == 1:
        return (f'bltz {R[rs]},0x{bt:08x}' if rt == 0 else f'bgez {R[rs]},0x{bt:08x}')
    m = {0x02: f'j 0x{jt:08x}', 0x03: f'jal 0x{jt:08x}', 0x04: f'beq {R[rs]},{R[rt]},0x{bt:08x}',
         0x05: f'bne {R[rs]},{R[rt]},0x{bt:08x}', 0x06: f'blez {R[rs]},0x{bt:08x}',
         0x07: f'bgtz {R[rs]},0x{bt:08x}', 0x08: f'addi {R[rt]},{R[rs]},{si}',
         0x09: f'addiu {R[rt]},{R[rs]},{si}', 0x0a: f'slti {R[rt]},{R[rs]},{si}',
         0x0b: f'sltiu {R[rt]},{R[rs]},{si}', 0x0c: f'andi {R[rt]},{R[rs]},0x{imm:04x}',
         0x0d: f'ori {R[rt]},{R[rs]},0x{imm:04x}', 0x0e: f'xori {R[rt]},{R[rs]},0x{imm:04x}',
         0x0f: f'lui {R[rt]},0x{imm:04x}', 0x20: f'lb {R[rt]},{si}({R[rs]})',
         0x21: f'lh {R[rt]},{si}({R[rs]})', 0x23: f'lw {R[rt]},{si}({R[rs]})',
         0x24: f'lbu {R[rt]},{si}({R[rs]})', 0x25: f'lhu {R[rt]},{si}({R[rs]})',
         0x28: f'sb {R[rt]},{si}({R[rs]})', 0x29: f'sh {R[rt]},{si}({R[rs]})',
         0x2b: f'sw {R[rt]},{si}({R[rs]})'}
    return m.get(op, f'.word 0x{x:08x} (op{op:#04x})')


def func_start(addr, limit=600):
    a = addr
    for _ in range(limit):
        a -= 4
        x = word(a)
        if x is None:
            return addr - 0x80
        if (x >> 26) == 0x09 and ((x >> 21) & 31) == 29 and ((x >> 16) & 31) == 29 and (x & 0x8000):
            return a
    return addr - 0x80


def dump(start, label='', n=240, stop_at_jr=True):
    print(f'--- {label} 0x{start:08x} ---')
    a = start
    for _ in range(n):
        print(f'  0x{a:08x}: {dis(a)}')
        x = word(a)
        if stop_at_jr and x is not None and (x >> 26) == 0 and (x & 0x3f) == 0x08:
            print(f'  0x{a + 4:08x}: {dis(a + 4)}')
            break
        a += 4
