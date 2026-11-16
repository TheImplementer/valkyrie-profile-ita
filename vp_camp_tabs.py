"""The camp menu's tabs: the Topic and Sort boxes above the item lists, which can be made wider.
The same program holds the small italic font's table, which `build_file` also gives the menus'
accented letters (vp_accents.small_italic_patch).

The camp menu program (file 2174, one SLZ block loaded at 0x8002F824) draws parts of its screens
from lists of 20-byte layout records, each list ending in a record whose first byte is 0xFF:

    +0  type (2 text, 5 button icon, 7 box), then 08 00 00
    +4  u32: the text's message id, or the icon number
    +12 s16 x, s16 y, relative to where the screen places the list
    +16 u16 width, u16 height (a box)

A tab is one such list: its label (type 2), its button icon (type 5) and its box (type 7). The box
function (0x8004081C) draws the frame, the background and both shadows from the box's width, so a
wider box gives a wider tab. The label is drawn in a large italic, about 1.8 times its width in
the menu font, so a translated label can outgrow the English tab.

Edit file (`export --camp-tabs`):

    # Valkyrie Profile camp menu tabs ...
    @@ 0x32 Sort
    width 112
"""
import re
import struct

HEADER = '# Valkyrie Profile camp menu tabs'
WORD = 2174                 # the camp menu program
BASE = 0x8002F824           # where it is loaded
REC = 20
MIN_W, MAX_W = 16, 160


def tabs(data):
    """[(message id, icon, box offset in the file, width, height, label x)] for each list that is a
    label, an icon and a box."""
    out = []
    for off in range(2 * REC, len(data) - REC, 4):
        if data[off] != 7 or data[off + 1:off + 4] != b'\x08\0\0' or data[off + 4:off + 16] != bytes(12):
            continue
        lab, icon = off - 2 * REC, off - REC
        if data[lab] != 2 or data[icon] != 5 or data[off + REC] != 0xFF or (lab >= REC and data[lab - REC] != 0xFF):
            continue
        mid, = struct.unpack_from('<I', data, lab + 4)
        num, = struct.unpack_from('<I', data, icon + 4)
        x, = struct.unpack_from('<h', data, lab + 12)
        w, h = struct.unpack_from('<HH', data, off + 16)
        out.append((mid, num, off, w, h, x))
    return out


ICONS = {4: 'Start', 8: 'Triangle'}


def is_edit_file(path):
    try:
        with open(path, encoding='utf-8') as f:
            return f.readline().startswith(HEADER)
    except OSError:
        return False


def read_edit_file(path):
    """{message id: width}"""
    out = {}; cur = None
    for n, line in enumerate(open(path, encoding='utf-8').read().split('\n'), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = re.fullmatch(r'@@ (0x[0-9a-fA-F]+|\d+)(?: .*)?', line)
        if m:
            cur = int(m.group(1), 0); continue
        m = re.fullmatch(r'width (\d+)', line)
        if not m or cur is None:
            raise ValueError(f'{path}:{n}: expected "@@ MESSAGE" or "width N", got {line!r}')
        out[cur] = int(m.group(1))
    return out


def edit_file_text(data, names=None):
    names = names or {}
    lines = [f'{HEADER} (file {WORD}): the boxes above the item lists',
             '# Each tab is a box with a button icon and a label (a camp menu message, translated in the',
             '# menu edit files). The label is drawn in a large italic, about 1.8 times its width in the',
             '# menu font, starting at the x below. Set a box\'s width in pixels to fit a longer label;',
             f'# the frame, background and shadows follow it ({MIN_W} to {MAX_W}).', '']
    for mid, icon, off, w, h, x in tabs(data):
        lines += [f'@@ {mid:#x} {names.get(mid, "")} ({ICONS.get(icon, f"icon {icon}")}, label at x {x})'.replace('  ', ' '),
                  f'width {w}']
    return '\n'.join(lines) + '\n'


def apply(data, widths, origin=''):
    """data with the boxes' widths set; returns (new data, [(message id, old width, new width)])."""
    data = bytearray(data); found = {t[0]: t for t in tabs(data)}; changed = []
    for mid, w in sorted(widths.items()):
        if mid not in found:
            raise ValueError(f'{origin}the camp menu has no tab labelled by message {mid:#x} '
                             f'(tabs: {", ".join(hex(m) for m in found)})')
        if not MIN_W <= w <= MAX_W:
            raise ValueError(f'{origin}tab {mid:#x}: width {w} is outside {MIN_W}..{MAX_W}')
        off, old = found[mid][2], found[mid][3]
        if w != old:
            struct.pack_into('<H', data, off + 16, w); changed.append((mid, old, w))
    return bytes(data), changed


def build_file(ed, arc, dirw, widths, log=print, accents=None):
    """(file, packed bytes, room), or None when nothing changes. `ed` is vp_text_editor's namespace.
    accents: {accented character: slot ASCII}, the letters the menus use, given table entries in the
    small italic font (its glyphs are drawn into its sheet with the italic title font's)."""
    f = ed.vtitle.TitleFile(ed, arc, dirw, WORD); f.label = f'camp menu file {WORD}'
    data, changed = apply(f.data, widths)
    for mid, old, w in changed:
        log(f'  tab {mid:#x}: box {old} -> {w} px wide')
    if accents:
        acc = ed.vmenu._load('vp_accents')
        pkg, part = acc.SMALL_SHEET
        sheet = ed.Package(arc, dirw, pkg).parts[part][1]
        tim = ed.unpack(*ed.read_container(sheet)[0])
        o = acc.SMALL_TABLE_OFFSET
        _, table, skipped = acc.small_italic_patch(tim, data[o:o + 960], accents)
        data = data[:o] + table + data[o + 960:]
        log(f'  small italic font: {"".join(c for c in sorted(accents) if c not in skipped)} drawn into package {pkg}'
            + (f'; no room or no base letter for {"".join(skipped)}' if skipped else ''))
    if data == f.data:
        return None
    packed, room = f.pack(data)
    if len(packed) > room:
        raise ValueError(f'{f.label}: needs {len(packed)} bytes, its sectors hold {room}')
    log(f'  {f.label}: {f.used} -> {len(packed)} bytes of {room}')
    return f, packed, room
