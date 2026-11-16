"""The characters of Valkyrie Profile's dialogue font, looked up by glyph bitmap.

Each scene carries its own font, with its glyphs in its own order, so a glyph code means nothing by
itself: the tools find the character by the glyph's 24-byte bitmap. vp_glyph_names.json holds that
table keyed by a hash of each bitmap (the first 16 hex digits of its SHA-1), not by the bitmap,
so the repository holds no font data. GlyphNames takes the bitmap's hex, as the tools have it, and
hashes it on every lookup.
"""
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, 'vp_glyph_names.json')


def key(bitmap_hex):
    return hashlib.sha1(bytes.fromhex(bitmap_hex)).hexdigest()[:16]


class GlyphNames(dict):
    """{bitmap hash: character}, looked up by bitmap hex."""
    def get(self, bitmap_hex, default=None):
        return dict.get(self, key(bitmap_hex), default)

    def setdefault(self, bitmap_hex, ch=None):
        return dict.setdefault(self, key(bitmap_hex), ch)

    def __contains__(self, bitmap_hex):
        return dict.__contains__(self, key(bitmap_hex))

    def __getitem__(self, bitmap_hex):
        return dict.__getitem__(self, key(bitmap_hex))


def load(path=PATH):
    return GlyphNames(json.load(open(path, encoding='utf-8')))


if __name__ == '__main__':
    # Rebuild vp_glyph_names.json from a bitmap-keyed table: python3 vp_glyph_names.py vp_glyphs.json
    import sys
    src = json.load(open(sys.argv[1], encoding='utf-8'))
    out = {key(h): c for h, c in src.items()}
    assert len(out) == len(src), 'two bitmaps share a hash'
    json.dump(dict(sorted(out.items())), open(PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    print(f'{len(out)} glyphs -> {PATH}')
