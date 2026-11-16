#!/usr/bin/env python3
"""Functional MIPS R3000 micro-executor for the PSX RE toolkit.

Runs individual routines in isolation over a flat 2 MB RAM model — no GPU/CD/BIOS
kernel emulation. Externals (calls outside loaded code, BIOS vectors) are stubbed.
COP0/COP2(GTE) are no-ops. Intended for: differential-validating the LZ decompressor
and probing specific routines with instrumented memory.

Run as a script => Phase-1 differential test of the type-1 decompressor (0x800123CC).
"""
import os
import struct
import sys

EXE_PATH = os.path.join(os.path.dirname(__file__), "SLUS_011.56")
ENGINE_BIN = os.path.join(os.path.dirname(__file__), "overlay_engine_8002F824.bin")

RAM_SIZE = 0x200000                 # 2 MB PSX main RAM
RAM_MASK = RAM_SIZE - 1
SENTINEL = 0xDEAD0000               # $ra marker: returning here ends run_call
M32 = 0xFFFFFFFF


def s16(v): return v - 0x10000 if v & 0x8000 else v
def s32(v): return v - 0x100000000 if v & 0x80000000 else v


class CPU:
    def __init__(self):
        self.ram = bytearray(RAM_SIZE)
        self.reg = [0] * 32
        self.hi = self.lo = 0
        self.pc = 0
        self.next_pc = 0
        self.loaded = []            # list of (lo, hi) code ranges
        self.watch = None           # (lo, hi) or None
        self.mem_log = []
        self.stubs = []             # (pc, target) external calls stubbed
        self.halt = None

    # ── memory (little-endian, mirror-folded) ────────────────────────────────
    def _i(self, a): return a & RAM_MASK

    def load32(self, a):
        i = a & RAM_MASK
        v = int.from_bytes(self.ram[i:i + 4], "little")
        if self.watch and self.watch[0] <= a < self.watch[1]:
            self.mem_log.append((self.pc, "R", a, 4, v))
        return v

    def load16u(self, a):
        i = a & RAM_MASK
        v = self.ram[i] | (self.ram[i + 1] << 8)
        self._w(a, "R", 2, v); return v

    def load8u(self, a):
        v = self.ram[a & RAM_MASK]
        self._w(a, "R", 1, v); return v

    def store32(self, a, v):
        v &= M32
        i = a & RAM_MASK
        self.ram[i:i + 4] = v.to_bytes(4, "little")
        self._w(a, "W", 4, v)

    def store16(self, a, v):
        v &= 0xFFFF
        i = a & RAM_MASK
        self.ram[i] = v & 0xFF
        self.ram[i + 1] = (v >> 8) & 0xFF
        self._w(a, "W", 2, v)

    def store8(self, a, v):
        self.ram[a & RAM_MASK] = v & 0xFF
        self._w(a, "W", 1, v & 0xFF)

    def _w(self, a, rw, sz, v):
        if self.watch and self.watch[0] <= a < self.watch[1]:
            self.mem_log.append((self.pc, rw, a, sz, v))

    # unaligned (verified little-endian formulas)
    def lwl(self, rt, a):
        sh = 24 - 8 * (a & 3)
        mem = self.load32(a & ~3)
        return ((rt & (~(M32 << sh) & M32)) | ((mem << sh) & M32)) & M32

    def lwr(self, rt, a):
        sh = 8 * (a & 3)
        mem = self.load32(a & ~3)
        return ((rt & (~(M32 >> sh) & M32)) | (mem >> sh)) & M32

    def swl(self, rt, a):
        sh = 24 - 8 * (a & 3)
        al = a & ~3
        mem = self.load32(al)
        self.store32(al, (mem & (~(M32 >> sh) & M32)) | (rt >> sh))

    def swr(self, rt, a):
        sh = 8 * (a & 3)
        al = a & ~3
        mem = self.load32(al)
        self.store32(al, (mem & (~(M32 << sh) & M32)) | ((rt << sh) & M32))

    # ── loading ──────────────────────────────────────────────────────────────
    def load_region(self, addr, data):
        i = addr & RAM_MASK
        self.ram[i:i + len(data)] = data
        self.loaded.append((addr, addr + len(data)))

    def is_code(self, a):
        return any(lo <= a < hi for lo, hi in self.loaded)

    # ── execution ─────────────────────────────────────────────────────────────
    def run_call(self, entry, a0=0, a1=0, a2=0, a3=0,
                 max_instrs=50_000_000, watch=None):
        self.reg = [0] * 32
        self.reg[4], self.reg[5], self.reg[6], self.reg[7] = a0, a1, a2, a3
        self.reg[29] = 0x801FFF00            # $sp
        self.reg[31] = SENTINEL              # $ra
        self.hi = self.lo = 0
        self.pc, self.next_pc = entry, entry + 4
        self.watch, self.mem_log, self.stubs, self.halt = watch, [], [], None

        n = 0
        while n < max_instrs:
            if self.pc == SENTINEL:
                self.halt = "return"
                break
            if self.halt:
                break
            self._step()
            self.reg[0] = 0
            n += 1
        else:
            self.halt = "guard"
        return {"regs": list(self.reg), "hi": self.hi, "lo": self.lo,
                "instr_count": n, "stubbed": self.stubs,
                "halt": self.halt, "mem_log": self.mem_log}

    def _branch(self, taken, target):
        if taken:
            self.next_pc = target & M32

    def _call_or_stub(self, target, link_reg=None):
        """Handle jal/jalr target: real jump if loaded, else stub (return 0)."""
        if self.is_code(target):
            if link_reg is not None:
                self.reg[link_reg] = self.next_pc & M32   # addr after delay slot
            self.next_pc = target & M32
        else:                                              # external / BIOS
            self.stubs.append((self.pc, target))
            self.reg[2] = 0                                # $v0 = 0
            # do NOT jump: delay slot still runs, call is a no-op

    def _step(self):
        w = self.load32(self.pc)
        r = self.reg
        op = w >> 26
        # advance (delay-slot model)
        cur = self.pc
        self.pc = self.next_pc
        self.next_pc = (self.next_pc + 4) & M32

        if op == 0:                                        # SPECIAL
            self._special(w, r)
        elif op == 1:                                      # REGIMM
            rs = (w >> 21) & 31
            rt = (w >> 16) & 31
            off = s16(w & 0xFFFF) << 2
            tgt = (cur + 4 + off) & M32
            val = s32(r[rs])
            if rt == 0x00:   self._branch(val < 0, tgt)              # bltz
            elif rt == 0x01: self._branch(val >= 0, tgt)            # bgez
            elif rt == 0x10:                                         # bltzal
                r[31] = self.next_pc; self._branch(val < 0, tgt)
            elif rt == 0x11:                                         # bgezal
                r[31] = self.next_pc; self._branch(val >= 0, tgt)
        elif op == 2:                                      # j
            tgt = ((cur + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2)
            if self.is_code(tgt):
                self.next_pc = tgt
            else:
                self.stubs.append((cur, tgt)); self.next_pc = r[31] & M32
        elif op == 3:                                      # jal
            tgt = ((cur + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2)
            self._call_or_stub(tgt, link_reg=31)
        else:
            self._itype(w, op, r, cur)

    def _special(self, w, r):
        rs = (w >> 21) & 31; rt = (w >> 16) & 31; rd = (w >> 11) & 31
        sh = (w >> 6) & 31; fn = w & 0x3F
        if fn == 0x00:   r[rd] = (r[rt] << sh) & M32                 # sll
        elif fn == 0x02: r[rd] = (r[rt] & M32) >> sh                 # srl
        elif fn == 0x03: r[rd] = (s32(r[rt]) >> sh) & M32            # sra
        elif fn == 0x04: r[rd] = (r[rt] << (r[rs] & 31)) & M32       # sllv
        elif fn == 0x06: r[rd] = (r[rt] & M32) >> (r[rs] & 31)       # srlv
        elif fn == 0x07: r[rd] = (s32(r[rt]) >> (r[rs] & 31)) & M32  # srav
        elif fn == 0x08: self.next_pc = r[rs] & M32                  # jr
        elif fn == 0x09:                                             # jalr
            tgt = r[rs] & M32
            if self.is_code(tgt):
                r[rd] = self.next_pc; self.next_pc = tgt
            else:
                self.stubs.append((self.pc, tgt)); r[2] = 0
        elif fn == 0x0C: self.halt = "syscall"                       # syscall
        elif fn == 0x0D: self.halt = "break"                        # break
        elif fn == 0x10: r[rd] = self.hi                            # mfhi
        elif fn == 0x11: self.hi = r[rs]                            # mthi
        elif fn == 0x12: r[rd] = self.lo                            # mflo
        elif fn == 0x13: self.lo = r[rs]                            # mtlo
        elif fn == 0x18:                                            # mult
            p = s32(r[rs]) * s32(r[rt]); self.lo = p & M32; self.hi = (p >> 32) & M32
        elif fn == 0x19:                                            # multu
            p = (r[rs] & M32) * (r[rt] & M32); self.lo = p & M32; self.hi = (p >> 32) & M32
        elif fn == 0x1A:                                            # div
            a, b = s32(r[rs]), s32(r[rt])
            if b == 0:
                self.lo = M32 if a >= 0 else 1; self.hi = a & M32
            else:
                q = int(a / b) if (a < 0) ^ (b < 0) else a // b
                self.lo = q & M32; self.hi = (a - q * b) & M32
        elif fn == 0x1B:                                            # divu
            a, b = r[rs] & M32, r[rt] & M32
            if b == 0: self.lo = M32; self.hi = a
            else: self.lo = (a // b) & M32; self.hi = (a % b) & M32
        elif fn == 0x20: r[rd] = (s32(r[rs]) + s32(r[rt])) & M32     # add
        elif fn == 0x21: r[rd] = (r[rs] + r[rt]) & M32              # addu
        elif fn == 0x22: r[rd] = (s32(r[rs]) - s32(r[rt])) & M32     # sub
        elif fn == 0x23: r[rd] = (r[rs] - r[rt]) & M32              # subu
        elif fn == 0x24: r[rd] = r[rs] & r[rt]                      # and
        elif fn == 0x25: r[rd] = (r[rs] | r[rt]) & M32             # or
        elif fn == 0x26: r[rd] = (r[rs] ^ r[rt]) & M32             # xor
        elif fn == 0x27: r[rd] = (~(r[rs] | r[rt])) & M32          # nor
        elif fn == 0x2A: r[rd] = 1 if s32(r[rs]) < s32(r[rt]) else 0  # slt
        elif fn == 0x2B: r[rd] = 1 if (r[rs] & M32) < (r[rt] & M32) else 0  # sltu
        # else: unknown special -> ignore (treated as nop)

    def _itype(self, w, op, r, cur):
        rs = (w >> 21) & 31; rt = (w >> 16) & 31
        imm = w & 0xFFFF; simm = s16(imm)
        addr = (r[rs] + simm) & M32
        btgt = (cur + 4 + (simm << 2)) & M32
        if op == 0x04:   self._branch(r[rs] == r[rt], btgt)         # beq
        elif op == 0x05: self._branch(r[rs] != r[rt], btgt)        # bne
        elif op == 0x06: self._branch(s32(r[rs]) <= 0, btgt)       # blez
        elif op == 0x07: self._branch(s32(r[rs]) > 0, btgt)        # bgtz
        elif op == 0x08: r[rt] = (r[rs] + simm) & M32              # addi
        elif op == 0x09: r[rt] = (r[rs] + simm) & M32              # addiu
        elif op == 0x0A: r[rt] = 1 if s32(r[rs]) < simm else 0     # slti
        elif op == 0x0B: r[rt] = 1 if (r[rs] & M32) < (simm & M32) else 0  # sltiu
        elif op == 0x0C: r[rt] = r[rs] & imm                       # andi
        elif op == 0x0D: r[rt] = (r[rs] | imm) & M32              # ori
        elif op == 0x0E: r[rt] = (r[rs] ^ imm) & M32             # xori
        elif op == 0x0F: r[rt] = (imm << 16) & M32                # lui
        elif op == 0x10 or op == 0x12:                            # COP0 / COP2(GTE): no-op
            pass
        elif op == 0x20:                                         # lb
            v = self.load8u(addr); r[rt] = (v - 0x100 if v & 0x80 else v) & M32
        elif op == 0x21:                                          # lh
            v = self.load16u(addr); r[rt] = (v - 0x10000 if v & 0x8000 else v) & M32
        elif op == 0x22: r[rt] = self.lwl(r[rt], addr)            # lwl
        elif op == 0x23: r[rt] = self.load32(addr)                # lw
        elif op == 0x24: r[rt] = self.load8u(addr)                # lbu
        elif op == 0x25: r[rt] = self.load16u(addr)               # lhu
        elif op == 0x26: r[rt] = self.lwr(r[rt], addr)            # lwr
        elif op == 0x28: self.store8(addr, r[rt])                 # sb
        elif op == 0x29: self.store16(addr, r[rt])               # sh
        elif op == 0x2A: self.swl(r[rt], addr)                   # swl
        elif op == 0x2B: self.store32(addr, r[rt])               # sw
        elif op == 0x2E: self.swr(r[rt], addr)                   # swr
        elif op in (0x32, 0x3A):                                  # lwc2 / swc2 (GTE): no-op
            pass
        # else: unknown -> nop


# ── factory: a CPU preloaded with the EXE + engine overlay ────────────────────
def make_cpu():
    cpu = CPU()
    with open(EXE_PATH, "rb") as f:
        exe = f.read()
    text = exe[0x800:0x800 + 0x1F800]
    cpu.load_region(0x80010000, text)
    with open(ENGINE_BIN, "rb") as f:
        cpu.load_region(0x8002F824, f.read())
    return cpu


# ── Phase 1: differential validation of decompressor 0x800123CC ───────────────
def phase1():
    import bin_navigator as bn
    bn._fh = open(bn.BIN_PATH, "rb")
    off = 0x049B1800                              # engine overlay SLZ container
    meta = bn.load_text_file(off)
    assert meta["slz_type"] == 1, f"expected type1, got {meta['slz_type']}"
    comp = bn.read_at(off + 16, meta["comp_size"])
    truth = meta["data"]
    print(f"comp_size={len(comp)}  decomp_size={len(truth)} (declared {meta['decomp_size']})")

    cpu = make_cpu()
    SRC = 0x80140000
    DST = 0x80090000
    assert DST + len(truth) < SRC, "dest overruns src"
    assert SRC + len(comp) < 0x801F0000, "src overruns stack"
    cpu.ram[SRC & RAM_MASK: (SRC & RAM_MASK) + len(comp)] = comp

    res = cpu.run_call(0x800123CC, a1=SRC, a2=DST, max_instrs=80_000_000)
    out = bytes(cpu.ram[DST & RAM_MASK: (DST & RAM_MASK) + len(truth)])

    print(f"halt={res['halt']}  instrs={res['instr_count']:,}  $v0={res['regs'][2]}  "
          f"stubs={len(res['stubbed'])}")
    if out == truth:
        print(f"PHASE 1: PASS — {len(out):,} bytes match bin_navigator.slz1_decompress "
              f"byte-for-byte. Type-1 confirmed single-stream.")
        return True
    # diagnose first mismatch
    m = next((i for i in range(min(len(out), len(truth))) if out[i] != truth[i]), None)
    print(f"PHASE 1: FAIL — first mismatch at offset {m} "
          f"(got {out[m]:#04x} vs {truth[m]:#04x})" if m is not None
          else f"PHASE 1: FAIL — length differs (got {len(out)} vs {len(truth)})")
    return False


if __name__ == "__main__":
    ok = phase1()
    sys.exit(0 if ok else 1)
