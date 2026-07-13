"""pcsx2_fork_settings — full GLOBAL settings trees for the Namco 246/256 (pcsx2x6)
forks: the Arcade member (-portable, ~/Applications/pcsx2x6) and the Retail member
(-datapath, ~/Applications/pcsx2x6-retail).

Both forks ARE PCSX2 builds, so the encodings are identical to the shipped standard
tree. This module REUSES the standard descriptor groups (pcsx2_settings + the per-game
Hardware/Upscaling groups in pcsx2_pergame_cmds) read-only and re-slices the Graphics
page into one sub-page per emulator tab (the "Video" sub-menu), adding the Hardware
Fixes / Upscaling Fixes / Texture Replacement tabs the standard GLOBAL page omits.
Nothing here mutates the shared descriptor dicts (the engine only reads them), so the
standard PCSX2 + per-game modules are untouched.

Reality-checked 2026-07-03 vs the LIVE arcade + retail inis: of the standard tree's
187 offered keys only `OutputVolume` is absent — the forks are NEWER than the installed
standard PCSX2 and use `StandardVolume` instead (see _fork_aud_groups). The Texture
Replacement + Advanced-tab keys were confirmed present in both fork inis.

Each member is one BufferedEngine over its own ini + buffer; every category page of a
member shares that one buffer (pages are modal, so this is safe). Writes refuse while
pcsx2x6 runs (arcade + retail share the AppImage process name, so the guard is broad
but safe). Namespaces are prefixed per member: x6a_* (arcade), x6r_* (retail).
"""
from __future__ import annotations

from pathlib import Path

from .. import proc_guard
from . import pcsx2_engine
from . import pcsx2_pergame_cmds as pg
from . import pcsx2_settings as ps
from .rpc import method

ARCADE_INI = Path("~/Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini").expanduser()
RETAIL_INI = Path("~/Applications/pcsx2x6-retail/PCSX2x6/inis/PCSX2.ini").expanduser()
_PROC = "pcsx2x6"   # both members share the AppImage process (matched by path in proc_guard)


def _by_title(groups: list, title: str) -> dict:
    for g in groups:
        if g["title"] == title:
            return g
    raise KeyError(f"group {title!r} not found (shared descriptor set changed?)")


# ── fork-specific descriptor groups (everything else is shared read-only) ──────
def _fork_aud_groups() -> list:
    """The standard Audio groups with the volume key swapped OutputVolume ->
    StandardVolume (the forks are newer PCSX2 builds). New dicts only, so the shared
    ps.AUD_GROUPS is never mutated."""
    out = []
    for g in ps.AUD_GROUPS:
        items = [({**it, "key": "StandardVolume"} if it["key"] == "OutputVolume" else it)
                 for it in g["items"]]
        out.append({**g, "items": items})
    return out


# Texture Replacement tab (the standard GLOBAL gfx page omits it). All keys [EmuCore/GS],
# confirmed present in both fork inis.
_TEX_REPL = {"title": "Texture Replacement",
             "note": "Load/dump HD texture replacement packs for this emulator.",
             "items": [
                 ps._bool("LoadTextureReplacements", "Load Texture Replacements"),
                 ps._bool("LoadTextureReplacementsAsync", "Load Replacements Asynchronously"),
                 ps._bool("PrecacheTextureReplacements", "Precache Replacements"),
                 ps._bool("DumpReplaceableTextures", "Dump Replaceable Textures"),
                 ps._bool("DumpReplaceableMipmaps", "Dump Replaceable Mipmaps"),
                 ps._bool("DumpTexturesWithFMVActive", "Dump Textures During FMVs"),
             ]}

# Hardware Fixes tab (global): the UserHacks master enable + the per-game HW-fix items
# (reused read-only from pcsx2_pergame_cmds). HWDownloadMode stays per-game (its emulator
# widget is per-game only), so it is NOT offered globally.
_HW_FIXES = {"title": "Hardware Fixes",
             "note": "Turn on 'Manual Hardware Renderer Fixes' for the rest to take effect.",
             "items": ([ps._bool("UserHacks", "Enable Manual Hardware Renderer Fixes")]
                       + _by_title(pg._PG_ONLY["pcsx2gfx"], "Hardware Fixes")["items"])}
_UPSCALING = _by_title(pg._PG_ONLY["pcsx2gfx"], "Upscaling Fixes")

# The shared standard graphics groups, referenced by title (robust to reordering).
_RENDERER = _by_title(ps.GFX_GROUPS, "Renderer")
_DISPLAY = _by_title(ps.GFX_GROUPS, "Display")
_REND_HW = _by_title(ps.GFX_GROUPS, "Rendering (Hardware)")
_REND_SW = _by_title(ps.GFX_GROUPS, "Rendering (Software)")
_POST = _by_title(ps.GFX_GROUPS, "Post-Processing")
_CAPTURE = _by_title(ps.GFX_GROUPS, "Media Capture")
_ADV_GS = _by_title(ps.GFX_GROUPS, "Advanced (GS)")

# The ordered "Video" sub-pages (one per emulator Graphics tab). (suffix, title, [groups]).
VIDEO_TABS = [
    ("display", "Renderer & Display", [_RENDERER, _DISPLAY]),
    ("hw", "Rendering (Hardware)", [_REND_HW]),
    ("sw", "Rendering (Software)", [_REND_SW]),
    ("hwfix", "Hardware Fixes", [_HW_FIXES]),
    ("upscale", "Upscaling Fixes", [_UPSCALING]),
    ("texrepl", "Texture Replacement", [_TEX_REPL]),
    ("post", "Post-Processing", [_POST]),
    ("capture", "Media Capture", [_CAPTURE]),
    ("advgs", "Advanced (Graphics)", [_ADV_GS]),
]

# All GLOBAL pages for a member: (page suffix, title, [groups]). The Video tabs plus the
# standalone Emulation / OSD / Audio / Advanced pages.
GLOBAL_PAGES = ([(f"gfx_{suf}", title, groups) for suf, title, groups in VIDEO_TABS]
                + [("emu", "Emulation", ps.EMU_GROUPS),
                   ("osd", "On-Screen Display", ps.OSD_GROUPS),
                   ("aud", "Audio", _fork_aud_groups()),
                   ("adv", "Advanced", ps.ADV_GROUPS)])


# ── a member = one BufferedEngine over its ini + buffer ───────────────────────
class Member:
    def __init__(self, key: str, prefix: str, ini: Path, label: str):
        self.key = key
        self.prefix = prefix
        self.ini = ini            # attribute so tests can redirect to a temp copy
        self.label = label
        self.buf = pcsx2_engine.new_buf()
        self.running = lambda: proc_guard.emulator_running(_PROC)
        self.categories = {f"{prefix}_{suf}": (title, groups)
                           for suf, title, groups in GLOBAL_PAGES}

    def engine(self) -> pcsx2_engine.BufferedEngine:
        # Rebuilt per call from the CURRENT attributes so a test can swap self.ini /
        # self.running; the buffer persists across calls (self.buf).
        return pcsx2_engine.BufferedEngine(self.ini, self.running, self.categories,
                                           self.buf, note_label=self.label)

    def register(self) -> None:
        for ns in self.categories:
            self._register_ns(ns)

    def _register_ns(self, ns: str) -> None:
        @method(f"{ns}.get", slow=True)
        def _g(params, ns=ns):
            return self.engine().get(ns)

        @method(f"{ns}.set", slow=True)
        def _s(params, ns=ns):
            return self.engine().set(ns, params)

        @method(f"{ns}.save", slow=True)
        def _sv(params, ns=ns):
            return self.engine().save(ns)

        @method(f"{ns}.cancel", slow=True)
        def _c(params, ns=ns):
            return self.engine().cancel(ns)


ARCADE = Member("arcade", "x6a", ARCADE_INI, "Namco 246/256 (Arcade)")
RETAIL = Member("retail", "x6r", RETAIL_INI, "Namco 246/256 (Retail)")
MEMBERS = {"arcade": ARCADE, "retail": RETAIL}


# ── sections tree for standalones_cmds (the GLOBAL Graphics/Audio/Advanced part) ──
def _settings_row(ns: str, title: str, member_label: str, sublabel: str = "") -> dict:
    return {"label": title, "sublabel": sublabel, "kind": "settings", "arg": ns,
            "title": f"{member_label} — {title}"}


def graphics_group(m: Member, member_label: str) -> dict:
    """The 'Graphics' group row: Video (a further sub-menu of the tab pages),
    Emulation, On-Screen Display."""
    video = {"label": "Video", "sublabel": "",
             "kind": "group", "arg": "", "title": f"{member_label} — Video",
             "sections": [_settings_row(f"{m.prefix}_gfx_{suf}", title, member_label)
                          for suf, title, _g in VIDEO_TABS]}
    return {"label": "Graphics", "sublabel": "",
            "kind": "group", "arg": "", "title": f"{member_label} — Graphics",
            "sections": [
                video,
                _settings_row(f"{m.prefix}_emu", "Emulation", member_label, ""),
                _settings_row(f"{m.prefix}_osd", "On-Screen Display", member_label, ""),
            ]}


def audio_row(m: Member, member_label: str) -> dict:
    return _settings_row(f"{m.prefix}_aud", "Audio", member_label, "")


def advanced_row(m: Member, member_label: str) -> dict:
    return _settings_row(f"{m.prefix}_adv", "Advanced", member_label, "")


def register_all() -> None:
    for m in MEMBERS.values():
        m.register()


register_all()
