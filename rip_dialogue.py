#!/usr/bin/env python3
"""Build the dialogue codepage from a RAM dump (substitution cribs), then decode every
dialogue-bearing SLZ container in VALKYRIE.BIN to per-sector script files."""
import json, os, struct
import bin_navigator as bn

ram=open('vpram2.bin','rb').read(); LO,HI=0x60000,0xF0000
def crib(s):
    n=len(s)
    for o in range(LO,HI-n):
        w=ram[o:o+n]
        if any(b>=0x80 or b<1 for b in w): continue
        b2c={};c2b={};ok=True
        for i in range(n):
            b,c=w[i],s[i]
            if b2c.get(b,c)!=c or c2b.get(c,b)!=b: ok=False;break
            b2c[b]=c;c2b[c]=b
        if ok: return b2c
    return None

CRIBS=["Watch where you're going, girl!","I wouldn't let anything happen to her",
  "How dare you splash water","Will you offer up the artifact","We escaped together",
  "Artifacts are the rightful","Return it or Evaluation","has been sent to Valhalla",
  "going to sell her","seven year old girl there","Why are you so sad?","No matter what",
  "for some reason","to Lord Odin","Her name was","Mother Laia","You can't hold any more",
  "This item is an","Exit to World Map","Hurry up and grab your bucket","Caution",
  "Scenario","Platina","Valkyrie","spoke ill of them","She took a shine","village rat",
  "any other way","none of your business","I forget her name"]
CRIBS=sorted(set(CRIBS),key=len,reverse=True)
M={};C={}
for s in CRIBS:
    m=crib(s)
    if m and all(M.get(b,c)==c and C.get(c,b)==b for b,c in m.items()):
        for b,c in m.items(): M[b]=c; C[c]=b
json.dump({f"0x{b:02x}":c for b,c in sorted(M.items())},open('vp_codepage.json','w'),ensure_ascii=False)
print(f"codepage: {len(M)} glyphs: "+' '.join(f"{b:02x}={c}" for b,c in sorted(M.items())))

def dec(rec):
    out=[]
    for b in rec:
        if b==0x80: out.append('\n')
        elif b>=0x80 or b==0: out.append('')
        else: out.append(M.get(b,'·'))
    return '\n'.join(l for l in ''.join(out).split('\n') if l.strip())

# enumerate SLZ + score + dump
bn._fh=open(bn.BIN_PATH,'rb'); fsize=bn._fh.seek(0,2)
CHUNK=8*1024*1024; offs=[]; base=0
while base<fsize:
    bn._fh.seek(base); chunk=bn._fh.read(CHUNK+64)
    if not chunk: break
    i=0
    while True:
        j=chunk.find(b'SLZ',i)
        if j<0 or j>=CHUNK: break
        if j+12<=len(chunk) and chunk[j+3] in (1,2):
            c=struct.unpack_from('<I',chunk,j+4)[0]; d=struct.unpack_from('<I',chunk,j+8)[0]
            if 0x40<c<4_000_000 and 0x40<d<8_000_000: offs.append(base+j)
        i=j+1
    base+=CHUNK
PATS=[bytes.fromhex(h) for h in ("363334","4b3c42","33343b","383e4d","36333836","523839")]  # the you her and that was
os.makedirs('script_dump',exist_ok=True)
dumped=0; tested=0
for off in offs:
    try: data=bn.load_text_file(off)["data"]
    except Exception: continue
    tested+=1
    if sum(data.count(p) for p in PATS) < 8: continue
    sec=off//2048; recs=[]; i=0
    while i<len(data):
        j=data.find(b'\x00',i)
        if j<0:break
        rec=data[i:j]; i=j+1
        g=[b for b in rec if b<0x80]
        if len(g)>=8 and len(g)>=0.55*len(rec) and max(g)<0x70:
            t=dec(rec)
            if t and sum(c!='·' for c in t.replace('\n',''))>=0.5*len(t.replace('\n','')): recs.append(t)
    if recs:
        open(f"script_dump/sec{sec:06d}_0x{off:08X}.txt",'w').write('\n---\n'.join(recs))
        dumped+=1
    if tested%3000==0: print(f"  tested {tested}, dumped {dumped}")
print(f"done: {dumped} dialogue containers -> script_dump/")
