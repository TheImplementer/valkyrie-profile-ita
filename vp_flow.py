#!/usr/bin/env python3
"""Valkyrie Profile scene scripts as play order: lines nested in the branches, choices and loops
that decide whether and when they are shown.

Usage:
    python3 vp_flow.py VALKYRIE.BIN --scene 0x16          one scene id (see vp_scenes.py)
    python3 vp_flow.py VALKYRIE.BIN --all [--stats]       every scene
    options: --flags  also show flag / variable writes
  Settled variables print by name (VAR_NAMES): story = G0xD0.b (storyline), step = G0xD1.b (step in
  it), chapter = G0x68.b, period = G0x69.b. See story/story_vars.md and story/event_order.txt.

How it reads a script (VM in VP_DIALOGUE_LOAD_PATH.md, disassembly in vp_script.py):
  * The scene's own code is compiled from structured source. `jump if !pop L` forward is an `if`;
    when the word before L is `jump M` forward, [L, M) is its `else`. A backward jump closes a loop.
    Anything that doesn't nest is printed as `goto` (and counted by --stats).
  * Conditions are rebuilt from the pushes and accumulator operations before the jump. The index a
    choice returns (0x9D wait_choice puts it in the accumulator) is followed into the local it is
    stored in, so `choice == 0` prints as the option's text.
  * Calls into the scene's functions are expanded in place, marked `[func_XXXX]` and not indented
    (a call runs in sequence), once per entry point: a later call prints `(as above)`. Calls into
    the shared library show the line they display (vp_script.analyze) and are otherwise left out.
  * Roots: the scene's main function (hdr[2]) first, then each function the code never calls
    directly, with the built-in that registers it (trigger zone, actor script...): those run when
    the player walks somewhere or talks to someone, so their order relative to main is up to play.
"""
import argparse
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


vsc = _load('vp_script')
vsn = _load('vp_scenes')

SYM = {'add': '+', 'sub': '-', 'mul': '*', 'div': '/', 'and': '&', 'or': '|', 'xor': '^', 'shl': '<<',
       'shr': '>>', 'land': 'and', 'lor': 'or', 'ne': '!=', 'eq': '==', 'gt': '>', 'lt': '<',
       'ge': '>=', 'le': '<='}
MAX_DEPTH = 64


# Access size by opcode. Only the binary operations 0x21-0x4A take it from operand bits 17-16; the
# push / pop / compare opcodes have a fixed size whatever those bits say (0x1C's handler 0x80073FD4
# does `lbu` on both sides). A `.bit` operand's number is a bit index into the flag array.
OP_SIZE = {0x01: '', 0x02: '', 0x08: '', 0x0b: '', 0x0f: '', 0x19: '', 0x1a: '',
           0x03: '.h', 0x04: '.h', 0x0c: '.h', 0x05: '.b', 0x06: '.b', 0x0d: '.b', 0x1c: '.b',
           0x07: '.bit', 0x0e: '.bit'}


def sized_var(w):
    v = vsc.var(w); op = w >> 24
    if op not in OP_SIZE or v.startswith('['):
        return v
    return v.split('.')[0] + OP_SIZE[op]


def neg(c):
    if c is None:
        return 'not (?)'
    if c.startswith('not ') and ' ' not in c[4:]:
        return c[4:]
    return f'not {c}' if ' ' not in c else f'not ({c})'


def options(text):
    return [l.strip()[1:].strip() for l in text.split('\n') if l.strip().startswith('・')]


class Scene:
    def __init__(self, W, hdr, records, show_flags=False, show_addr=False):
        self.W, self.hdr, self.records = W, hdr, records
        self.flags, self.addr = show_flags, show_addr
        self.starts = vsc.sweep(W)
        self.next = {a: b for a, b in zip(self.starts, self.starts[1:] + [len(W)])}
        self.prev = {b: a for a, b in zip(self.starts, self.starts[1:])}
        self.funcs = vsc.functions(W, self.starts)
        self.fset = set(self.funcs)
        self.lo = vsc.scene_start(hdr[2])
        _, self.sites = vsc.analyze(W, self.starts)
        self.end = {}
        for f, g in zip(self.funcs, self.funcs[1:] + [len(W)]):
            self.end[f] = g
        self.gotos = 0
        # backward jumps: loop head -> last jump back to it
        self.back = {}
        for i in self.starts:
            op = W[i] >> 24
            if op in (0x15, 0x16) and (W[i] & 0x7FFFFF) <= i:
                t = W[i] & 0x7FFFFF
                self.back[t] = max(self.back.get(t, 0), i)

    # ---- rendering helpers ----
    def rec(self, r):
        if r >= len(self.records):
            return f'#{r} (no such record)'
        return f'#{r} ' + self.records[r].replace('\n', ' / ')

    def cond_text(self, c, st):
        if c is None:
            return '?'
        return c

    # ---- structuring ----
    def block(self, lo, hi, depth, st, loop_exit=None):
        """Nodes for the instructions [lo, hi). st = shared expression state (stack, acc, alias)."""
        out = []; i = lo
        while i < hi:
            if i in self.back and self.back[i] < hi and st.get('in_loop_head') != i:
                j = self.back[i]; jn = self.next[j]
                st2 = dict(st, in_loop_head=i)
                body = self.block(i, j, depth, st2, loop_exit=jn)
                if self.W[j] >> 24 == 0x16:
                    c = st2['stack'].pop() if st2['stack'] else None
                    head = f'repeat while not ({c or "?"}):'
                else:
                    head = 'loop:'
                out.append((head, body)); i = jn; continue
            w = self.W[i]; op = w >> 24; n = self.next[i]
            stack = st['stack']
            for r in self.sites.get(i, []):
                if op == 0x93 and r < len(self.records):
                    st['options'] = options(self.records[r])
                    out.append(('choice ' + self.rec(r), None))
                elif not (0 <= r < 14):
                    out.append((self.rec(r), None))
            if self.expr(i, st):
                pass
            elif op in vsc.BINOPS or op - 1 in vsc.BINOPS:
                name = vsc.BINOPS.get(op) or vsc.BINOPS[op - 1]
                v = sized_var(w); rhs = self.W[i + 1] if op in vsc.BINOPS else (stack.pop() if stack else '?')
                if name == 'set':
                    st['alias'].pop(v, None)
                    if op - 1 in vsc.BINOPS and (rhs == 'choice' or (v.startswith('L') and str(rhs).startswith('func_'))):
                        st['alias'][v] = rhs
                    elif self.flags:
                        out.append((f'{v} = {rhs}', None))
                else:
                    st['acc'] = self.compare(self.operand(w, st), SYM[name], rhs, st)   # only `x op pop`
            elif op in (0x01, 0x02, 0x08, 0x03, 0x04, 0x05, 0x06, 0x07):
                v = sized_var(w); val = stack.pop() if stack else '?'
                st['alias'].pop(v, None)
                if val == 'choice' or (v.startswith('L') and val.startswith('func_')):
                    st['alias'][v] = val           # a local holding a choice or a call's result
            elif op == 0x9d:
                st['acc'] = 'choice'
            elif op == 0x16:
                c = stack.pop() if stack else None
                t = w & 0x7FFFFF
                if t > i:
                    c, n, t = self.disjunction(c, n, t, hi, st)
                    c, n = self.conjunction(c, n, t, hi, st)
                if t <= i:
                    pass                                   # loop tail, handled by the loop node
                elif loop_exit is not None and t == loop_exit:
                    if c != '1':                           # `push 1; jump if !pop` = while (1)
                        out.append((f'if {neg(c)}: break', None))
                elif t <= hi:
                    p = self.prev.get(t)
                    if p is not None and p > i and self.W[p] >> 24 == 0x15 and loop_exit is not None \
                            and (self.W[p] & 0x7FFFFF) == loop_exit and t <= hi:
                        # `case: ...; break` inside a loop: the rest of the loop body is the else
                        a = self.block(n, p, depth, self.fork(st), loop_exit) + [('break', None)]
                        b = self.block(t, hi, depth, self.fork(st), loop_exit)
                        out.append((f'if {c or "?"}:', a))
                        out.append(('else:', b)); i = hi; continue
                    if p is not None and p > i and self.W[p] >> 24 == 0x15 and t < (self.W[p] & 0x7FFFFF) and \
                            self.ends(hi, self.W[p] & 0x7FFFFF):
                        u = min(self.W[p] & 0x7FFFFF, hi)
                        a = self.block(n, p, depth, self.fork(st), loop_exit)
                        b = self.block(t, u, depth, self.fork(st), loop_exit)
                        out.append((f'if {c or "?"}:', a))
                        out.append(('else:', b)); i = u; continue
                    a = self.block(n, t, depth, self.fork(st), loop_exit)
                    out.append((f'if {c or "?"}:', a)); i = t; continue
                else:
                    self.gotos += 1; out.append((f'if {neg(c)}: goto L_{t:04x}', None))
                st['stack'] = []
            elif op == 0x15:
                t = w & 0x7FFFFF
                if t > i and not (t >= hi and loop_exit is None and t == hi):
                    if loop_exit is not None and t == loop_exit:
                        out.append(('break', None))
                    elif not self.ends(hi, t):
                        self.gotos += 1; out.append((f'goto L_{t:04x}', None))
                st['stack'] = []
            elif op == 0x17:
                if n < hi:
                    out.append(('return', None))
                st['stack'] = []
            elif op == 0x14:
                t = w & 0x7FFFFF
                if t in self.fset and t >= self.lo and depth < MAX_DEPTH:
                    if t in st['calls']:
                        out.append((f'(calls func_{t:04x} again: recursion)', None))
                    elif t in self.expanded:
                        if self.expanded[t]:
                            out.append((f'[func_{t:04x}]  (as above)', None))
                    else:
                        self.expanded[t] = False
                        st2 = self.fork(st); st2['calls'] = st['calls'] | {t}
                        body = prune(self.function(t, depth + 1, st2))
                        self.expanded[t] = has_text(body)
                        if body:                   # a call runs in sequence: no extra indent
                            out.append((f'[func_{t:04x}]', None)); out.extend(body)
                st['stack'] = []; st['acc'] = f'func_{t:04x}()'
            elif op == 0x9e:
                tgt = (w & 0xFFFF) if not w & 0x100000 else (int(stack[-1]) if stack and stack[-1].isdigit() else None)
                out.append((f'→ next scene {tgt:#x}' if tgt is not None else '→ next scene (computed)', None))
                st['stack'] = []
            elif op >= 0x50 and w & 0x100000:
                st['stack'] = []; st['acc'] = f'{vsc.BUILTIN_NAMES.get(op, hex(op))}(...)'
            elif op >= 0x50 and op not in (0x9d,):
                args = [w & 0xFFFF] + list(self.W[i + 1:i + vsc.length(w)])
                st['acc'] = f'{vsc.BUILTIN_NAMES.get(op, hex(op))}(' + ', '.join(
                    str(a) if a < 10 else hex(a) for a in args) + ')'
            i = n
        return out

    def expr(self, i, st):
        """Apply instruction i if it only computes a value (push, compare, `acc = var op word`)."""
        w = self.W[i]; op = w >> 24; stack = st['stack']
        if op == 0x12:
            stack.append(str(w & 0x7FFFFF))
        elif op == 0x13:
            stack.append(f'{self.W[i + 1]:#x}')
        elif op in (0x0b, 0x0f, 0x0c, 0x0d, 0x0e):
            v = sized_var(w)
            stack.append(st['acc'] if v == 'G0x0' else st['alias'].get(v, v))
        elif op in (0x19, 0x1c):
            v = self.operand(w, st)
            stack.append(self.compare(v, '==', self.W[i + 1], st))
        elif op in vsc.BINOPS and vsc.BINOPS[op] != 'set':
            st['acc'] = self.compare(self.operand(w, st), SYM[vsc.BINOPS[op]], self.W[i + 1], st)
        else:
            return False
        return True

    @staticmethod
    def operand(w, st):
        """A variable operand as text: G0x0 is the accumulator, a local may stand for a choice/result."""
        v = sized_var(w)
        return st['acc'] if v == 'G0x0' else st['alias'].get(v, v)

    def ends(self, hi, u):
        """Is a jump to u the same as falling off the end of a block that ends at hi? (u is hi, or
        the code at hi jumps on to where u leads: the compiler threads jumps to jumps.)"""
        return u <= hi or (hi < len(self.W) and self.thread(hi) == self.thread(u))

    def thread(self, x):
        """Follow a chain of unconditional jumps to where it ends."""
        seen = set()
        while x < len(self.W) and self.W[x] >> 24 == 0x15 and x not in seen:
            seen.add(x); x = self.W[x] & 0x7FFFFF
        return x

    def disjunction(self, c, n, t, hi, st):
        """`a || b || c` compiles to `a; jump if !pop A; jump T; A: b; jump if !pop B; jump T; B: c;
        jump if !pop F; T: body`, often with the jumps to T chained through each other. Fold it into
        one condition whose false target is F. Returns (condition, body start, false target)."""
        terms = [c]; k = n
        while k < hi and self.W[k] >> 24 == 0x15 and self.next[k] == t:
            T = self.thread(k)
            sub = self.fork(st); j = t
            while j < hi and self.expr(j, sub):
                j = self.next[j]
            if not (j < hi and j != t and self.W[j] >> 24 == 0x16 and sub['stack']):
                break
            terms.append(sub['stack'].pop()); t2 = self.W[j] & 0x7FFFFF; jn = self.next[j]
            if t2 <= j:
                break
            if self.W[jn] >> 24 == 0x15 and self.next[jn] == t2 and self.thread(jn) == T:
                k, t = jn, t2                        # another `jump T` follows: keep going
                continue
            if jn == T:                              # last term: the body follows it
                return ' or '.join(x or '?' for x in terms), jn, t2
            break
        return c, n, t

    def conjunction(self, c, n, t, hi, st):
        """`a && b` compiles to two `jump if !pop` to the same label: fold them into one condition."""
        while True:
            sub = self.fork(st); k = n
            while k < hi and self.expr(k, sub):
                k = self.next[k]
            if k < hi and k != n and self.W[k] >> 24 == 0x16 and self.W[k] & 0x7FFFFF == t and sub['stack']:
                c = f'{c or "?"} and {sub["stack"].pop()}'; n = self.next[k]; st['acc'] = sub['acc']
            else:
                return c, n

    def compare(self, a, sym, b, st):
        if a == 'choice' and sym in ('==', '!=') and str(b).lstrip('-').isdigit():
            k = int(b); opts = st.get('options') or []
            label = f'choice {k}' + (f' «{opts[k]}»' if k < len(opts) else '')
            return label if sym == '==' else f'not {label}'
        if a.endswith('.bit') and sym == '==' and str(b) in ('0', '1'):
            return a if str(b) == '1' else f'not {a}'
        return f'{a} {sym} {b if not isinstance(b, int) else hex(b) if b > 9 else b}'

    @staticmethod
    def fork(st):
        return {'stack': [], 'acc': st['acc'], 'alias': dict(st['alias']), 'options': st.get('options'),
                'calls': st['calls']}

    def function(self, f, depth, st):
        body_start = self.next[f]
        return self.block(body_start, self.end[f], depth, st)

    def roots(self):
        called = {self.W[i] & 0x7FFFFF for i in self.starts if self.W[i] >> 24 == 0x14}
        refs = {}
        for i in self.starts:
            w = self.W[i]; op = w >> 24
            if op >= 0x50 and not w & 0x100000:
                for k in range(1, vsc.length(w)):
                    v = self.W[i + k]
                    if v in self.fset and v >= self.lo:
                        refs.setdefault(v, f'{vsc.BUILTIN_NAMES.get(op, hex(op))} at {i:04x}')
        main = self.hdr[2]
        out = [(main, 'scene main (hdr[2])')]
        for f in self.funcs:
            if f >= self.lo and f != main and f not in called:
                out.append((f, refs.get(f, 'not called or registered here (library or another actor)')))
        return out


def has_text(nodes):
    for head, body in nodes:
        if head.startswith(('#', 'choice', '→')) or head.endswith('(as above)'):
            return True
        if body and has_text(body):
            return True
    return False


def prune(nodes):
    out = []
    for k, (head, body) in enumerate(nodes):
        if body is None:
            out.append((head, None)); continue
        body = prune(body)
        if has_text(body):
            out.append((head, body))
        elif head == 'else:' and out and out[-1][0].startswith('if '):
            continue
    # else: if ... -> elif
    flat = []
    for head, body in out:
        if head == 'else:' and body and body[0][0].startswith('if ') and \
                all(h.startswith(('elif ', 'else:')) for h, _ in body[1:]) and flat and flat[-1][0].startswith(('if ', 'elif ')):
            flat.append(('elif ' + body[0][0][3:], body[0][1])); flat.extend(body[1:])
        else:
            flat.append((head, body))
    return flat


# Names for variables whose meaning is settled (story/story_vars.md). Applied when printing only, so
# Scene's condition strings keep the raw operands for other tools.
VAR_NAMES = {'G0xd0.b': 'story', 'G0xd1.b': 'step', 'G0x68.b': 'chapter', 'G0x69.b': 'period',
             'G0xe8.bit': 'valhalla_intro_done'}
_NAME_RE = re.compile('|'.join(re.escape(k) for k in sorted(VAR_NAMES, key=len, reverse=True)) + r'(?![\w.])')


def named(text):
    return _NAME_RE.sub(lambda m: VAR_NAMES[m.group(0)], text)


def render(nodes, ind=1):
    lines = []
    for head, body in nodes:
        lines.append('  ' * ind + (named(head) if not head.startswith('#') else head))
        if body:
            lines.extend(render(body, ind + 1))
    return lines


def scene_flow(data, at, glyphs, show_flags=False):
    hdr, W, records = vsc.load(data, at, glyphs)
    sc = Scene(W, hdr, records, show_flags)
    parts = []
    for f, why in sc.roots():
        sc.expanded = {}
        st = {'stack': [], 'acc': 'acc', 'alias': {}, 'options': None, 'calls': frozenset({f})}
        nodes = prune(sc.function(f, 0, st))
        if has_text(nodes):
            parts.append(f'func_{f:04x}  ({why})')
            parts.extend(render(nodes))
    return parts, sc.gotos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('archive')
    ap.add_argument('--scene', type=lambda s: int(s, 0))
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--stats', action='store_true')
    ap.add_argument('--flags', action='store_true')
    a = ap.parse_args()
    data = open(a.archive, 'rb').read()
    glyphs = vsc.vst.glyph_names()
    dirw = vsn.directory(data)
    ids = range(vsn.SCENE_COUNT) if a.all else [a.scene]
    gotos = 0; scenes = 0
    for k in ids:
        at = vsn.scene_container(data, dirw, k)
        if at is None:
            continue
        parts, g = scene_flow(data, at, glyphs, a.flags)
        gotos += g; scenes += 1
        if not a.stats:
            print(f'=== scene {k:#x} (container {at:#x})')
            print('\n'.join(parts) if parts else '  (no lines)')
            print()
    print(f'# {scenes} scenes, {gotos} jumps that do not nest (printed as goto)', file=sys.stderr)


if __name__ == '__main__':
    main()
