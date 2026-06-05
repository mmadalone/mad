#!/usr/bin/env python3
"""For each per-system theme.xml in pixel-retropie, ensure its system view has
a <carousel name="systemcarousel"><staticImage>./logo.png</staticImage></carousel>
override so ES-DE's carousel picks up the correct logo for that system.

The original RetroPie theme had `<image name="logo">` in the system view but
ES-DE's carousel doesn't auto-pick those — it needs an explicit staticImage on
the carousel element.

Idempotent: re-running won't duplicate the injection.
"""
import re, sys, pathlib

THEME_DIR = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/home/deck/ES-DE/themes/pixel-retropie")

# Match the system view block. Capture the opening tag, body, closing tag.
RE_SYSTEM_VIEW = re.compile(r'(<view\s+name="system"\s*>)(.*?)(</view>)', re.S)
RE_HAS_CAROUSEL = re.compile(r'<carousel\b', re.S)

INJECTION = """
    <carousel name="systemcarousel">
        <staticImage>./logo.png</staticImage>
    </carousel>
"""

count_injected = 0
count_skipped = 0

for xml in THEME_DIR.glob("*/theme.xml"):
    # Skip the root theme.xml (only in subdirs).
    if xml.parent == THEME_DIR:
        continue
    src = xml.read_text(encoding="utf-8", errors="replace")
    m = RE_SYSTEM_VIEW.search(src)
    if not m:
        # No system view in this per-system theme — skip
        continue
    body = m.group(2)
    if RE_HAS_CAROUSEL.search(body):
        count_skipped += 1
        continue
    new_body = body.rstrip() + INJECTION
    out = src[:m.start(2)] + new_body + src[m.end(2):]
    xml.write_text(out, encoding="utf-8")
    count_injected += 1

print(f"==> injected carousel/staticImage into {count_injected} per-system theme.xml files")
print(f"    skipped {count_skipped} that already had a carousel definition")
