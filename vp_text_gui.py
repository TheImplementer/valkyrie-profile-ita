#!/usr/bin/env python3
"""Valkyrie Profile text editor, GUI: edit scene dialogue with an in-game preview of each box, and
menu and battle text (the "Menus" tab: title screen, camp menu, item / skill names and descriptions,
world map, and the four battle tables: battle menu and attack names, character and monster names,
battle item names and help lines, battle hints) drawn with the table's own font next to the
original, with its width.

Usage:
    python3 vp_text_gui.py [DISC.cue] [--port 8766] [--edits DIR]

Then open http://127.0.0.1:8766/. DISC.cue defaults to ~/Documents/vp.cue; it is only read.
Edits are saved as you type, as edit files in DIR (default vp-analysis/edits/, one file per scene,
the same format `vp_text_editor.py export` writes and `build` reads). "Build" writes a new
cue/bin pair (default next to the original, vp_edit.cue); "Run in TinyPSX" opens it in the
emulator window when the repository's release build and BIOS are there.

The preview draws what the game would draw, from what the editor learned (docs/VP_DIALOGUE_LOAD_PATH.md,
"Editing the dialogue" and "The message box"):
  * the record bytes are exactly what `build` writes (same encoder, glyphs added to the font
    included), drawn with the scene's own glyph bitmaps and widths;
  * the box position and size are the script's pushes for that record (x / y anchors 0-4 resolved
    the way the library wrapper func_033c does), or the fitted size when "Fit boxes" is on;
  * layout follows the game's layout pass (0x80052C3C): advance = width byte x width scale, word
    wrap when the word would reach the box width, line height 12 x height scale + 2, pages at
    <page>, and a box shorter than its text scrolls a line at a time;
  * glyph colours are the four palette sets of colour command 4, with the 1-pixel shadow;
  * the box artwork is cut from a TinyPSX screenshot (vp_box_skin.png, see vp_box_skin.py) and
    9-sliced to the box size.
"""
import argparse
import collections
import http.server
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import traceback
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


ed = _load('vp_text_editor')
vm = ed.vmenu
vb = ed.vbattle

# Colour command 4: palette set (argument & 3) -> (glyph, shadow), measured in TinyPSX from an
# edited line showing all four sets (set 0 is every ordinary line).
PALETTE = [[(16, 16, 16), (140, 140, 140)], [(115, 41, 0), (189, 148, 99)],
           [(107, 0, 0), (189, 140, 140)], [(0, 123, 57), (115, 173, 132)]]
TINYPSX = os.path.join(REPO, 'target', 'release', 'tinypsx')
BIOS = os.path.join(REPO, 'local', 'bios', 'scph1001.bin')


class Workspace:
    def __init__(self, cue, edits_dir):
        self.cue = os.path.abspath(cue)
        self.disc, self.arc, self.dirw, self.names = ed.open_disc(self.cue)
        self.edits_dir = edits_dir
        os.makedirs(edits_dir, exist_ok=True)
        self.lock = threading.RLock()
        self.cache = collections.OrderedDict()
        self.db = None
        try:
            self.map = json.load(open(os.path.join(HERE, 'vp_scene_map.json'), encoding='utf-8'))
        except OSError:
            self.map = []
        self.edits = {}; self.menu_edits = {}         # menu_edits: {table key: {id: text}}
        self.no_link = set()                          # table keys whose edit file says "# no-link"
        self.title = None                             # title-menu labels (vp_title_menu), if a file has them
        self.worldmap = {}                            # world-map text (vp_worldmap_text): built as it is
        self.system = {}                              # system messages (records 0-13 of every scene): likewise
        for f in sorted(os.listdir(edits_dir)):
            if f.endswith('.txt'):
                path = os.path.join(edits_dir, f)
                try:
                    if ed.vtitle.is_title_edit_file(path):
                        self.title = ed.vtitle.read_edit_file(path)     # built as it is, not edited here
                        continue
                    if ed.vwm.is_edit_file(path):
                        self.worldmap.update(ed.vwm.read_edit_file(path))   # likewise
                        continue
                    if ed.is_system_edit_file(path):
                        self.system.update(ed.read_system_edit_file(path))  # likewise
                        continue
                    if vm.is_menu_edit_file(path):
                        word, recs = vm.read_edit_file(path)
                        self.menu_edits.setdefault(str(word), {}).update(recs)
                        if vm.no_link_file(path):
                            self.no_link.add(str(word))
                        continue
                    if vb.is_battle_edit_file(path):
                        key, recs = vb.read_edit_file(path)
                        self.menu_edits.setdefault(key, {}).update(recs)
                        if vm.no_link_file(path):
                            self.no_link.add(key)
                        continue
                    scene, recs = ed.read_edit_file(path)
                except SystemExit:
                    continue
                self.edits.setdefault(scene, {}).update(recs)
        self.tables = ed.text_tables(self.arc, self.dirw)
        self.copies = collections.defaultdict(list)     # (id, original text) -> [table keys]
        for w, tb in self.tables.items():
            for (mid, _), s in zip(tb.pairs, tb.orig):
                key = (mid, tb.decode(s))
                if w not in self.copies[key]:
                    self.copies[key].append(w)
        self.index = None
        threading.Thread(target=self._build_index, daemon=True).start()

    # -- scenes
    def scene(self, k):
        with self.lock:
            if k in self.cache:
                self.cache.move_to_end(k); return self.cache[k]
            s = ed.Scene(self.arc, self.dirw, k, self.names)
            self.cache[k] = s
            if len(self.cache) > 24:
                self.cache.popitem(last=False)
            return s

    def glyph_db(self):
        with self.lock:
            if self.db is None:
                self.db = ed.glyph_db(self.arc, self.dirw, self.names)
            return self.db

    def place(self, k):
        try:
            return ' | '.join(self.map[k].get('place') or [])
        except (IndexError, AttributeError):
            return ''

    def _build_index(self):
        idx = []
        for k in range(ed.vsn.SCENE_COUNT):
            try:
                s = ed.Scene(self.arc, self.dirw, k, self.names)
            except (SystemExit, AssertionError, Exception):
                continue
            for r in range(14, len(s.orig_records)):
                plain = re.sub(r'<[^>]*>', '', s.text(r)).replace('\n', ' ')
                if plain.strip():
                    idx.append((k, r, plain))
        self.index = idx

    def scenes(self):
        out = []
        for row in self.map:
            if row.get('container') is None:
                continue
            k = row['scene']
            out.append({'id': k, 'place': ' | '.join(row.get('place') or []), 'first': row.get('first_line', ''),
                        'records': row.get('records', 0), 'edited': len(self.edits.get(k, {}))})
        return out

    # -- one scene for the page
    def scene_json(self, k):
        s = self.scene(k)
        calls = ed.box_calls(s.orig_script)
        boxes = {r: [{'x': v[0], 'y': v[1], 'w': v[2], 'h': v[3], 'wait': v[5], 'func': ed.BOX_FUNCS[f],
                      'word': iw + 4} for iw, ih, v, f in lst] for r, lst in calls.items() if r < len(s.orig_records)}
        edits = self.edits.get(k, {})
        recs = []
        for r in range(len(s.orig_records)):
            t = s.text(r)
            recs.append({'k': r, 'orig': t, 'text': edits.get(r, t), 'edited': r in edits})
        f = s.font
        return {'id': k, 'place': self.place(k), 'word': s.word, 'sectors': [s.pkg.first, s.pkg.end - 1],
                'records': recs, 'boxes': boxes,
                'font': {'n': f.n, 'widths': f.widths, 'bitmaps': [b.hex() for b in f.bitmaps],
                         'chars': [f.char(c) for c in range(1, f.n + 1)]},
                'prefer': s.preferred_codes()}

    def preview(self, k, r, text):
        s = self.scene(k)
        try:
            rec, added = s.preview(r, text, self.glyph_db())
        except ValueError as e:
            return {'ok': False, 'error': str(e)}
        ops = []; i = 0
        while i < len(rec):
            b = rec[i]
            if b & 0x80:
                v = (b & 0x7F) | rec[i + 1] << 7
                if v < 0x4000:
                    ops.append(['g', v]); i += 2; continue
                c = v - 0x4000; n = ed.ARG_BYTES.get(c, 0)
                ops.append(['c', c, list(rec[i + 2:i + 2 + n])]); i += 2 + n
            else:
                ops.append(['g', b]); i += 1
        font = ed.Font(s.font.raw(), s.font.names)
        for c, (bm, w) in sorted(added.items()):
            font.add(bytes.fromhex(bm), w)
        px, ph = ed.measure(rec, font)
        return {'ok': True, 'ops': ops, 'added': {c: v for c, v in added.items()}, 'px': px, 'ph': ph,
                'bytes': len(rec), 'orig_bytes': len(s.orig_records[r])}

    # -- edits
    def save(self, k, r, text):
        s = self.scene(k)
        try:
            ed.parse(text)
        except ValueError as e:
            return {'ok': False, 'error': str(e)}
        with self.lock:
            recs = self.edits.setdefault(k, {})
            if text == s.text(r):
                recs.pop(r, None)
            else:
                recs[r] = text
            self._write(k)
            return {'ok': True, 'edited': r in recs, 'count': len(recs)}

    def _write(self, k):
        path = os.path.join(self.edits_dir, f'scene_{k:#05x}.txt')
        recs = self.edits.get(k, {})
        if not recs:
            self.edits.pop(k, None)
            if os.path.exists(path):
                os.remove(path)
            return
        open(path, 'w', encoding='utf-8').write(ed.edit_file_text(k, self.place(k) or '-', recs, ': edited in vp_text_gui.py'))

    def build(self, output, fit, link=True):
        output = os.path.abspath(os.path.expanduser(output))
        if not output.endswith('.cue'):
            return {'ok': False, 'log': 'the output must be a .cue path (the .bin is written next to it)'}
        log = []
        edits = [(f'edits/scene_{k:#05x}.txt', k, dict(v)) for k, v in sorted(self.edits.items()) if v]
        menu_edits = [(f'edits/{self._edit_name(w)}', w, dict(v), w in self.no_link)
                      for w, v in sorted(self.menu_edits.items()) if v]
        try:
            with self.lock:
                ed.build_image(self.disc, self.arc, self.dirw, self.names, edits, output, fit, log.append,
                               table_edits=menu_edits, link=link, title=self.title,
                               worldmap=self.worldmap, system=self.system)
        except (ValueError, SystemExit) as e:
            log.append(f'ERROR: {e}'); return {'ok': False, 'log': '\n'.join(log)}
        return {'ok': True, 'log': '\n'.join(log), 'output': output}

    def run(self, cue):
        if not (os.path.exists(TINYPSX) and os.path.exists(BIOS)):
            return {'ok': False, 'error': f'needs {TINYPSX} and {BIOS}'}
        cue = os.path.abspath(os.path.expanduser(cue))
        if not os.path.exists(cue):
            return {'ok': False, 'error': f'{cue} does not exist yet: build first'}
        p = subprocess.Popen([TINYPSX, '--bios', BIOS, '--disc', cue], cwd=REPO,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {'ok': True, 'pid': p.pid}

    # -- menu and battle text (tables keyed '2175', '1806.1', ...)
    def menus(self):
        return [{'word': w, 'kind': tb.kind, 'name': tb.name, 'count': len(tb.ids()),
                 'edited': len(self.menu_edits.get(w, {}))} for w, tb in self.tables.items()]

    @staticmethod
    def _font_json(widths, bitmaps):
        return {'n': len(widths), 'widths': list(widths), 'bitmaps': [b.hex() for b in bitmaps]}

    def menu_json(self, w):
        tb = self.tables[w]; edits = self.menu_edits.get(w, {}); msgs = []
        for mid in tb.ids():
            orig = tb.text(mid); s = tb.orig[tb.index(mid)]
            msgs.append({'id': mid, 'orig': orig, 'text': edits.get(mid, orig), 'edited': mid in edits,
                         'copies': [x for x in self.copies[(mid, orig)] if x != w], 'px': tb.width(s)})
        widths, bitmaps = tb.font or ([], [])
        out = {'word': w, 'kind': tb.kind, 'name': tb.name, 'sectors': [tb.pkg.first, tb.pkg.end - 1],
               'messages': msgs, 'font': self._font_json(widths, bitmaps)}
        if tb.kind == 'battle':
            out['file'] = tb.word
            out['chars'] = [tb.chars.get(g) for g in range(1, len(widths) + 1)]
            out['special'] = list(vb.SPECIAL)
        return out

    def _encode(self, tb, text):
        """(bytes, font json or None when the font does not change, px, characters added to the
        font) without changing tb."""
        if not tb.font:
            b = tb.encode(text, [])
            return b, None, tb.width(b), ''
        added = list(tb.added); b = tb.encode(text, added)
        font = self._font_json(*tb.glyphs(added)) if added != tb.added else None
        return b, font, tb.width(b, added), ''.join(added[len(tb.added):])

    def menu_preview(self, w, mid, text):
        tb = self.tables[w]
        try:
            b, font, px, added = self._encode(tb, text)
        except ValueError as e:
            return {'ok': False, 'error': str(e)}
        s = tb.orig[tb.index(mid)]
        out = {'ok': True, 'bytes': list(b), 'orig': list(s), 'px': px, 'orig_px': tb.width(s)}
        if font:
            out['font'] = font; out['added'] = added
        return out

    @staticmethod
    def _edit_name(w):
        return f'battle_{w}.txt' if '.' in w else f'menu_{w}.txt'

    def menu_save(self, w, mid, text):
        tb = self.tables[w]
        try:
            self._encode(tb, text)
        except ValueError as e:
            return {'ok': False, 'error': str(e)}
        with self.lock:
            recs = self.menu_edits.setdefault(w, {})
            if text == tb.text(mid):
                recs.pop(mid, None)
            else:
                recs[mid] = text
            path = os.path.join(self.edits_dir, self._edit_name(w))
            mod = vb if tb.kind == 'battle' else vm
            if recs:
                text = mod.edit_file_text(tb, recs).replace('): ', '): edited in vp_text_gui.py; ', 1)
                if w in self.no_link:
                    text = text.replace('\n\n@@', '\n# no-link\n\n@@', 1)
                open(path, 'w', encoding='utf-8').write(text)
            else:
                self.menu_edits.pop(w, None)
                if os.path.exists(path):
                    os.remove(path)
            return {'ok': True, 'edited': mid in recs, 'count': len(recs)}

    def search(self, q):
        if self.index is None:
            return {'ready': False, 'hits': []}
        q = q.lower(); hits = []
        for w, tb in self.tables.items():
            for mid in tb.ids():
                plain = re.sub(r'\{[^}]*\}', '', tb.text(mid)).replace('\n', ' ')
                if q in plain.lower():
                    hits.append({'menu': w, 'kind': tb.kind, 'id': mid, 'text': plain[:120]})
                    if len(hits) >= 100:
                        break
        for k, r, plain in self.index:
            if q in plain.lower():
                hits.append({'scene': k, 'rec': r, 'text': plain[:120]})
                if len(hits) >= 200:
                    break
        return {'ready': True, 'hits': hits}


def make_handler(ws, default_out):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype='application/json', code=200):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            elif isinstance(body, str):
                body = body.encode('utf-8')
            self.send_response(code); self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store')
            self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
            try:
                if u.path == '/':
                    return self._send(page(), 'text/html; charset=utf-8')
                if u.path in ('/skin.png', '/bg_valhalla.png', '/bg_hall.png'):
                    name = {'/skin.png': 'vp_box_skin.png', '/bg_valhalla.png': 'vp_bg_valhalla.png',
                            '/bg_hall.png': 'vp_bg_hall.png'}[u.path]
                    path = os.path.join(HERE, name)
                    if not os.path.exists(path):
                        return self._send({'error': 'missing ' + name}, code=404)
                    return self._send(open(path, 'rb').read(), 'image/png')
                if u.path == '/api/init':
                    db = ws.glyph_db()
                    return self._send({'disc': ws.cue, 'scenes': ws.scenes(), 'output': default_out,
                                       'palette': PALETTE, 'argbytes': ed.ARG_BYTES,
                                       'commands': ed.COMMAND_NAMES, 'glyphdb': db,
                                       'tinypsx': os.path.exists(TINYPSX) and os.path.exists(BIOS),
                                       'edits_dir': ws.edits_dir, 'menus': ws.menus()})
                if u.path == '/api/scene':
                    return self._send(ws.scene_json(int(q['id'][0])))
                if u.path == '/api/menu':
                    return self._send(ws.menu_json(q['word'][0]))
                if u.path == '/api/search':
                    return self._send(ws.search(q.get('q', [''])[0]))
            except Exception as e:
                traceback.print_exc(); return self._send({'error': str(e)}, code=500)
            self._send({'error': 'not found'}, code=404)

        def do_POST(self):
            n = int(self.headers.get('Content-Length', 0))
            try:
                body = json.loads(self.rfile.read(n) or b'{}')
                if self.path == '/api/preview':
                    return self._send(ws.preview(int(body['scene']), int(body['rec']), body['text']))
                if self.path == '/api/save':
                    return self._send(ws.save(int(body['scene']), int(body['rec']), body['text']))
                if self.path == '/api/build':
                    return self._send(ws.build(body['output'], bool(body.get('fit')), body.get('link', True) is not False))
                if self.path == '/api/menu_preview':
                    return self._send(ws.menu_preview(str(body['word']), int(body['id']), body['text']))
                if self.path == '/api/menu_save':
                    return self._send(ws.menu_save(str(body['word']), int(body['id']), body['text']))
                if self.path == '/api/run':
                    return self._send(ws.run(body['cue']))
            except Exception as e:
                traceback.print_exc(); return self._send({'ok': False, 'error': str(e)}, code=500)
            self._send({'error': 'not found'}, code=404)
    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('disc', nargs='?', default=os.path.expanduser('~/Documents/vp.cue'))
    ap.add_argument('--port', type=int, default=8766)
    ap.add_argument('--edits', default=os.path.join(HERE, 'edits'))
    ap.add_argument('--output', help='default output .cue (default: vp_edit.cue next to the disc)')
    a = ap.parse_args()
    ws = Workspace(a.disc, a.edits)
    out = a.output or os.path.join(os.path.dirname(os.path.abspath(a.disc)), 'vp_edit.cue')
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', a.port), make_handler(ws, out))
    print(f'Valkyrie Profile text editor: http://127.0.0.1:{a.port}/  (disc {ws.cue}, edits in {ws.edits_dir})')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def page():
    path = os.path.join(HERE, 'vp_text_gui.html')
    return open(path, encoding='utf-8').read() if os.path.exists(path) else '<p>vp_text_gui.html is missing</p>'


if __name__ == '__main__':
    main()
