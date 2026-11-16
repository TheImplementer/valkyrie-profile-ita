"""Copy the shareable part of this folder into the public repository's checkout.

    python3 vp_publish.py ~/workspace/valkyrie-profile-ita-blog

Only the files matched by INCLUDE are copied: the write-ups, the tools and the Italian edit files.
Nothing from the game goes out: no executable, disassembly, text dumps, disc images, screenshots
or font bitmaps (the tools read the font through vp_glyph_names.json, which holds hashes only).
Every copied file is also checked against FORBIDDEN, and against personal paths, before anything
is written. Files the repository holds that are no longer published are listed, not deleted.
"""
import fnmatch
import glob
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

INCLUDE = [
    # write-ups
    'CLAUDE.md', 'VP_DIALOGUE_FORMAT.md', 'VP_DIALOGUE_LOAD_PATH.md', 'DIALOGUE_SEARCH.md',
    'notes/*.md', 'notes/builtins_pass2.json',
    # the Italian translation
    'edits_it/*.txt', 'edits_it/*.md',
    # the text tools and the data they need
    'vp_*.py', 'vp_text_gui.html', 'vp_glyph_names.json', 'vp_builtins.json', 'vp_codepage*.json',
    'vp_newgame.replay', 'vp_load_log.lua', 'tools/render.swift',
    # the older exploration tools
    'bin_navigator.py', 'index_images.py', 'find_container_sector.py', 'render_tim.py',
    'psx_disasm.py', 'analyze.py', 'gen_report.py', 'micro_exec.py', '_dis.py',
    'codepage.py', 'crib_codepage.py', 'rip_dialogue.py', 'scan_vp_text.py',
]

# Never published, whatever INCLUDE says.
FORBIDDEN = [
    'SLUS_*', '*.asm', '*.bin', '*.BIN', '*.cue', '*.iso', '*.png', '*.zip', '*.ppf', '*.xdelta',
    '*_report*.html', 'overlay_engine_*', 'vp_glyphs.json', 'vp_glyph_db.json', 'rare_glyphs*',
    'vp_all_scene_text.txt', 'vp_battle_text.txt', 'vp_scene_lines.txt', 'vp_scene_flow.txt',
    'vp_scene_map.*', 'images_index.json', 'release/*', 'script_dump/*', 'analysis/*',
    'story/*',                  # event_order.txt quotes the first line of every story event
]

# (split so that this line does not match itself)
PERSONAL = re.compile('/Us' r'ers/[A-Za-z]|C:[/\\]Us' r'ers[/\\][A-Za-z]|/priv' 'ate/tmp/')


def files():
    out = set()
    for pat in INCLUDE:
        out.update(os.path.relpath(p, HERE) for p in glob.glob(os.path.join(HERE, pat)) if os.path.isfile(p))
    return sorted(out)


def check(rel):
    name = os.path.basename(rel)
    for pat in FORBIDDEN:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat):
            return f'matches {pat}'
    try:
        text = open(os.path.join(HERE, rel), encoding='utf-8').read()
    except UnicodeDecodeError:
        return None if rel.endswith('.replay') else 'binary file'
    m = PERSONAL.search(text)
    if m:
        line = text[:m.start()].count('\n') + 1
        return f'personal path at line {line}'
    return None


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.split('\n\n')[1])
    dest = os.path.expanduser(sys.argv[1])
    if not os.path.isdir(os.path.join(dest, '.git')):
        raise SystemExit(f'{dest} is not a git checkout')
    todo = files()
    bad = [(f, why) for f in todo if (why := check(f))]
    if bad:
        for f, why in bad:
            print(f'refused: {f}: {why}')
        raise SystemExit('nothing copied')
    for f in todo:
        os.makedirs(os.path.join(dest, os.path.dirname(f)), exist_ok=True)
        shutil.copy2(os.path.join(HERE, f), os.path.join(dest, f))
    print(f'copied {len(todo)} files to {dest}')
    keep = set(todo) | {'README.md', '.gitignore', 'LICENSE', 'LICENSE-text'}
    stale = [p for p in os.popen(f'git -C "{dest}" ls-files').read().split('\n') if p and p not in keep]
    for p in stale:
        print(f'not published any more (remove it with git rm if so): {p}')


if __name__ == '__main__':
    main()
