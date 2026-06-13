import re,html,os,glob,time,shutil,difflib
import xml.etree.ElementTree as ET
GLD=os.path.expanduser("~/ES-DE/gamelists"); MEDIA="/run/media/deck/1tbDeck/downloaded_media"
FIELDS=['desc','developer','publisher','genre','releasedate','players','rating']
MT=['covers','miximages','marquees','screenshots']
import sys as _s
_s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.proc_guard import abort_if_esde_running
from lib import fsutil
SYS=_s.argv[1:] or ['snes','sfc','nes','famicom','mastersystem','pcengine','pcenginecd','segacd','sega32x','saturn','amigacd32','n64','dreamcast']
TS=time.strftime("%Y%m%d-%H%M%S")
NAME_OVERWRITE = os.environ.get('NAME_OVERWRITE','1')=='1'
def norm(s):
    s=s.lower(); s=re.sub(r'\([^)]*\)|\[[^\]]*\]','',s)
    s=re.sub(r'\b(the|a|of|and|in|no|de|le|la)\b','',s); return re.sub(r'[^a-z0-9]','',s)
summ=[]; flagged_all=[]
if abort_if_esde_running("apply Skyscraper metadata"):
    _s.exit(1)
for sysn in SYS:
    tmp=f"/tmp/sky-{sysn}/gl/gamelist.xml"; real=os.path.join(GLD,sysn,"gamelist.xml")
    if not os.path.isfile(tmp): summ.append((sysn,0,0,0,'NO /tmp gl')); continue
    scr={}
    t=open(tmp,encoding='utf-8',errors='replace').read()
    for m in re.finditer(r'<game>(.*?)</game>',t,re.S):
        b=m.group(1); nm=re.search(r'<name>(.*?)</name>',b,re.S); pa=re.search(r'<path>([^<]*)</path>',b)
        if not nm or not pa: continue
        stem=os.path.splitext(os.path.basename(html.unescape(pa.group(1))))[0]
        scr[stem]=(nm.group(1), {f:mm.group(1) for f in FIELDS for mm in [re.search(rf'<{f}>(.*?)</{f}>',b,re.S)] if mm})
    rt=open(real,encoding='utf-8').read(); shutil.copy(real, real+f".bak-metaall-{TS}")
    st={'ap':0}; flagged=[]
    def edit(mo):
        blk=mo.group(0); pa=re.search(r'<path>\./([^<]+)</path>',blk); nm=re.search(r'<name>(.*?)</name>',blk,re.S)
        if not pa or not nm: return blk
        stem=os.path.splitext(html.unescape(pa.group(1)))[0]
        if stem not in scr: return blk
        cur=html.unescape(nm.group(1)); sc_raw,meta=scr[stem]; sc=html.unescape(sc_raw)
        a,bb=norm(cur),norm(sc)
        ok = a==bb or (a and bb and (a in bb or bb in a)) or difflib.SequenceMatcher(None,a,bb).ratio()>=0.72
        if not ok: flagged.append((cur,sc,stem)); return blk
        st['ap']+=1
        if NAME_OVERWRITE: blk=blk.replace(f'<name>{nm.group(1)}</name>',f'<name>{sc_raw}</name>',1)
        for f,val in meta.items():
            if re.search(rf'<{f}>.*?</{f}>',blk,re.S): blk=re.sub(rf'<{f}>.*?</{f}>',f'<{f}>{val}</{f}>',blk,count=1,flags=re.S)
            else: blk=blk.replace('</game>',f'\t<{f}>{val}</{f}>\n\t</game>',1)
        return blk
    new=re.sub(r'\t<game>.*?</game>',edit,rt,flags=re.S); fsutil.atomic_write(real, new)
    try: ET.fromstring('<root>'+re.sub(r'^<\?xml[^>]*\?>','',new).strip()+'</root>')
    except ET.ParseError as e: summ.append((sysn,0,0,0,f'XML ERROR {e}')); shutil.copy(real+f".bak-metaall-{TS}",real); continue
    dm=os.path.join(MEDIA,sysn); cd=os.path.join(dm,'covers')
    cov={os.path.splitext(f)[0] for f in os.listdir(cd)} if os.path.isdir(cd) else set()
    filled=0
    for stem in scr:
        if stem in cov: continue
        for mt in MT:
            for src in glob.glob(os.path.join(f"/tmp/sky-{sysn}/media",mt,glob.escape(stem)+'.*')):
                try:
                    dd=os.path.join(dm,mt)
                    if not os.path.isdir(dd):
                        try: os.makedirs(dd)
                        except Exception: pass
                    shutil.copy(src,os.path.join(dd,os.path.basename(src))); filled+=1
                except Exception: pass
    summ.append((sysn,st['ap'],len(flagged),filled,'ok'))
    for cur,sc,stem in flagged: flagged_all.append((sysn,cur,sc))
print(f"{'SYSTEM':13}{'applied':>8}{'flagged':>8}{'artfill':>8}")
ta=tf=0
for s,ap,fl,ar,note in summ:
    print(f"{s:13}{ap:>8}{fl:>8}{ar:>8}  {note if note!='ok' else ''}"); ta+=ap; tf+=fl
print(f"\nTOTAL applied {ta}, flagged/held {tf}")
import json
try: prev=json.load(open('/tmp/flagged_all.json'))
except Exception: prev=[]
json.dump(prev+flagged_all,open('/tmp/flagged_all.json','w'))
