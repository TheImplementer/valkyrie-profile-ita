#!/usr/bin/env python3
"""Disassemble Valkyrie Profile scene event scripts, and tie each message to its text.

Usage:
    python3 vp_script.py VALKYRIE.BIN OFFSET [--lib]   listing of one scene (--lib: include the
                                                      shared library, words 0..hdr[2])
    python3 vp_script.py VALKYRIE.BIN --lines          every scene: the lines its script shows,
                                                      in script order
    python3 vp_script.py VALKYRIE.BIN --stats          coverage over all scenes

The VM (field engine; interpreter loop at 0x800715B4, handler table at 0x800831D0) is described in
VP_DIALOGUE_LOAD_PATH.md. In short: a 32-bit word per instruction, opcode = bits 31-24. Core
opcodes 0x01-0x4A have fixed lengths; built-ins (0x50+) take their arguments from the stack when
bit 20 is set, otherwise from N inline words that follow (N per built-in, BUILTIN_ARGS).
"""
import argparse
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('vp_scene_text', os.path.join(HERE, 'vp_scene_text.py'))
vst = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(vst)

# Inline argument words of each built-in when bit 20 is clear. Learned from all 1,034 distinct
# scripts (every call/jump target then lands on an instruction, every call on a 0x18), and checked
# against a live trace of the VM for 0x71 0x72 0x75 0x87 0x8A 0x8C 0x92 0x9E 0xA1 0xBB 0xD9, and corrected
# from the handlers for 0x78 / 0xE2 (0 words), 0xA4 (4), 0xA8 / 0xBD (1).
# The shared library is the first 0xA51 words of every script (identical in 1,116 of 1,117 scenes);
# the scene's own functions follow it, and its main function (hdr[2]) comes last.
LIBRARY_WORDS = 0xA51

BUILTIN_ARGS = {0x71: 2, 0x72: 1, 0x73: 2, 0x75: 1, 0x79: 1, 0x80: 3, 0x82: 1, 0x83: 2,
                0x85: 4, 0x86: 1, 0x87: 1, 0x88: 1, 0x8a: 2, 0x8c: 1, 0x8f: 2, 0x90: 2, 0x92: 3,
                0x93: 3, 0x94: 3, 0x95: 1, 0x96: 1, 0x97: 1, 0x98: 3, 0x9c: 1, 0x9d: 1, 0x9e: 1,
                0x9f: 2, 0xa0: 2, 0xa1: 2, 0xa2: 1, 0xa3: 2, 0xa4: 4, 0xa5: 2, 0xa8: 1, 0xac: 1, 0xad: 2, 0xae: 2,
                0xb3: 2, 0xb4: 1, 0xba: 2, 0xbb: 1, 0xbc: 3, 0xbd: 1, 0xbf: 1, 0xc0: 2, 0xc2: 1, 0xc4: 2,
                0xc5: 1, 0xc9: 1, 0xcc: 6, 0xcd: 3, 0xce: 1, 0xd0: 2, 0xd1: 1, 0xd8: 2, 0xd9: 2,
                0xda: 2, 0xdb: 2, 0xdc: 1, 0xe1: 1, 0xe4: 1, 0xe6: 1, 0xe7: 2, 0xeb: 1}
# Text built-ins: the record number is the low 16 bits when the arguments are inline. 0x93 calls the
# 0x92 handler (jal 0x800748EC), so its stack form has the same layout too. 0x92 was traced
# live; 0x93 points at a record with ・ choices in 72 of 72 uses, 0xCB / 0x97 at a one-line <5>
# caption (a place name) in 87 of 88 and 44 of 45 uses.
# 0xCC is a treasure/item pickup: 6 inline words (position, item id, flags, ...) and in the 5th the
# "You found ..." record (valid in 466 of 466 uses).
import json as _json
_B = _json.load(open(os.path.join(HERE, 'vp_builtins.json'), encoding='utf-8'))
# Names for every built-in, from vp_builtins.json (low-confidence names get a '?').
BUILTIN_NAMES = {int(k, 16): v['name'] + ('?' if v['confidence'] == 'low' else '') for k, v in _B.items()}
TEXT_OPS = {0x92, 0x93, 0xcb, 0x97, 0x94, 0x98}   # 0x94/0x98: siblings of 0x92 (box style 3/4); 8 of 8 uses valid
# Library functions that take a record but reach the screen some other way: word index ->
# argument index from the top. func_0090(record, kind) = door/exit label ("To lower level", "Door";
# kind 1 or 2): a one-line <5> label in 14 of 14 literal calls.
LIB_TEXT_FUNCS = {0x90: 1}

# Core opcodes: name, inline words, how the operand is shown.
#   V = variable operand (bit 23 local, bits 17-16 size), I = 23-bit immediate, T = code target,
#   F = function entry, '' = none.
CORE = {
    0x01: ('pop', 0, 'V'), 0x02: ('pop', 0, 'V'), 0x08: ('pop', 0, 'V'),
    0x03: ('pop.h', 0, 'V'), 0x04: ('pop.h', 0, 'V'), 0x05: ('pop.b', 0, 'V'), 0x06: ('pop.b', 0, 'V'),
    0x07: ('pop.bit', 0, 'V'), 0x09: ('acc = pop *', 0, 'I'), 0x0a: ('acc +=', 0, 'I'),
    0x0b: ('push', 0, 'V'), 0x0f: ('push', 0, 'V'), 0x0c: ('push.h', 0, 'V'), 0x0d: ('push.b', 0, 'V'),
    0x0e: ('push.bit', 0, 'V'), 0x10: ('acc = imm + word', 1, 'I'), 0x11: ('acc *=', 0, 'I'),
    0x12: ('push', 0, 'I'), 0x13: ('push word', 1, ''), 0x14: ('call', 0, 'T'), 0x15: ('jump', 0, 'T'),
    0x16: ('jump if !pop', 0, 'T'), 0x17: ('return', 0, ''), 0x18: ('enter', 1, 'F'),
    0x19: ('push var == word', 1, 'V'), 0x1a: ('set var = word', 1, 'V'), 0x1c: ('push var.b == word', 1, 'V'),
    0x1b: ('nop', 0, ''), 0x1d: ('nop', 0, ''), 0x1e: ('nop', 0, ''), 0x1f: ('nop', 0, ''),
    0x3b: ('flag op', 0, 'V'), 0x3c: ('flag op', 0, 'V'), 0x49: ('inc', 0, 'V'), 0x4a: ('dec', 0, 'V'),
}
BINOPS = {0x21: 'set', 0x23: 'add', 0x25: 'sub', 0x27: 'mul', 0x29: 'div', 0x2b: 'and', 0x2d: 'or',
          0x2f: 'xor', 0x33: 'shl', 0x35: 'shr', 0x37: 'land', 0x39: 'lor', 0x3d: 'ne', 0x3f: 'eq',
          0x41: 'gt', 0x43: 'lt', 0x45: 'ge', 0x47: 'le'}
for op, name in BINOPS.items():
    CORE[op] = (('' if name == 'set' else 'acc = ') + name + ' var, word', 1, 'V')
    CORE[op + 1] = (('' if name == 'set' else 'acc = ') + name + ' var, pop', 0, 'V')


def length(w):
    op = w >> 24
    if op in CORE:
        return 1 + CORE[op][1]
    if op < 0x50 or op == 0:
        return 1
    return 1 if w & 0x100000 else 1 + BUILTIN_ARGS.get(op, 0)


def var(w):
    off = w & 0xFFFF; size = {0: '', 0x10000: '.h', 0x20000: '.b', 0x30000: '.bit'}[w & 0x30000]
    if w & 0x0084FFFF == 0 and w & 0x30000:
        return f'[acc]{size}'
    return f'{"L" if w & 0x800000 else "G"}{off:#x}{size}'


def sweep(W):
    """Instruction start indexes (linear sweep; data runs decode as single words)."""
    i = 0; out = []
    while i < len(W):
        out.append(i); i += length(W[i])
    return out


def functions(W, starts):
    return [i for i in starts if W[i] >> 24 == 0x18]


def analyze(W, starts):
    """Follow record numbers through the script.

    Returns (summary, sites): summary[f] = (number of arguments f's prologue pops, set of argument
    indexes k, counted from the top of the caller's stack, whose value reaches a message's record
    number); sites = {instruction index: [record numbers]} for every call or 0x92 where the record
    number is a literal (pushed with 0x12, or inline in the 0x92)."""
    funcs = functions(W, starts); fset = set(funcs)
    body = {}; cur = None
    for i in starts:
        if i in fset: cur = i; body[cur] = []
        if cur is not None: body[cur].append(i)
    summary = {}; sites = {}
    def walk(f, depth=0):
        if f in summary: return summary[f]
        summary[f] = (0, set())          # provisional, breaks recursion
        argmap = {}; k = 0
        for i in body.get(f, [])[1:]:
            if W[i] >> 24 in (0x01, 0x02, 0x08):
                if W[i] & 0x800000: argmap[W[i] & 0xFFFF] = ('arg', k)
                k += 1
            else:
                break
        args = set(); stack = []
        def use(v, i):
            if v is None: return
            if v[0] == 'arg': args.add(v[1])
            else: sites.setdefault(i, []).append(v[1])
        for i in body.get(f, []):
            w = W[i]; op = w >> 24
            if op == 0x12: stack.append(('const', w & 0x7FFFFF))
            elif op == 0x13: stack.append(('const', W[i + 1]))
            elif op in (0x0b, 0x0f, 0x0c, 0x0d, 0x0e): stack.append(argmap.get(w & 0xFFFF) if w & 0x800000 else None)
            elif op == 0x14 and (w & 0x7FFFFF) in LIB_TEXT_FUNCS:
                k = LIB_TEXT_FUNCS[w & 0x7FFFFF]
                use(stack[-1 - k] if k < len(stack) else None, i)
                stack = []
            elif op == 0x14:
                t = w & 0x7FFFFF
                if t in fset:
                    n, targs = walk(t, depth + 1)
                    for kk in targs:
                        use(stack[-1 - kk] if kk < len(stack) else None, i)
                    del stack[max(0, len(stack) - n):]
                else:
                    stack = []
            elif op in TEXT_OPS and not (w & 0x100000):
                sites.setdefault(i, []).append(w & 0xFFFF)
            elif op == 0xcc and not (w & 0x100000):
                sites.setdefault(i, []).append(W[i + 5])
            elif op in (0x92, 0x93):        # 0x93 (choice) calls the 0x92 handler: same layout
                if w & 0x100000:
                    use(stack[-6] if len(stack) >= 6 else None, i)
                    del stack[max(0, len(stack) - 7):]
                else:
                    sites.setdefault(i, []).append(w & 0xFFFF)
            elif op in (0x15, 0x17):
                stack = []
            elif op >= 0x50 and w & 0x100000:
                stack = []                   # stack built-in: pops an unknown number
            elif op in CORE and (CORE[op][0].startswith('pop') or CORE[op][0].endswith('pop') or op in (0x16, 0x09)):
                if stack: stack.pop()
            elif op in (0x19, 0x1c):
                stack.append(None)
        summary[f] = (k, args)
        return summary[f]
    for f in funcs: walk(f)
    return summary, sites


def scene_start(main):
    return min(main, LIBRARY_WORDS)


def scene_lines(W, starts, main):
    """(script word, record) for each message the scene's own code shows, in code order."""
    _, sites = analyze(W, starts)
    return [(i, r) for i in sorted(sites) if i >= scene_start(main) for r in sites[i]]


def listing(W, starts, main, records, lib=False):
    fset = set(functions(W, starts)); targets = {W[i] & 0x7FFFFF for i in starts if W[i] >> 24 in (0x14, 0x15, 0x16)}
    _, sites = analyze(W, starts); lines = []
    for i in starts:
        if i < scene_start(main) and not lib: continue
        w = W[i]; op = w >> 24; n = length(w)
        if i in fset: lines.append(f'\nfunc_{i:04x}:' + ('   ; scene main (hdr[2])' if i == main else ''))
        elif i in targets: lines.append(f'L_{i:04x}:')
        extra = ' '.join(f'{x:08x}' for x in W[i + 1:i + n])
        if op in CORE:
            name, _, kind = CORE[op]
            arg = {'V': var(w), 'I': f'{w & 0x7FFFFF:#x}', 'T': (f'func_{w & 0x7FFFFF:04x}' if op == 0x14 else f'L_{w & 0x7FFFFF:04x}'),
                   'F': f'frame {((w << 9) & 0xFFFFFFFF) >> 12}', '': ''}[kind]
            text = f'{name} {arg}'.strip() + (f'  {extra}' if extra else '')
            for rec in sites.get(i, []):
                if rec < len(records):
                    text += f'    ; message {rec}: ' + records[rec].replace('\n', ' / ')[:90]
        elif op >= 0x50 and op not in (0,):
            name = BUILTIN_NAMES.get(op, f'sys_{op:02x}')
            mode = 'stack' if w & 0x100000 else 'inline'
            text = f'{name} {w & 0xFFFFF:#x} ({mode})' + (f'  {extra}' if extra else '')
            for rec in sites.get(i, []):
                if rec < len(records):
                    text += f'    ; message {rec}: ' + records[rec].replace('\n', ' / ')[:90]
        else:
            text = f'.word {w:#010x}'
        lines.append(f'  {i:04x}: {w:08x}  {text}')
    return '\n'.join(lines)


def load(data, at, glyphs):
    blk = vst.entries(data, at)[0]
    hdr = struct.unpack_from('<7I', blk)
    W = struct.unpack_from(f'<{hdr[0] // 4}I', blk, 0x1C)
    _, records = vst.decode_scene(data, at, glyphs)
    return hdr, W, records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('archive'); ap.add_argument('offset', nargs='?', type=lambda s: int(s, 0))
    ap.add_argument('--lib', action='store_true'); ap.add_argument('--lines', action='store_true')
    ap.add_argument('--stats', action='store_true')
    a = ap.parse_args()
    import json
    glyphs = vst.glyph_names()
    data = open(a.archive, 'rb').read()
    if a.offset is not None and not (a.lines or a.stats):
        hdr, W, records = load(data, a.offset, glyphs)
        print(listing(W, sweep(W), hdr[2], records, a.lib))
        return
    offsets = []; i = 0
    while (i := data.find(b'SLZ\x01', i)) >= 0:
        if vst.is_scene(data, i): offsets.append(i)
        i += 4
    if a.offset is not None:
        offsets = [a.offset]
    shown = total = 0
    for at in offsets:
        hdr, W, records = load(data, at, glyphs)
        if hdr[2] < 2: continue
        starts = sweep(W); used = scene_lines(W, starts, hdr[2])
        recs = {r for _, r in used if 14 <= r < len(records)}
        shown += len(recs); total += max(0, len(records) - 14)
        if a.lines:
            print(f'=== scene {at:#x}: script shows {len(recs)} of {max(0, len(records) - 14)} scene lines')
            for i, r in used:
                if r < len(records):
                    print(f'[{i:04x}] #{r}: ' + records[r].replace('\n', ' / '))
    if a.stats or a.lines:
        print(f'# scene lines tied to a message call: {shown} of {total} ({100 * shown / max(1, total):.0f}%)', file=sys.stderr)


if __name__ == '__main__':
    main()
