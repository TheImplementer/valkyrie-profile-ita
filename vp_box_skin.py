#!/usr/bin/env python3
"""Cut the message-box artwork out of a TinyPSX screenshot, for the GUI's in-game preview.

Usage:
    python3 vp_box_skin.py SCREENSHOT.png [-o vp_box_skin.png]

The box is found by its dark outline colour (90, 66, 8). The fill is opaque (the same pixels over
Valhalla's bright sky and over the dark stone of the hall), so the cut-out is the real artwork:
the outline, the 2-pixel gold bevel with its chamfered corners, and the parchment texture shading
from about (226, 222, 209) under the top bevel to about (176, 172, 163) above the bottom one,
stretched over the box height. Text pixels, palette colours (16, 16, 16) and their (140, 140, 140)
shadow, are painted over from their row neighbours. The GUI draws any box size from it by 9-slice
scaling (8-pixel corners), which also stretches the shading vertically the way the game does.
"""
import argparse
import os

import vp_png

OUTLINE = (90, 66, 8)
TEXT = {(16, 16, 16), (140, 140, 140)}


def cut(px):
    pts = [(x, y) for y, row in enumerate(px) for x, p in enumerate(row) if p == OUTLINE]
    if not pts:
        raise SystemExit('no message box outline in this screenshot')
    x0 = min(p[0] for p in pts); x1 = max(p[0] for p in pts)
    y0 = min(p[1] for p in pts); y1 = max(p[1] for p in pts)
    box = [list(row[x0:x1 + 1]) for row in px[y0:y1 + 1]]
    for row in box:
        for x, p in enumerate(row):
            if p in TEXT:
                l = next((row[i] for i in range(x - 1, -1, -1) if row[i] not in TEXT), None)
                r = next((row[i] for i in range(x + 1, len(row)) if row[i] not in TEXT), None)
                l = l or r; r = r or l
                row[x] = tuple((a + b) // 2 for a, b in zip(l, r))
    # outside the first and last outline pixel of each row is background (the chamfered
    # corners): a key colour, magenta, that the GUI leaves transparent
    for row in box:
        ends = [i for i, p in enumerate(row) if p == OUTLINE]
        for i in range(len(row)):
            if i < ends[0] or i > ends[-1]:
                row[i] = (255, 0, 255)
    return box, (x0, y0, x1, y1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('screenshot')
    ap.add_argument('-o', '--output', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vp_box_skin.png'))
    a = ap.parse_args()
    box, rect = cut(vp_png.read(a.screenshot))
    vp_png.write(a.output, box)
    print(f'box at {rect}: {len(box[0])} x {len(box)} -> {a.output}')


if __name__ == '__main__':
    main()
