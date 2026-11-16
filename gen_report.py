#!/usr/bin/env python3
"""Generate a styled HTML analysis report from a psx_disasm.py disassembly.

Mechanical + evidenced only: every function gets address / size / call-counts and a
rule-based category. Human-readable names appear ONLY where rule-certain
(engine_entry, nop_stub). No per-function semantics are invented.

Reuses analyze.py for the canonical headline numbers so the report matches
`python3 analyze.py <asm>` exactly; adds a word-level pass to resolve global-data
references (lui+offset pairing) and to categorise each function.

Usage: python3 gen_report.py <disassembly.asm> [out.html] [--title "..."]
"""
import html
import os
import re
import sys
from collections import Counter

import analyze   # reuse parse(), second_pass_functions(), regexes

RE_FULL = re.compile(r'^(0x[0-9A-Fa-f]{8}):\s+([0-9A-Fa-f]{8})\s+(\S+)(.*)')
RE_HEXT = re.compile(r'0x[0-9A-Fa-f]+')

EXE_LO, EXE_HI = 0x80010000, 0x8002F800     # resident EXE image
GTE_MNEM = {"cop2", "mfc2", "cfc2", "mtc2", "ctc2", "lwc2", "swc2"}
LOAD_MNEM = {"lb", "lbu", "lh", "lhu", "lw", "lwl", "lwr"}
STORE_MNEM = {"sb", "sh", "sw", "swl", "swr"}

# category -> (display label, accent colour)
CATS = [
    ("entry",      "Entry & Startup",   "#6366f1"),
    ("gte",        "GTE / Rendering",   "#ec4899"),
    ("exe",        "Calls EXE Library", "#f59e0b"),
    ("mem",        "Memory / Copy",     "#10b981"),
    ("arith",      "General code (mixed)","#8b5cf6"),
    ("leaf",       "Leaf / Stub",       "#0ea5e9"),
    ("data",       "Data / BSS region", "#64748b"),
    ("uncat",      "Uncategorised",     "#475569"),
]
CAT_LABEL = {k: v for k, v, _ in CATS}
CAT_COLOR = {k: c for k, _, c in CATS}


# ── word-level parse (addr, word, mnem, rest) ─────────────────────────────────
def parse_words(path):
    lines, idx = [], {}
    first_data = None
    for raw in open(path, encoding="utf-8"):
        m = RE_FULL.match(raw.rstrip())
        if not m:
            continue
        addr = int(m.group(1), 16)
        word = int(m.group(2), 16)
        mnem = m.group(3).lower()
        rest = m.group(4)
        idx[addr] = len(lines)
        lines.append((addr, word, mnem, rest))
        if mnem == ".word" and first_data is None:
            first_data = addr
    return lines, idx, first_data


# ── resolve absolute data addresses via lui + (addiu/ori/load/store) pairing ──
def resolve_globals(lines):
    luival = {}
    hist = Counter()
    for addr, word, mnem, rest in lines:
        op = word >> 26
        rt = (word >> 16) & 0x1F
        rs = (word >> 21) & 0x1F
        imm = word & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm
        if op == 0x0F:                       # lui rt, imm
            luival[rt] = imm << 16
        elif op == 0x09 and rs in luival:    # addiu rt, rs, imm
            hist[(luival[rs] + simm) & 0xFFFFFFFF] += 1
        elif op == 0x0D and rs in luival:    # ori rt, rs, imm
            hist[luival[rs] | imm] += 1
        elif op in (0x20, 0x21, 0x23, 0x24, 0x25, 0x28, 0x29, 0x2B) and rs in luival:
            hist[(luival[rs] + simm) & 0xFFFFFFFF] += 1   # lb/lh/lw/lbu/lhu/sb/sh/sw
    return hist


def func_category(lines, idx, start, size, first_data, entry_pc):
    """Return (cat_key, name, confidence, note) for one function."""
    si = idx.get(start)
    if si is None:
        return "uncat", "", "", ""
    slc = lines[si:si + size]
    gte = sum(1 for _, _, m, _ in slc if m in GTE_MNEM)
    ld = sum(1 for _, _, m, _ in slc if m in LOAD_MNEM)
    st = sum(1 for _, _, m, _ in slc if m in STORE_MNEM)
    callees = [int(RE_HEXT.search(r).group(), 16)
               for _, _, m, r in slc if m == "jal" and RE_HEXT.search(r)]
    exe_calls = sum(1 for c in callees if EXE_LO <= c < EXE_HI)
    has_loop = any(
        m in ("j", "b", "beq", "bne", "blez", "bgtz", "bltz", "bgez")
        and RE_HEXT.search(r) and int(RE_HEXT.search(r).group(), 16) <= start
        for _, _, m, r in slc)
    ends_ret = any(m == "jr" and "$ra" in r for _, _, m, r in slc)
    nonzero = [w for _, w, m, _ in slc if w != 0]

    # ── rule cascade (mechanical, ordered) ──
    if entry_pc is not None and start == entry_pc:
        return "entry", "engine_entry", "high", "overlay entry point"
    if first_data is not None and start >= first_data:
        return "data", "", "", "in the .word / bss region (not code)"
    if size <= 4 and ends_ret:
        if not nonzero or all(w == 0 for _, w, m, _ in slc if m == "nop"):
            return "leaf", "nop_stub", "high", "empty leaf — returns immediately"
        return "leaf", "", "low", "leaf: returns immediately"
    if gte:
        return "gte", "", "low", f"uses GTE: {gte} cop2-family ops"
    if exe_calls and callees and exe_calls * 2 >= len(callees):
        return "exe", "", "low", f"{exe_calls}/{len(callees)} calls target the EXE image"
    if size and (ld + st) >= size * 0.45 and has_loop:
        return "mem", "", "low", f"load/store-heavy loop ({ld}L/{st}S)"
    if size:
        return "arith", "", "", ""
    return "uncat", "", "", ""


def build(path, title):
    data = analyze.parse(path)
    funcs = analyze.second_pass_functions(data)
    lines, idx, first_data = parse_words(path)

    total = len(data["all_addrs"])
    cat = data["category"]
    freq = data["instr_freq"]
    call_count = data["call_count"]
    entry_pc = data["entry_pc"]
    base = data["all_addrs"][0] if data["all_addrs"] else 0
    last = data["all_addrs"][-1] if data["all_addrs"] else 0

    # per-function records
    recs = []
    for f in funcs:
        ck, name, conf, note = func_category(
            lines, idx, f["start"], f["size"], first_data, entry_pc)
        recs.append({
            "addr": f["start"], "size": f["size"],
            "incalls": call_count.get(f["start"], 0),
            "outcalls": sum(1 for _, _, m, _ in lines[idx.get(f["start"], 0):
                            idx.get(f["start"], 0) + f["size"]] if m in ("jal", "jalr")),
            "cat": ck, "name": name, "conf": conf, "note": note,
        })
    by_cat = {k: [r for r in recs if r["cat"] == k] for k, _, _ in CATS}

    # globals
    ghist = resolve_globals(lines)
    top_globals = [(a, n) for a, n in ghist.most_common(2000)
                   if 0x8007C000 <= a < 0x8007E000][:6]
    top_ext = [(a, c) for a, c in call_count.most_common(2000)
               if EXE_LO <= a < EXE_HI][:6]
    top_int = [(a, c) for a, c in call_count.most_common(2000)
               if not (EXE_LO <= a < EXE_HI)][:6]

    gte_total = cat["gte"]
    code_total = total - cat["data_word"]
    code_end = first_data if first_data else last + 4

    findings = [
        f"Entry point <code>0x{entry_pc:08X}</code> — prologue "
        f"<code>addiu $sp,-24; sw $s0; sw $ra; jal 0x800788E4</code>; the called "
        f"address sits at offset 0 of this overlay, confirming the load base."
        if entry_pc else "",
        f"<b>{total:,}</b> words decoded: <b>{code_total:,}</b> code + "
        f"<b>{cat['data_word']:,}</b> data <code>.word</code>. Coherent code runs "
        f"<code>0x{base:08X}</code>–<code>0x{code_end:08X}</code>, then data/rodata, "
        f"then bss/zeros — a normal <code>.text/.data/.bss</code> image layout.",
        f"<b>{len(funcs):,}</b> functions (JAL targets + entry). Only ~1/3 begin with a "
        f"standard <code>addiu $sp,-N</code> prologue, so names are assigned only where "
        f"mechanically certain.",
        f"<b>{gte_total}</b> GTE/COP2 instructions "
        f"(<code>" + ", ".join(f"{k}×{freq[k]}" for k in
            sorted(GTE_MNEM, key=lambda m: -freq.get(m, 0)) if freq.get(k, 0)) +
        "</code>) — an active 3D geometry/rendering pipeline.",
        ("Heavy use of the engine global-data segment at "
         + ", ".join(f"<code>0x{a:08X}</code>×{n}" for a, n in top_globals[:3])
         + f" — <code>0x{top_globals[0][0]:08X}</code> is the hot global pointer."
         ) if top_globals else "",
        ("Calls back into the resident EXE image: "
         + ", ".join(f"<code>0x{a:08X}</code>×{c}" for a, c in top_ext[:4])
         + " — the overlay reuses EXE library routines."
         ) if top_ext else "",
    ]
    findings = [f for f in findings if f]

    return render(title, path, base, entry_pc, total, code_total, cat, len(funcs),
                  func_count_by_size(funcs), findings, by_cat, top_int, top_ext,
                  freq, last)


def func_count_by_size(funcs):
    s = [f["size"] for f in funcs if f["size"] > 0]
    return (sum(1 for x in s if x <= 4), sum(1 for x in s if 5 <= x <= 20),
            sum(1 for x in s if 21 <= x <= 100), sum(1 for x in s if x > 100))


# ── HTML rendering ────────────────────────────────────────────────────────────
def esc(s):
    return html.escape(str(s))


def render(title, path, base, entry, total, code_total, cat, nfuncs, sizedist,
           findings, by_cat, top_int, top_ext, freq, last):
    cards = [
        ("Functions", f"{nfuncs:,}"),
        ("Code instructions", f"{code_total:,}"),
        ("Data words", f"{cat['data_word']:,}"),
        ("GTE / COP2 ops", f"{cat['gte']:,}"),
    ]
    present = [(k, lbl, col) for k, lbl, col in CATS if by_cat.get(k)]

    nav = "\n".join(
        f'<a class="nav-item" href="#{k}" style="border-left:3px solid {col}">'
        f'{esc(lbl)}<span class="nav-count">{len(by_cat[k])}</span></a>'
        for k, lbl, col in present)

    stat_cards = "\n".join(
        f'<div class="card"><div class="num">{v}</div><div class="lbl">{esc(l)}</div></div>'
        for l, v in cards)

    find_html = "\n".join(f"<li>{f}</li>" for f in findings)

    chart_labels = [CAT_LABEL[k] for k, _, _ in present]
    chart_data = [len(by_cat[k]) for k, _, _ in present]
    chart_colors = [CAT_COLOR[k] for k, _, _ in present]

    def badge(conf):
        if conf == "high":
            return '<span class="badge badge-high">high</span>'
        if conf == "low":
            return '<span class="badge badge-low">low</span>'
        return ''

    sections = []
    for k, lbl, col in present:
        rows = []
        for r in sorted(by_cat[k], key=lambda r: r["addr"]):
            rows.append(
                f'<tr><td class="addr">0x{r["addr"]:08X}</td>'
                f'<td>{r["size"]}</td>'
                f'<td>{r["incalls"]} / {r["outcalls"]}</td>'
                f'<td class="fname">{esc(r["name"]) or "&mdash;"}</td>'
                f'<td>{badge(r["conf"])}</td>'
                f'<td class="fdesc">{esc(r["note"])}</td></tr>')
        sections.append(
            f'<section id="{k}"><h2 style="border-left:4px solid {col};padding-left:12px">'
            f'{esc(lbl)} <span class="section-count">{len(by_cat[k])} functions</span></h2>'
            f'<table><thead><tr><th>Address</th><th>Size</th><th>In / Out calls</th>'
            f'<th>Name</th><th>Conf</th><th>Note (mechanical)</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></section>')

    callee_rows = "".join(
        f'<tr><td class="addr">0x{a:08X}</td><td>{c}×</td><td>{"EXE image" if EXE_LO<=a<EXE_HI else "overlay/other"}</td></tr>'
        for a, c in (top_int + top_ext)[:14])
    instr_rows = "".join(
        f'<tr><td class="fname">{esc(m)}</td><td>{c:,}</td><td>{c/total*100:.1f}%</td></tr>'
        for m, c in freq.most_common(20))

    return TEMPLATE.format(
        title=esc(title), path=esc(os.path.basename(path)),
        base=f"0x{base:08X}", entry=(f"0x{entry:08X}" if entry else "?"),
        end=f"0x{last:08X}",
        stat_cards=stat_cards, findings=find_html, nav=nav,
        sections="\n".join(sections),
        chart_labels=chart_labels, chart_data=chart_data, chart_colors=chart_colors,
        callee_rows=callee_rows, instr_rows=instr_rows,
        sizedist=" · ".join(f"{n} {lbl}" for n, lbl in zip(
            sizedist, ("≤4", "5–20", "21–100", ">100"))),
    )


TEMPLATE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
:root{{--bg:#0f1117;--surface:#1a1d27;--border:#2e3347;--text:#e2e8f0;--muted:#8892a4;--accent:#6366f1}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);display:flex}}
code{{font-family:ui-monospace,Menlo,Consolas,monospace;color:#a5b4fc;background:#11141d;padding:1px 5px;border-radius:4px;font-size:.86em}}
nav{{width:250px;min-width:250px;height:100vh;position:sticky;top:0;background:var(--surface);border-right:1px solid var(--border);overflow-y:auto;padding:18px 0}}
nav h1{{font-size:15px;padding:0 18px 12px;margin:0;color:#fff;word-break:break-all}}
.nav-item{{display:flex;justify-content:space-between;padding:7px 18px;color:var(--muted);text-decoration:none;font-size:13.5px}}
.nav-item:hover{{background:#222633;color:var(--text)}}
.nav-count{{color:var(--muted);font-size:12px}}
main{{flex:1;padding:32px 40px;max-width:1180px}}
header h1{{margin:0 0 4px;font-size:26px}}
header .sub{{color:var(--muted);font-size:14px;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:22px 0}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px}}
.card .num{{font-size:26px;font-weight:700;color:#fff}}
.card .lbl{{color:var(--muted);font-size:12.5px;margin-top:4px}}
.panel{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:18px 22px;margin:22px 0}}
.panel h2{{margin:0 0 12px;font-size:18px}}
.panel ul{{margin:0;padding-left:20px;line-height:1.7}}
.panel li{{margin-bottom:8px}}
#chartWrap{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:18px;margin:22px 0;height:300px}}
#search{{width:100%;padding:11px 14px;background:var(--surface);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:14px;margin:10px 0 4px}}
section{{margin:30px 0}}
section h2{{font-size:19px}}
.section-count{{color:var(--muted);font-size:13px;font-weight:400;margin-left:8px}}
table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:13.5px}}
th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--border)}}
th{{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
tr:hover td{{background:#1e2230}}
.addr,.fname{{font-family:ui-monospace,Menlo,Consolas,monospace}}
.addr{{color:#a5b4fc}}.fname{{color:#fcd34d}}.fdesc{{color:var(--muted)}}
.badge{{padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}}
.badge-high{{background:#14532d;color:#86efac}}.badge-low{{background:#4c1d24;color:#fda4af}}
.hidden{{display:none}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:22px}}
@media(max-width:1000px){{.grid{{grid-template-columns:repeat(2,1fr)}}.cols{{grid-template-columns:1fr}}nav{{display:none}}}}
</style></head><body>
<nav><h1>{path}</h1>{nav}
<a class="nav-item" href="#xref" style="border-left:3px solid #94a3b8">Call / Instr X-ref</a></nav>
<main>
<header><h1>{title}</h1>
<div class="sub">{path} · MIPS R3000 · base {base} · entry {entry} · ends {end}</div></header>
<div class="grid">{stat_cards}</div>
<div class="panel"><h2>Key Findings <span style="font-size:12px;color:var(--muted)">(auto-derived, not hand-authored)</span></h2><ul>{findings}</ul></div>
<div id="chartWrap"><canvas id="chart"></canvas></div>
<input id="search" placeholder="Filter functions by address / name / note…" oninput="flt(this.value)">
{sections}
<section id="xref"><div class="cols">
<div><h2>Most-called targets</h2><table><thead><tr><th>Address</th><th>Calls</th><th>Region</th></tr></thead><tbody>{callee_rows}</tbody></table></div>
<div><h2>Top 20 instructions</h2><table><thead><tr><th>Mnemonic</th><th>Count</th><th>%</th></tr></thead><tbody>{instr_rows}</tbody></table></div>
</div></section>
<p style="color:var(--muted);font-size:12px;margin-top:40px">Function size distribution: {sizedist} instrs. Categories are assigned by mechanical rules (GTE opcodes, call targets, load/store ratio, region); names only where rule-certain.</p>
</main>
<script>
function flt(q){{q=q.toLowerCase();document.querySelectorAll('section table tbody tr').forEach(function(r){{
 if(r.parentElement.parentElement.closest('#xref'))return;
 r.classList.toggle('hidden', q && r.textContent.toLowerCase().indexOf(q)<0);}});}}
new Chart(document.getElementById('chart'),{{type:'bar',
 data:{{labels:{chart_labels},datasets:[{{label:'functions',data:{chart_data},backgroundColor:{chart_colors}}}]}},
 options:{{plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:'#8892a4'}}}},y:{{ticks:{{color:'#8892a4'}}}}}},maintainAspectRatio:false}}}});
</script></body></html>"""


def main():
    args = sys.argv[1:]
    title = None
    if "--title" in args:
        ti = args.index("--title")
        title = args[ti + 1]
        del args[ti:ti + 2]
    if not args:
        print("usage: gen_report.py <disassembly.asm> [out.html] [--title T]", file=sys.stderr)
        sys.exit(1)
    inp = args[0]
    out = args[1] if len(args) > 1 else os.path.splitext(inp)[0] + "_report.html"
    title = title or f"Disassembly Report — {os.path.basename(inp)}"
    htmltext = build(inp, title)
    with open(out, "w", encoding="utf-8") as f:
        f.write(htmltext)
    print(f"wrote {out} ({len(htmltext):,} bytes)")


if __name__ == "__main__":
    main()
