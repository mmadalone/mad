#!/usr/bin/env python3
"""
Convert classic EmulationStation theme (formatVersion 3) to ES-DE format.
Applies the transformations from lilbud's conversion guide to every theme.xml
in a theme directory.

Operates as a text-level transformer (regex + targeted XML rewrites) rather
than a full XML round-trip — that preserves comments, whitespace, and per-line
attribute ordering, which matters for readability of the converted files.
"""
import re, sys, pathlib, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import esde_settings

THEME_DIR = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                         else esde_settings.APPDATA / "themes" / "pixel-retropie")

# views that need to be wrapped: <view name="X"> ... </view> becomes
# <variant name="X"><view name="gamelist"> ... </view></variant>
VIEWS_TO_WRAP = ("detailed", "basic", "video")

# Image elements that are wholly cosmetic and lack a clear metadata mapping; we
# default them to imageType=image. Theme authors can fine-tune later.
DEFAULT_IMAGE_TYPE = "image"

# Pattern fragments.
RE_EXTRA_ATTR     = re.compile(r'\s+extra="true"')
RE_FORMAT_VER     = re.compile(r'\s*<formatVersion>.*?</formatVersion>\s*\n?', re.S)
RE_SHOW_SNAP_NV   = re.compile(r'\s*<showSnapshotNoVideo>.*?</showSnapshotNoVideo>\s*\n?', re.S)
RE_SHOW_SNAP_DEL  = re.compile(r'\s*<showSnapshotDelay>.*?</showSnapshotDelay>\s*\n?', re.S)
RE_FORCE_UP_1     = re.compile(r'<forceUppercase>\s*1\s*</forceUppercase>')
RE_FORCE_UP_0     = re.compile(r'<forceUppercase>\s*0\s*</forceUppercase>')

# Carousel element renames (from official ES-DE THEMES-DEV.md):
#   logoSize → itemSize, logoScale → itemScale, maxLogoCount → maxItemCount,
#   logoRotation → itemRotation, logoRotationOrigin → itemRotationOrigin,
#   logoAlignment → itemHorizontalAlignment (best-effort default).
# Textlist: selectorOffsetY → selectorVerticalOffset
# These only apply inside <carousel> / <textlist>, but the tag names are unique
# enough that a global rename is safe (no false hits in standard ES themes).
CAROUSEL_RENAMES = [
    ('logoSize',             'itemSize'),
    ('logoScale',            'itemScale'),
    ('maxLogoCount',         'maxItemCount'),
    ('logoRotation',         'itemRotation'),
    ('logoRotationOrigin',   'itemRotationOrigin'),
    ('logoAlignment',        'itemHorizontalAlignment'),
    ('selectorOffsetY',      'selectorVerticalOffset'),
]
# precompiled patterns for open + close tags of each
RE_CAROUSEL_RENAMES = [
    (re.compile(rf'<{old}\b'),  f'<{new}',  re.compile(rf'</{old}>'),  f'</{new}>')
    for old, new in CAROUSEL_RENAMES
]

# Removed entirely in ES-DE: <systemInfo>...</systemInfo>, <logoText>...</logoText>
RE_DROPPED_TAGS = [
    re.compile(r'\s*<systemInfo>.*?</systemInfo>\s*\n?', re.S),
    re.compile(r'\s*<logoText>.*?</logoText>\s*\n?',     re.S),
]

# Wrap a single named view in a variant. Greedy across newlines via the (?s) flag.
def make_view_wrapper(view_name):
    pat = re.compile(
        rf'(<view\s+name="{view_name}"\s*>)(.*?)(</view>)',
        re.S
    )
    def repl(m):
        inner = m.group(2)
        return (f'<variant name="{view_name}">\n'
                f'    <view name="gamelist">{inner}</view>\n'
                f'</variant>')
    return pat, repl

# Classic ES allowed `<view name="A, B">` to define one block applying to
# multiple views. ES-DE requires one <variant> per name. Split such blocks
# into separate <view name="A"> ... <view name="B"> ... blocks BEFORE the
# single-name wrapper runs.
RE_VIEW_MULTINAME = re.compile(
    r'<view\s+name="([a-z]+(?:\s*,\s*[a-z]+)+)"\s*>(.*?)</view>',
    re.S | re.I
)
def split_multiname_views(text: str) -> str:
    def repl(m):
        names = [n.strip() for n in m.group(1).split(',')]
        body = m.group(2)
        return ''.join(f'<view name="{n}">{body}</view>\n' for n in names)
    return RE_VIEW_MULTINAME.sub(repl, text)

def transform(text: str) -> str:
    # 1. strip extra="true"
    text = RE_EXTRA_ATTR.sub('', text)
    # 2. drop formatVersion + showSnapshot* (no-ops in ES-DE)
    text = RE_FORMAT_VER.sub('\n', text)
    text = RE_SHOW_SNAP_NV.sub('', text)
    text = RE_SHOW_SNAP_DEL.sub('', text)
    # 3. forceUppercase -> letterCase
    text = RE_FORCE_UP_1.sub('<letterCase>uppercase</letterCase>', text)
    text = RE_FORCE_UP_0.sub('<letterCase>none</letterCase>', text)
    # 4. carousel + textlist element renames
    for open_re, open_sub, close_re, close_sub in RE_CAROUSEL_RENAMES:
        text = open_re.sub(open_sub, text)
        text = close_re.sub(close_sub, text)
    # 5. drop tags that don't exist in ES-DE anymore
    for re_drop in RE_DROPPED_TAGS:
        text = re_drop.sub('', text)
    # 6. split <view name="A, B"> into <view name="A">...</view><view name="B">...</view>
    text = split_multiname_views(text)
    # 7. wrap the gamelist-style views into variants
    for vn in VIEWS_TO_WRAP:
        pat, repl = make_view_wrapper(vn)
        text = pat.sub(repl, text)
    return text

count_changed = 0
count_seen = 0
for xml in THEME_DIR.rglob("theme.xml"):
    count_seen += 1
    src = xml.read_text(encoding="utf-8", errors="replace")
    out = transform(src)
    if out != src:
        # leave a single .pre-esde-port backup so we can diff/restore if needed
        bak = xml.with_suffix(".xml.pre-esde-port")
        if not bak.exists():
            bak.write_text(src, encoding="utf-8")
        xml.write_text(out, encoding="utf-8")
        count_changed += 1

print(f"==> processed {count_seen} theme.xml files, modified {count_changed}")
print(f"    backups left as <name>.xml.pre-esde-port (one-time)")
