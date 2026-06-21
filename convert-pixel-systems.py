#!/usr/bin/env python3
"""
Convert per-system theme.xml files in the RetroPie Pixel theme to modern ES-DE format.

For each per-system folder containing a theme.xml:
  1. Parse out <themeColor> and <selectColor> from the original
  2. Write a fresh modern-format per-system theme.xml that:
     - Declares those colors as variables
     - Includes the root theme.xml (./../theme.xml)
     - Overrides system view's console_overlay path
     - Overrides background and textlist colors across all variants

Folders without a theme.xml (e.g., art/, splashscreens/) are left alone.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import esde_settings

THEME_DIR = sys.argv[1] if len(sys.argv) > 1 else str(esde_settings.APPDATA / "themes" / "pixel-retropie")

DEFAULT_THEME_COLOR = "808080"
DEFAULT_SELECT_COLOR = "ffffff"

# Per-system theme.xml template (modern ES-DE 7+ format)
TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<theme>

    <variables>
        <themeColor>{theme_color}</themeColor>
        <selectColor>{select_color}</selectColor>
    </variables>

    <include>./../theme.xml</include>

    <view name="system">
        <image name="background">
            <color>${{themeColor}}</color>
        </image>
        <image name="console_overlay">
            <path>./console.png</path>
        </image>
    </view>

    <variant name="textlistBasic">
        <view name="gamelist">
            <image name="background">
                <color>${{themeColor}}</color>
            </image>
            <textlist name="gamelist">
                <selectedColor>${{selectColor}}</selectedColor>
            </textlist>
        </view>
    </variant>

    <variant name="textlistWithImages">
        <view name="gamelist">
            <image name="background">
                <color>${{themeColor}}</color>
            </image>
            <textlist name="gamelist">
                <selectorColor>${{themeColor}}40</selectorColor>
            </textlist>
        </view>
    </variant>

    <variant name="textlistWithVideos">
        <view name="gamelist">
            <image name="background">
                <color>${{themeColor}}</color>
            </image>
            <textlist name="gamelist">
                <selectorColor>${{themeColor}}40</selectorColor>
            </textlist>
        </view>
    </variant>

</theme>
"""


def extract_color(xml_text, var_name, default):
    """Extract <themeColor> or <selectColor> from a variables block."""
    pattern = rf"<{var_name}>\s*([0-9a-fA-F]+)\s*</{var_name}>"
    m = re.search(pattern, xml_text)
    if m:
        return m.group(1).lower()
    return default


def convert_one(system_dir):
    theme_xml = os.path.join(system_dir, "theme.xml")
    if not os.path.isfile(theme_xml):
        return None

    with open(theme_xml, "r", encoding="utf-8", errors="replace") as f:
        original = f.read()

    theme_color = extract_color(original, "themeColor", DEFAULT_THEME_COLOR)
    select_color = extract_color(original, "selectColor", DEFAULT_SELECT_COLOR)

    new_content = TEMPLATE.format(
        theme_color=theme_color,
        select_color=select_color,
    )

    # Save the original as a backup once
    backup = theme_xml + ".retropie-original"
    if not os.path.exists(backup):
        with open(backup, "w", encoding="utf-8") as f:
            f.write(original)

    with open(theme_xml, "w", encoding="utf-8") as f:
        f.write(new_content)

    return theme_color, select_color


def main():
    converted = []
    skipped = []
    for entry in sorted(os.listdir(THEME_DIR)):
        full = os.path.join(THEME_DIR, entry)
        if not os.path.isdir(full):
            continue
        # Skip the art folder (shared assets, not a system)
        if entry == "art":
            continue
        result = convert_one(full)
        if result is None:
            skipped.append(entry)
        else:
            tc, sc = result
            converted.append(f"  {entry:30s}  themeColor={tc}  selectColor={sc}")

    print(f"Converted {len(converted)} per-system theme.xml files:")
    for line in converted:
        print(line)
    if skipped:
        print(f"\nSkipped {len(skipped)} folders without theme.xml: {', '.join(skipped)}")


if __name__ == "__main__":
    main()
