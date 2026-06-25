"""
Theme the MAD control panel to match the active ES-DE theme.

Resolution chain (first that yields colours wins):
  1. SIDECAR  — `~/ES-DE/themes/<theme>/router-config/theme.toml`, a small palette/
     font file a theme author drops in. Authoritative, hand-authorable, all keys
     optional. (ES-DE only parses capabilities.xml/theme.xml/includes, so this file
     is ignored by ES-DE and travels with the theme.)
  2. AUTO-EXTRACT — pull colours from the theme's own theme.xml, resolving
     `<variables>`/`${var}` references and honouring the ACTIVE variant + colorScheme
     (es_settings.xml ThemeVariant / ThemeColorScheme).
  3. FALLBACK — a built-in dark palette + DejaVu Sans.

A theme's literal background is often a mid grey that's unusable behind small text
at TV distance, so auto-extract keeps a dark neutral base and only *tints* it toward
the theme; a sidecar may set `bg`/`panel` explicitly and we trust it.

No pip: TOML via stdlib `tomllib` (py3.11+); font registration copies the .ttf into
~/.local/share/fonts + `fc-cache`, then `fc-scan` for the family name.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

try:
    import tomllib                            # py3.11+ (Deck has 3.13)
except ImportError:                           # pragma: no cover
    tomllib = None

try:
    from . import esde_settings
except ImportError:                           # run directly (python3 lib/gui_theme.py)
    import esde_settings                       # type: ignore

# Built-in dark fallback (the GUI's original palette + selection keys).
FALLBACK = {
    "bg": "#15171c",        # window background
    "surface": "#1b1e25",   # row / card / sidebar background
    "panel": "#1b1e25",     # alias of surface (sidecar-friendly name)
    "row": "#222730",       # interactive control background (resting)
    "border": "#2c313c",    # control border (unfocused)
    "text": "#e6e6e6",      # primary text
    "text_dim": "#8a8f99",  # captions / secondary text
    "accent": "#5db0ff",    # headings / Player-1 / accent
    "accent2": "#c5d0ff",   # secondary accent
    "warn": "#d0a000",      # semantic warning/attention (amber)
    "selectBg": "#5db0ff",  # focused control background
    "selectFg": "#0b0d10",  # focused control text
    "selectorColor": "#5db0ff",  # focus ring colour
}
FALLBACK_FONT = "DejaVu Sans"
FALLBACK_MONO = "DejaVu Sans Mono"
FONT_DEST = Path.home() / ".local" / "share" / "fonts"

_HEX = re.compile(r"#?([0-9a-fA-F]{8}|[0-9a-fA-F]{6})\b")

# Font-scale presets (multipliers). 'auto' = larger on a TV (docked), smaller when
# handheld so text doesn't clip on the small screen.
SCALE_PRESETS = {"xsmall": 0.70, "small": 0.85, "normal": 1.0,
                 "large": 1.25, "xlarge": 1.50}


def external_display_connected() -> bool:
    """True if an external display (HDMI/DP — e.g. the dock) is connected. The Deck's
    dock is DisplayPort, so we check both HDMI* and DP* DRM connector status nodes."""
    import glob
    for s in (glob.glob("/sys/class/drm/card*-HDMI*/status")
              + glob.glob("/sys/class/drm/card*-DP*/status")):
        try:
            with open(s) as f:
                if f.read().strip() == "connected":
                    return True
        except OSError:
            pass
    return False


def resolve_scale(mode: str) -> float:
    if mode in SCALE_PRESETS:
        return SCALE_PRESETS[mode]
    return SCALE_PRESETS["large"] if external_display_connected() else SCALE_PRESETS["small"]
_VARREF = re.compile(r"\$\{([^}]+)\}")
# Sidecar keys -> internal palette keys (accept friendly names; pass internal too).
_SIDECAR_MAP = {"fg": "text", "fgDim": "text_dim", "fgdim": "text_dim",
                "dim": "text_dim", "panel": "surface"}
_PALETTE_KEYS = set(FALLBACK)


# ── colour helpers ──────────────────────────────────────────────────────────
def _norm_hex(raw: str) -> str | None:
    """'bc14ff' / '#80808040' -> '#bc14ff' (drop alpha); None if not a hex."""
    if not raw:
        return None
    s = raw.strip()
    m = _HEX.fullmatch(s.lstrip("#")) or _HEX.match(s)
    if not m:
        return None
    return "#" + m.group(1)[:6].lower()


def _rgb(h: str):
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _luminance(h: str) -> float:
    r, g, b = _rgb(h)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _contrast_text(bg: str) -> str:
    """Black or near-white, whichever is legible on `bg`."""
    return "#0b0d10" if _luminance(bg) >= 0.5 else "#f4f4f5"


def _mix(a: str, b: str, t: float) -> str:
    """Linear blend a->b by t (0..1)."""
    ar, ag, ab = _rgb(a); br, bg_, bb = _rgb(b)
    r = round(ar + (br - ar) * t); g = round(ag + (bg_ - ag) * t); bl = round(ab + (bb - ab) * t)
    return "#%02x%02x%02x" % (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, bl)))


# ── sidecar (authoritative) ─────────────────────────────────────────────────
def _load_sidecar(theme_dir: Path) -> dict:
    f = theme_dir / "router-config" / "theme.toml"
    if not (tomllib and f.is_file()):
        return {}
    try:
        return tomllib.loads(f.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


# ── variable-aware auto-extract ─────────────────────────────────────────────
def _theme_xml_files(theme_dir: Path) -> list[Path]:
    seen, out = set(), []
    for p in ([theme_dir / "theme.xml", theme_dir / "system" / "theme.xml"]
              + sorted(theme_dir.glob("*.xml")) + sorted(theme_dir.glob("system/*.xml"))
              + sorted(theme_dir.glob("colors/*.xml")) + sorted(theme_dir.glob("_inc/**/*.xml"))):
        if p.is_file() and p not in seen:
            seen.add(p); out.append(p)
    return out


def _vars_in(elem) -> dict:
    """All <variables> children (name->value) found anywhere under `elem`."""
    out = {}
    for vb in elem.iter("variables"):
        for child in vb:
            if child.text and child.text.strip():
                out[child.tag] = child.text.strip()
    return out


def _collect(theme_dir: Path, variant: str, color_scheme: str):
    """Return (colors_by_tag, [scheme_vars, variant_vars, global_vars]) honouring
    the active variant + colorScheme. Best-effort across the theme's XML files."""
    from xml.etree import ElementTree as ET
    glob_v, var_v, sch_v = {}, {}, {}
    colors: dict = {}
    tags = ("selectedColor", "primaryColor", "secondaryColor", "selectorColor",
            "themeColor", "selectColor", "textColor")
    for f in _theme_xml_files(theme_dir):
        try:
            root = ET.fromstring(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        # top-level <variables> (direct children of root)
        for vb in root.findall("variables"):
            for child in vb:
                if child.text and child.text.strip():
                    glob_v.setdefault(child.tag, child.text.strip())
        for v_el in root.iter("variant"):
            if v_el.get("name") == variant:
                for k, val in _vars_in(v_el).items():
                    var_v.setdefault(k, val)
        if color_scheme and color_scheme != "none":
            for c_el in root.iter("colorScheme"):
                if c_el.get("name") == color_scheme:
                    for k, val in _vars_in(c_el).items():
                        sch_v.setdefault(k, val)
        for tag in tags:
            for el in root.iter(tag):
                if el.text and el.text.strip():
                    colors.setdefault(tag, el.text.strip())
                    break
    return colors, [sch_v, var_v, glob_v]


def _resolve(val: str, varmaps: list[dict]) -> str:
    """Substitute ${name} (incl. partial-append like ${themeColor}40) from the
    var maps in priority order; leaves unknown refs as-is."""
    def sub(m):
        name = m.group(1)
        for mp in varmaps:
            if name in mp:
                return mp[name]
        return m.group(0)
    prev = None
    for _ in range(64):                       # hard cap: a cyclic ${a}->${b}->${a} ref oscillates
        if prev == val or "${" not in val:    # (prev != val forever), so bound the chain-resolve
            break
        prev = val
        val = _VARREF.sub(sub, val)
    return val


def _auto_palette(theme_dir: Path, variant: str, color_scheme: str) -> dict:
    colors_raw, varmaps = _collect(theme_dir, variant, color_scheme)
    col = {}
    for tag, raw in colors_raw.items():
        hx = _norm_hex(_resolve(raw, varmaps))
        if hx:
            col[tag] = hx
    accent = col.get("selectedColor") or col.get("selectColor") or col.get("themeColor")
    if not accent:
        return {}                              # nothing usable -> caller keeps fallback
    pal = {"accent": accent}
    sel = col.get("selectorColor") or accent
    pal["selectorColor"] = sel
    pal["selectBg"] = accent
    pal["selectFg"] = _contrast_text(accent)
    if col.get("secondaryColor") or col.get("themeColor"):
        pal["accent2"] = col.get("secondaryColor") or col.get("themeColor")
    text = col.get("primaryColor") or col.get("textColor")
    if text and _luminance(text) >= 0.5:       # only adopt a light text colour
        pal["text"] = text
    # Subtle theme tint of the (dark) chrome — keep legible by mixing only a little
    # of the theme colour into the near-black fallbacks.
    tint = col.get("themeColor") or accent
    pal["bg"] = _mix(FALLBACK["bg"], tint, 0.10)
    pal["surface"] = _mix(FALLBACK["surface"], tint, 0.10)
    pal["panel"] = pal["surface"]
    pal["row"] = _mix(FALLBACK["row"], tint, 0.12)
    pal["border"] = _mix(FALLBACK["border"], accent, 0.25)
    return pal


# ── font registration ───────────────────────────────────────────────────────
def _register_font(theme_dir: Path, rel: str | None = None) -> str | None:
    """Copy a theme font into the user font dir, refresh the cache, return its
    family name. `rel` = sidecar-specified theme-relative path; else art/font.ttf
    or the first .ttf found. None on any failure."""
    if rel:
        src = (theme_dir / rel)
    else:
        src = theme_dir / "art" / "font.ttf"
        if not src.is_file():
            cands = sorted(theme_dir.glob("**/*.ttf"))
            if not cands:
                return None
            src = cands[0]
    if not src.is_file() or not shutil.which("fc-scan"):
        return None
    try:
        FONT_DEST.mkdir(parents=True, exist_ok=True)
        dest = FONT_DEST / f"mad-{theme_dir.name}-{src.stem}.ttf"
        if not dest.is_file():
            shutil.copy2(src, dest)
            if shutil.which("fc-cache"):
                subprocess.run(["fc-cache", "-f", str(FONT_DEST)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=20)
        fam = subprocess.run(["fc-scan", "--format", "%{family}", str(dest)],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return (fam.split(",")[0].strip()) or None
    except (OSError, subprocess.SubprocessError):
        return None


class Theme:
    """Resolved palette + font for the GUI. Construct once at startup."""

    def __init__(self, use_theme_colors: bool = True, use_theme_font: bool = True,
                 font_scale: str = "auto"):
        self.colors = dict(FALLBACK)
        self.family = FALLBACK_FONT
        self.mono = FALLBACK_MONO
        self.matched_colors = False
        self.matched_font = False
        self.theme_name = ""
        self.theme_dir = None                  # Path to the active theme folder
        self.source = "fallback"               # fallback | auto | sidecar
        self.pixel_font = False
        self.scale_mode = font_scale
        self.scale = resolve_scale(font_scale)   # auto: TV=large, handheld=small

        tdir = esde_settings.active_theme_dir()
        if not tdir:
            return
        self.theme_name = tdir.name
        self.theme_dir = tdir
        info = esde_settings.read()
        side = _load_sidecar(tdir)

        if use_theme_colors:
            if side:
                self._apply_palette(side)
                self.matched_colors = True
                self.source = "sidecar"
            else:
                pal = _auto_palette(tdir, info.get("variant", ""),
                                    info.get("color_scheme", "none"))
                if pal:
                    self._apply_palette(pal)
                    self.matched_colors = True
                    self.source = "auto"

        if use_theme_font:
            self.pixel_font = bool(side.get("pixelFont", False))
            fam = side.get("font")             # explicit family name wins
            if not fam:
                fam = _register_font(tdir, side.get("fontFile"))
            elif side.get("fontFile"):
                _register_font(tdir, side.get("fontFile"))   # ensure it's installed
            if fam:
                self.family = fam
                self.matched_font = True
            self._font_pt = side.get("fontSizePt")

    def _apply_palette(self, p: dict):
        """Merge a palette dict (sidecar friendly names OR auto-extract internal
        names) into self.colors, normalising hex and deriving missing selection
        colours from the accent."""
        for k, v in p.items():
            key = _SIDECAR_MAP.get(k, k)
            if key in _PALETTE_KEYS and isinstance(v, str):
                hx = _norm_hex(v)
                if hx:
                    self.colors[key] = hx
        c = self.colors
        # Derive selection colours if the source didn't set them.
        if "selectBg" not in p and "accent" in p:
            c["selectBg"] = c["accent"]
        if "selectFg" not in p and ("selectBg" in p or "accent" in p):
            c["selectFg"] = _contrast_text(c["selectBg"])
        if "selectorColor" not in p:
            c["selectorColor"] = c.get("accent", c["selectorColor"])
        c["panel"] = c["surface"]              # panel is just a sidecar-friendly alias

    def font(self, size: int, bold: bool = False, mono: bool = False):
        fam = self.mono if mono else self.family
        bump = 0 if (mono or not self.matched_font) else 1
        sz = max(7, int(round((int(size) + bump) * self.scale)))   # apply font scale
        spec = [fam, sz]
        if bold:
            spec.append("bold")
        return tuple(spec)


if __name__ == "__main__":
    import json
    t = Theme()
    print("theme:", t.theme_name, "| source:", t.source)
    print("matched_colors:", t.matched_colors, " matched_font:", t.matched_font,
          " family:", t.family, " pixel_font:", t.pixel_font)
    print(json.dumps(t.colors, indent=2))
