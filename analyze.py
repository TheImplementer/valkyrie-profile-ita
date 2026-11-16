#!/usr/bin/env python3
"""Analyze a psx_disasm.py output file and print a high-level report."""

import re
import sys
from collections import defaultdict, Counter

# ── regex helpers ────────────────────────────────────────────────────────────
RE_LINE    = re.compile(r'^(0x[0-9A-Fa-f]{8}):\s+[0-9A-Fa-f]{8}\s+(\S+)(.*)')
RE_JAL     = re.compile(r'^jal\s+(0x[0-9A-Fa-f]+)')
RE_BRANCH  = re.compile(r'^(b(?:eq|ne|lez|gtz|ltz|gez|ltzal|gezal)?)$')
RE_LOAD    = re.compile(r'^(lb|lbu|lh|lhu|lw|lwl|lwr|lwc\d)$')
RE_STORE   = re.compile(r'^(sb|sh|sw|swl|swr|swc\d)$')
RE_COP2    = re.compile(r'^(cop2|mfc2|cfc2|mtc2|ctc2|lwc2|swc2)$')
RE_ENTRY   = re.compile(r'<-- entry')


def parse(path: str):
    funcs        = {}           # addr -> {instrs:[], calls:Counter}
    call_count   = Counter()   # callee addr -> times called
    jal_sites    = []          # (caller_addr, callee_addr)
    instr_freq   = Counter()
    category     = Counter()
    entry_pc     = None
    cur_func     = None
    cur_start    = None
    prev_mnem    = None
    all_addrs    = []

    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip()

            # grab entry point from header comment
            if line.startswith("; Entry point"):
                m = re.search(r'0x[0-9A-Fa-f]+', line)
                if m:
                    entry_pc = int(m.group(), 16)
                continue

            m = RE_LINE.match(line)
            if not m:
                continue

            addr   = int(m.group(1), 16)
            mnem   = m.group(2).lower()
            rest   = m.group(3)
            full   = (mnem + rest).strip()
            all_addrs.append(addr)

            instr_freq[mnem] += 1

            # categorise
            if mnem == "nop":
                category["nop"] += 1
            elif mnem == ".word":
                category["data_word"] += 1
            elif RE_COP2.match(mnem):
                category["gte"] += 1
            elif mnem in ("jal", "jalr"):
                category["call"] += 1
            elif mnem in ("jr",):
                category["return"] += 1
            elif mnem in ("j",):
                category["jump"] += 1
            elif RE_BRANCH.match(mnem) or mnem == "b":
                category["branch"] += 1
            elif RE_LOAD.match(mnem):
                category["load"] += 1
            elif RE_STORE.match(mnem):
                category["store"] += 1
            elif mnem in ("add","addu","sub","subu","addi","addiu","li","mult","multu","div","divu"):
                category["arith"] += 1
            elif mnem in ("and","or","xor","nor","andi","ori","xori","sll","srl","sra","sllv","srlv","srav","lui"):
                category["logic_shift"] += 1
            elif mnem in ("slt","sltu","slti","sltiu"):
                category["compare"] += 1
            elif mnem in ("mfhi","mflo","mthi","mtlo"):
                category["hilo"] += 1
            elif mnem in ("move",):
                category["move"] += 1
            elif mnem.startswith("mfc") or mnem.startswith("mtc") or mnem == "rfe":
                category["cop0"] += 1
            else:
                category["other"] += 1

            # ── function boundary detection ──────────────────────────────────
            # A new function starts at:
            #   • the entry point
            #   • any jal target (collected on first pass — we use a second pass below)
            if addr == entry_pc and cur_func is None:
                cur_func  = addr
                cur_start = addr
                funcs[addr] = {"start": addr, "instrs": 0, "calls": Counter(), "returns": 0}

            if cur_func is not None:
                funcs[cur_func]["instrs"] += 1
                if mnem == "jr" and "$ra" in rest:
                    funcs[cur_func]["returns"] += 1

            # track jal calls
            mj = RE_JAL.match(full)
            if mj:
                callee = int(mj.group(1), 16)
                call_count[callee] += 1
                jal_sites.append((addr, callee))
                if cur_func is not None:
                    funcs[cur_func]["calls"][callee] += 1

            prev_mnem = mnem

    return {
        "funcs":      funcs,
        "call_count": call_count,
        "jal_sites":  jal_sites,
        "instr_freq": instr_freq,
        "category":   category,
        "entry_pc":   entry_pc,
        "all_addrs":  all_addrs,
    }


def second_pass_functions(data):
    """Re-partition into functions using all known jal targets + entry."""
    jal_targets = set(callee for _, callee in data["jal_sites"])
    if data["entry_pc"]:
        jal_targets.add(data["entry_pc"])

    addrs = data["all_addrs"]
    addr_set = set(addrs)

    # sort targets that actually appear in the binary
    starts = sorted(a for a in jal_targets if a in addr_set)

    # build addr→index map
    idx = {a: i for i, a in enumerate(addrs)}

    funcs = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else addrs[-1] + 4
        si  = idx.get(start, 0)
        ei  = idx.get(end, len(addrs))
        size = ei - si
        funcs.append({"start": start, "size": size})

    return funcs


def report(path: str):
    data  = parse(path)
    funcs = second_pass_functions(data)

    total      = len(data["all_addrs"])
    cat        = data["category"]
    freq       = data["instr_freq"]
    call_count = data["call_count"]

    func_sizes = [f["size"] for f in funcs if f["size"] > 0]
    func_sizes.sort()

    print("=" * 60)
    print("  PSX DISASSEMBLY REPORT")
    print(f"  File : {path}")
    print("=" * 60)

    print(f"\n── Overview ─────────────────────────────────────────────")
    print(f"  Total words decoded  : {total:,}")
    print(f"  Data words (.word)   : {cat['data_word']:,}  ({cat['data_word']/total*100:.1f}%)")
    print(f"  Code instructions    : {total - cat['data_word']:,}")
    print(f"  Entry point          : 0x{data['entry_pc']:08X}" if data['entry_pc'] else "  Entry point: unknown")

    print(f"\n── Functions ────────────────────────────────────────────")
    print(f"  Identified functions : {len(funcs):,}  (via JAL targets + entry)")
    if func_sizes:
        tiny   = sum(1 for s in func_sizes if s <= 4)
        small  = sum(1 for s in func_sizes if 5  <= s <= 20)
        medium = sum(1 for s in func_sizes if 21 <= s <= 100)
        large  = sum(1 for s in func_sizes if s > 100)
        print(f"  Size distribution    :")
        print(f"    ≤4 instrs (leaf/stub) : {tiny}")
        print(f"    5–20 instrs (small)   : {small}")
        print(f"    21–100 instrs (medium): {medium}")
        print(f"    >100 instrs (large)   : {large}")
        print(f"  Largest function     : {func_sizes[-1]} instrs  @ 0x{funcs[func_sizes.index(func_sizes[-1])]['start'] if False else [f for f in funcs if f['size']==func_sizes[-1]][0]['start']:08X}")
        print(f"  Median size          : {func_sizes[len(func_sizes)//2]} instrs")
        print(f"  Mean size            : {sum(func_sizes)/len(func_sizes):.1f} instrs")

    print(f"\n── Instruction Mix ──────────────────────────────────────")
    groups = [
        ("Loads",        cat["load"]),
        ("Stores",       cat["store"]),
        ("Arithmetic",   cat["arith"]),
        ("Logic/Shift",  cat["logic_shift"]),
        ("Compare",      cat["compare"]),
        ("Branches",     cat["branch"]),
        ("Calls (jal)",  cat["call"]),
        ("Returns (jr)", cat["return"]),
        ("Jumps (j)",    cat["jump"]),
        ("Move",         cat["move"]),
        ("Hi/Lo",        cat["hilo"]),
        ("GTE (cop2)",   cat["gte"]),
        ("COP0",         cat["cop0"]),
        ("Nop",          cat["nop"]),
        ("Other",        cat["other"]),
    ]
    code_total = total - cat["data_word"]
    for label, count in sorted(groups, key=lambda x: -x[1]):
        if count == 0:
            continue
        bar = "█" * int(count / code_total * 40)
        print(f"  {label:<16} {count:>6,}  {count/code_total*100:5.1f}%  {bar}")

    print(f"\n── Most Called Functions ────────────────────────────────")
    print(f"  {'Address':<14} {'Call count':>10}")
    for addr, cnt in call_count.most_common(15):
        print(f"  0x{addr:08X}     {cnt:>6}x")

    print(f"\n── Top 20 Instructions ──────────────────────────────────")
    for mnem, cnt in freq.most_common(20):
        print(f"  {mnem:<12}  {cnt:>6,}  ({cnt/total*100:.1f}%)")

    print(f"\n── GTE Usage ────────────────────────────────────────────")
    gte_total = cat["gte"]
    gte_instrs = {k: v for k, v in freq.items()
                  if k in ("cop2","mfc2","cfc2","mtc2","ctc2","lwc2","swc2")}
    if gte_total:
        for k, v in sorted(gte_instrs.items(), key=lambda x: -x[1]):
            print(f"  {k:<10}  {v:>5,}")
    else:
        print("  No GTE instructions found")

    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} <disassembly.asm>")
        sys.exit(1)
    report(sys.argv[1])
