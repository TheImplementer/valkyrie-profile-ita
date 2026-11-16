#!/usr/bin/env python3
"""How much of Valkyrie Profile's text a directory of edit files translates.

  python3 vp_progress.py DISC1.cue [--edits edits_it] [-o docs/PROGRESS.md]

It reads every piece of text the editor can change from the disc: the dialogue records of the
1,118 scenes, the 13 menu tables, the 4 battle tables, the world map's text and the title-screen
labels. Then it works out which of them the edit files cover, and prints a summary (or writes a
Markdown report with -o).

How it counts:
  * The unit is a distinct English text within an area. A line that is in 36 scenes, or an item
    name in six menu tables, counts once. It is "done" when every copy of it is covered, and
    "partial" when only some are.
  * A text is weighted by its letters (A-Z), so a long speech counts for more than "Yes". Text with
    no letters (numbers, ・・・) is not counted, and neither is the speaker label above a line of dialogue.
  * A copy is covered when an edit file lists it, even with the English left as it is (a name kept
    on purpose, such as Platina's 「Lucian?」: `build` leaves an unchanged entry byte for byte). Menu and battle edits also cover the copies `build` links to: the same id with
    the same English text in another table.
  * Records 0-13 of every scene are the same system messages ("The item has broken." ...) in each
    of the 1,118 scenes. They are counted once, as their own area.

Disc 1 is the one to read: it has every scene disc 2 has, with the same text, plus 105 more, and
the menu, battle, world-map and title text is the same on both.
"""
import argparse
import collections
import datetime
import glob
import importlib.util
import os
import re
import struct
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SYSTEM_RECORDS = 14


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


ed = _load('vp_text_editor')


def letters(s):
    return len(re.findall('[A-Za-z]', s))


def plain_scene(text):
    """A dialogue record's words, without commands and without the speaker label above them."""
    lines = re.sub(r'<[^>]*>', '', text).split('\n')
    if len(lines) > 1 and lines[0].strip() and not re.match('[「(（]', lines[0].strip()) \
            and len(lines[0].split()) <= 3 and re.match('[「(（]', lines[1].strip() or '-'):
        lines = lines[1:]
    return '\n'.join(lines)


def plain_table(text):
    return re.sub(r'\{[^}]*\}', '', text)


class Area:
    """Distinct English texts, each with its copies' covered / not covered flags."""

    def __init__(self, name):
        self.name = name
        self.copies = collections.defaultdict(list)       # text -> [covered, ...]

    def add(self, text, covered):
        if letters(text):
            self.copies[text].append(bool(covered))

    def stats(self):
        s = collections.Counter()
        for text, cov in self.copies.items():
            w = letters(text)
            state = 'done' if all(cov) else 'partial' if any(cov) else 'todo'
            s[state] += 1; s[state + '_w'] += w; s['n'] += 1; s['w'] += w
            s['done_copies_w'] += w * sum(cov) / len(cov)
        return s


def pct(a, b):
    return 100.0 * a / b if b else 0.0


def read_edits(paths):
    """Edit files sorted by kind, read the way `build` reads them."""
    f = ed.EditFiles(paths)
    out = types.SimpleNamespace(scenes=collections.defaultdict(dict), tables=f.table_edits, title=f.title or {},
                                worldmap=f.worldmap, system=f.system, files=[os.path.basename(p) for p in paths])
    for _, sc, recs in f.edits:
        out.scenes[sc].update(recs)
    return out


def scene_hint(texts):
    """The first spoken words of a scene, to tell scenes apart in the report."""
    for t in texts:
        p = plain_scene(t).replace('「', '').strip()
        if letters(p):
            first = p.split('\n')[0].strip()
            return first[:40] + ('…' if len(first) > 40 else '')
    return ''


def measure(cue, edits):
    disc, arc, dirw, names = ed.open_disc(cue)
    areas = {k: Area(k) for k in ('Dialogue (scenes)', 'System messages (in every scene)', 'Menus',
                                  'Battle text', 'World map text', 'Title-screen labels')}
    scenes = []; warnings = []
    seen_scenes = set()
    system = {}
    for k in range(ed.vsn.SCENE_COUNT):
        try:
            s = ed.Scene(arc, dirw, k, names)
        except (SystemExit, AssertionError, struct.error):
            continue
        seen_scenes.add(k)
        recs = edits.scenes.get(k, {})
        texts = [s.text(r) for r in range(len(s.orig_records))]
        for r in recs:
            if not 0 <= r < len(texts):
                warnings.append(f'scene {k:#x} has no record {r}')
        for r in range(2, min(SYSTEM_RECORDS, len(texts))):     # 0 and 1: "--" and a digit line
            # the same text in every scene: one distinct text with a copy per scene
            system.setdefault(r, (plain_scene(texts[r]), []))[1].append(r in recs or r in edits.system)
        sc = Area(f'scene {k:#x}')
        for r in range(SYSTEM_RECORDS, len(texts)):
            p = plain_scene(texts[r])
            sc.add(p, r in recs); areas['Dialogue (scenes)'].add(p, r in recs)
        if sc.copies:
            scenes.append((k, scene_hint(texts[SYSTEM_RECORDS:]), sc.stats()))
    for sc in edits.scenes:
        if sc not in seen_scenes:
            warnings.append(f'an edit file is for scene {sc:#x}, which has no text on this disc')
    for text, cov in system.values():
        for c in cov:
            areas['System messages (in every scene)'].add(text, c)

    tables = ed.text_tables(arc, dirw)
    listed = collections.defaultdict(set)
    for path, key, recs, own in sorted(edits.tables, key=lambda e: bool(e[3])):
        if key not in tables:
            warnings.append(f'{os.path.basename(path)}: no table {key}'); continue
        try:
            ed.vmenu.apply_edits(tables, key, recs, not own, f'{os.path.basename(path)}: ')
        except ValueError as e:
            warnings.append(str(e))
        # an entry listed unchanged (kept in English) covers the copies `build` would link it to:
        # the same id with the same English text in the other tables
        for mid in recs:
            listed[key].add((mid, None))
            if own:
                continue
            try:
                english = tables[key].text(mid)
            except KeyError:
                continue
            for k2 in tables:
                if k2 != key:
                    listed[k2].add((mid, english))
    per_table = []
    for key, t in tables.items():
        area = areas['Menus' if t.kind == 'menu' else 'Battle text']
        ta = Area(key)
        for i, ((mid, _), o, s) in enumerate(zip(t.pairs, t.orig, t.strings)):
            english = t.decode(o)
            if english.startswith(' !"#$%&'):
                continue            # message 0 of a menu table: the font's character list, not shown
            text = plain_table(english)
            cov = s != o or (mid, None) in listed[key] or (mid, english) in listed[key]
            area.add(text, cov); ta.add(text, cov)
        per_table.append((t.kind, key, t.name, ta.stats()))

    wm = ed.vwm.WorldMapText(types.SimpleNamespace(**vars(ed)), arc, dirw, names)
    for mid in wm.ids():
        areas['World map text'].add(re.sub(r'<[^>]*>', '', wm.text(mid)), mid in edits.worldmap)
    for mid in edits.worldmap:
        if mid not in wm.orig:
            warnings.append(f'the world map table has no message {mid:#x}')

    for i, name in enumerate(ed.vtitle.ITEMS):
        areas['Title-screen labels'].add(name, i in edits.title)
    return areas, scenes, per_table, warnings


def bar(p, width=20):
    n = round(p / 100 * width)
    return '█' * n + '░' * (width - n)


def summary_rows(areas):
    rows = []; tot = collections.Counter()
    for a in areas.values():
        s = a.stats(); tot.update(s)
        rows.append((a.name, s))
    return rows, tot


def report_text(areas, scenes, per_table, warnings, edits):
    rows, tot = summary_rows(areas)
    out = ['# Translation progress', '',
           f'Generated by `vp_progress.py` on {datetime.date.today():%Y-%m-%d} from {len(edits.files)} edit '
           f'files. Don\'t edit this file by hand: run the script again after changing the edit files.', '',
           'A text counts once per area however many copies it has, and is weighted by its letters. It is '
           'done when an edit file covers every copy of it. An entry left in English on purpose counts too, '
           'so a line that needs no translation (a name, 「Lucian?」) can be marked done by listing it unchanged.',
           'Only text the editor can read is counted: words drawn in pictures (other than the title '
           'menu) or text not found yet are not.', '',
           f'**Overall: {pct(tot["done_copies_w"], tot["w"]):.1f}%** of the letters '
           f'({tot["done"]} of {tot["n"]} distinct texts done, {tot["partial"]} partly).', '',
           '| Area | Texts | Done | Partly | Progress | |', '|---|---:|---:|---:|---:|---|']
    for name, s in rows:
        p = pct(s['done_copies_w'], s['w'])
        out.append(f'| {name} | {s["n"]} | {s["done"]} | {s["partial"]} | {p:.1f}% | `{bar(p)}` |')
    out += ['', '## Menu and battle tables', '',
            '| Table | Contents | Texts | Done | Progress |', '|---|---|---:|---:|---:|']
    for kind, key, name, s in per_table:
        out.append(f'| {kind} {key} | {name} | {s["n"]} | {s["done"]} | {pct(s["done_copies_w"], s["w"]):.1f}% |')
    started = [x for x in scenes if x[2]['done'] or x[2]['partial']]
    out += ['', '## Scenes', '',
            f'{len(scenes)} scenes have dialogue; {len(started)} are started. '
            'The words are each scene\'s first spoken line, to tell them apart.', '',
            '| Scene | First line | Texts | Done | Progress |', '|---|---|---:|---:|---:|']
    for k, hint, s in started:
        out.append(f'| 0x{k:x} | {hint.replace("|", "/")} | {s["n"]} | {s["done"]} | '
                   f'{pct(s["done_copies_w"], s["w"]):.0f}% |')
    out += ['', '<details><summary>Scenes not started</summary>', '',
            '| Scene | First line | Texts | Letters |', '|---|---|---:|---:|']
    for k, hint, s in scenes:
        if not (s['done'] or s['partial']):
            out.append(f'| 0x{k:x} | {hint.replace("|", "/")} | {s["n"]} | {s["w"]} |')
    out += ['', '</details>']
    if warnings:
        out += ['', '## Warnings', ''] + [f'* {w}' for w in warnings]
    return '\n'.join(out) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('disc', help='disc 1 .cue')
    ap.add_argument('--edits', default=os.path.join(HERE, 'edits_it'), help='directory of edit files')
    ap.add_argument('-o', '--output', help='write a Markdown report here')
    a = ap.parse_args()
    paths = sorted(glob.glob(os.path.join(a.edits, '*.txt')))
    if not paths:
        raise SystemExit(f'{a.edits}: no edit files (*.txt)')
    edits = read_edits(paths)
    areas, scenes, per_table, warnings = measure(a.disc, edits)
    rows, tot = summary_rows(areas)
    for name, s in rows:
        p = pct(s['done_copies_w'], s['w'])
        print(f'{name:34s} {bar(p)} {p:5.1f}%  {s["done"]:5d} of {s["n"]:5d} texts done, {s["partial"]} partly')
    print(f'{"overall":34s} {bar(pct(tot["done_copies_w"], tot["w"]))} '
          f'{pct(tot["done_copies_w"], tot["w"]):5.1f}%  {tot["done"]:5d} of {tot["n"]:5d}')
    for w in warnings:
        print('warning:', w, file=sys.stderr)
    if a.output:
        open(a.output, 'w', encoding='utf-8').write(report_text(areas, scenes, per_table, warnings, edits))
        print(f'wrote {a.output}')


if __name__ == '__main__':
    main()
