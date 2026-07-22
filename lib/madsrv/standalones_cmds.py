"""standalones.* methods — the Standalones hub's tile list.

The MAD "Standalones" page is the single home for every standalone emulator: each
tile opens that emulator's config. A tile can have one or more SECTIONS (e.g.
Dolphin = Settings + Controllers; Daphne = Button mapping + Controllers); the C++
opens a single-section tile directly and shows a small chooser for multi-section
tiles. Tiles are filtered to the systems present in ES-DE, with the system's
console.png as art (per user request).

Section `kind` tells the C++ which page to open (see madOpenStandaloneTarget):
  settings   -> GuiMadPageEmuSettings(arg = RPC namespace, title)
  gamepad    -> GuiMadPageBackendDetail(arg = backend name)
  model2     -> GuiMadPageModel2
  daphne_map -> GuiMadPageDaphne (button mapping)
"""
from __future__ import annotations

import glob
import re
import shutil
from pathlib import Path

from .. import es_gamelist, es_systems
from . import cfgutil
from . import mad_tree
from . import policy_settings_cmds
from .rpc import method
from .systems_cmds import console_art, resolve_art

# Curated standalone emulators (the user's list). `systems` = ES-DE system names
# implying the emulator is present (the tile shows only if one exists).
# `settings_ns` (optional) = the RPC namespace of its Settings page (added per
# emulator as Phase 3 lands; Dolphin is first). `backend` (optional) = its
# controller backend. `kind` (optional) = a special single-section tile.
STANDALONES = [
    {"key": "model2",     "label": "Sega Model 2",       "systems": ["model2"],
     "kind": "model2"},
    {"key": "supermodel", "label": "Sega Model 3",       "systems": ["model3"],
     "backend": "supermodel", "settings_ns": "model3"},
    # Dolphin drives BOTH Wii and GameCube (standalone). A bespoke grouped section tree
    # (_dolphin_sections): System / Video->Graphics(4 tabs) / Input{GameCube, Wii, Hotkeys} /
    # Audio. No settings_ns (that drives the DEFAULT single-"Settings" path the bespoke tree
    # bypasses); keeps `backend` for the Wii Controllers "gamepad" router page (arg="dolphin").
    {"key": "dolphin",    "label": "Wii / GameCube",     "systems": ["wii", "gc"],
     "backend": "dolphin"},
    # Wii U (Cemu): a bespoke grouped section tree (_cemu_sections) - General / Graphics group /
    # Audio / Graphic packs (dynamic) / Controllers (router profile-picker) / Per-game. No
    # settings_ns (that drives the DEFAULT single-"Settings" path _cemu_sections bypasses); keeps
    # `backend` for the Controllers "gamepad" backend-detail page (arg="cemu").
    {"key": "cemu",       "label": "Wii U",              "systems": ["wiiu"],
     "backend": "cemu"},
    # Switch is a GROUP: its tile opens a sub-grid of the Switch emulators
    # (Eden + Ryujinx + Citron). Members are defined in _EMUS below.
    {"key": "switch",     "label": "Switch",             "systems": ["switch"],
     "members": ["eden", "ryujinx", "citron"]},
    # PS3 global settings = the FULL RPCS3 tree split into 5 category sections (CPU / GPU /
    # Audio / Advanced / Emulator, built in _rpcs3_sections from rpcs3_settings.CATEGORIES),
    # NOT the old single curated "Settings" — so no settings_ns here (mirrors pcsx2).
    {"key": "rpcs3",      "label": "PlayStation 3",      "systems": ["ps3"],
     "backend": "rpcs3"},
    # PS2 global settings = the FULL PCSX2 tree split into 5 category sections (built
    # in _sections_for from pcsx2_settings.CATEGORIES), NOT the old single curated
    # "Settings" — so no settings_ns here.
    {"key": "pcsx2",      "label": "PlayStation 2",      "systems": ["ps2"],
     "backend": "pcsx2"},
    # pcsx2x6 = the Namco System 246/256 PCSX2 fork (Tekken, Soul Calibur, Time Crisis,
    # Vampire Night, …; both pad/stick and Sinden-lightgun games; portable ini).
    # Full standalone tile: Settings (+ a "Start Sinden guns" action button), Input
    # mapping, and Controllers (pads -> players). It has NO router `backend` key: pads
    # are bound at launch by switch_bind (mad-standalone-launch.py), so the Controllers
    # section is the pads-to-players assigner (via _PADS_MAP_EMUS), not the router page.
    {"key": "pcsx2x6",    "label": "Namco 246/256",      "systems": ["pcsx2x6"],
     "settings_ns": "pcsx2x6"},
    {"key": "xemu",       "label": "Xbox",               "systems": ["xbox"],
     "backend": "xemu"},
    {"key": "openbor",    "label": "OpenBOR",            "systems": ["openbor"],
     "backend": "openbor"},
    {"key": "daphne",     "label": "LaserDisc (Daphne)", "systems": ["daphne"],
     "kind": "daphne"},
    # Sega Lindbergh (lindbergh-loader): per-game Settings + a general input binder.
    # Strictly per-game config (each game owns its lindbergh.ini), so Settings is a
    # game picker; Input mapping is the Daphne-style live binder (lindbergh_map).
    {"key": "lindbergh",  "label": "Sega Lindbergh",     "systems": ["lindbergh"],
     "kind": "lindbergh"},
    # M.U.G.E.N (Ikemen GO / native): every game is a self-contained Ikemen GO
    # install with its own save/config.ini, so the tile is a GAME-FIRST per-game
    # config tree (Settings over that game's config.ini via mugen_cmds), plus the
    # X-Arcade warning toggle (injected by tile_flag_sections). Controllers (family
    # seat priority) + on-the-go land in later phases. Tile shows only when present.
    {"key": "mugen",      "label": "M.U.G.E.N",          "systems": ["mugen"],
     "backend": "mugen"},
]


# Group members (not top-level tiles): emulator definitions used to build a group
# tile's sub-grid. `icon` is a router-config/icons/*.png (themable), resolved via
# resolve_art (the icons/ chain) — NOT console_art (which only does <system>/
# console.png). Ryujinx/Citron have no `backend` (un-routed); their Controllers page is
# the pads→players assigner inside their bespoke section trees.
_EMUS = {
    # Eden (Yuzu fork): a bespoke section tree (_eden_sections) mirroring Eden's own Configure
    # dialog, so it needs no settings_ns/backend (those drive the DEFAULT single-"Settings" path
    # that _eden_sections bypasses).
    "eden":    {"key": "eden",    "label": "Eden",    "icon": "icons/eden.png"},
    # Ryujinx (Ryubing): a bespoke section tree (_ryujinx_sections) mirroring Ryujinx's own
    # Settings sidebar, so no settings_ns/backend — the tree supplies the granular pages plus
    # the pads→players Controllers row (the DEFAULT single-"Settings" path is bypassed).
    "ryujinx": {"key": "ryujinx", "label": "Ryujinx", "icon": "icons/ryujinx.png"},
    # Citron (Yuzu fork): a bespoke section tree (_citron_sections) mirroring Citron's
    # own Configure dialog, so it needs no settings_ns/backend (those drive the DEFAULT
    # single-"Settings" path that _citron_sections bypasses).
    "citron":  {"key": "citron",  "label": "Citron",  "icon": "icons/citron.png"},
}

# Emulators with a native per-button input-map page ({emu}.input_get/.input_set).
# Grows as the phased rollout lands; Model2 stays out (binary config, XInput-only).
# xemu: per-pad controller_mapping in xemu.toml (xemu >= v0.8.133).
# rpcs3: per-button Config in input_configs/global/Default.yml (Player N Input).
_INPUT_MAP_EMUS = {"pcsx2", "pcsx2x6", "xemu", "rpcs3"}   # citron/eden/ryujinx -> bespoke tree

# Emulators whose "Controllers → pads → players" section is the per-emulator
# device-assignment page (pads.get/.set → GuiMadPagePadsPriority), NOT the router
# backend detail page. The Switch emulators are configure-once / router-skip, so
# the router-backend "gamepad" page is inert for them — this writes the emulator's
# own config directly (arg = emulator key, not a router backend name).
_PADS_MAP_EMUS = {"pcsx2", "pcsx2x6", "xemu", "rpcs3"}   # citron/eden/ryujinx -> bespoke tree

# Switch-emulator install detection (drives the dynamic Switch tile). STRICT binary detection:
# an emulator counts as installed ONLY when its actual launchable binary is present -- the same
# thing ES-DE resolves for %EMULATOR_<X>% -- NOT when a leftover config dir exists. So deleting
# the binary drops the emu from the tile, and (re)installing it brings it back. Citron/Eden are
# AppImages: reuse the glob patterns from es_find_rules._RULES so detection stays in lockstep
# with the real find rules. Ryujinx has no custom rule (it uses ES-DE's BUNDLED RYUJINX find
# rule); mirror that rule here -- an AppImage, an EmuDeck publish/ build, a flatpak export, or a
# binary on $PATH.
_RYUJINX_BINARY_GLOBS = (
    "~/Applications/*yujinx*.AppImage",
    "~/.local/share/applications/*yujinx*.AppImage",
    "~/.local/bin/*yujinx*.AppImage",
    "~/bin/*yujinx*.AppImage",
    "~/Applications/publish/Ryujinx",
    "~/.local/share/applications/publish/Ryujinx",
    "~/.local/bin/publish/Ryujinx",
    "~/bin/publish/Ryujinx",
    "/var/lib/flatpak/exports/bin/io.github.ryubing.Ryujinx",
    "~/.local/share/flatpak/exports/bin/io.github.ryubing.Ryujinx",
    "/var/lib/flatpak/exports/bin/org.ryujinx.Ryujinx",
    "~/.local/share/flatpak/exports/bin/org.ryujinx.Ryujinx",
)
_RYUJINX_PATH_NAMES = ("Ryujinx", "Ryujinx.Ava", "ryujinx")  # systempath rule (binary on $PATH)


def _glob_any(patterns) -> bool:
    """True if any glob pattern (with ~ expanded) matches an existing path."""
    return any(glob.glob(str(Path(p).expanduser())) for p in patterns)


def _emu_installed(emu: str) -> bool:
    """True only if the emulator's launchable binary is present (strict). Mirrors how ES-DE
    resolves %EMULATOR_<X>%; a leftover config no longer counts. Unknown members (not one of the
    three Switch emus) are treated as installed so a future member is never silently hidden."""
    if emu in ("citron", "eden"):
        from .. import es_find_rules
        return _glob_any(dict(es_find_rules._RULES).get(emu.upper(), ()))
    if emu == "ryujinx":
        return _glob_any(_RYUJINX_BINARY_GLOBS) or any(
            shutil.which(n) for n in _RYUJINX_PATH_NAMES)
    return True


def _pcsx2x6_has_guncon2() -> bool:
    """True if either pcsx2x6 USB port is the lightgun (guncon2) device — gates the
    Lightgun section on the Namco 246/256 tile (the controller-type picker sets it)."""
    ini = Path("~/Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini").expanduser()
    try:
        text = ini.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any((cfgutil.ini_read(text, sec, "Type") or "").strip() == "guncon2"
               for sec in ("USB1", "USB2"))


def _pcsx2x6_has_guncon2_retail() -> bool:
    """True if the retail -datapath ini has a USB port set to guncon2-retail — gates the
    'PS2 Light Gun (GunCon 2)' tile's sections (i.e. the retail setup is installed)."""
    ini = Path("~/Applications/pcsx2x6-retail/PCSX2x6/inis/PCSX2.ini").expanduser()
    try:
        text = ini.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any((cfgutil.ini_read(text, sec, "Type") or "").strip() == "guncon2-retail"
               for sec in ("USB1", "USB2"))


# ── PlayStation 2 tile = a grouped sub-grid (Switch-style members): Graphics / Input /
#    Audio / Per-game. Each member opens its own section chooser; Audio has a single
#    section so it opens the Audio page directly. Python-only grouping — no C++ change. ──
_PCSX2_CAT_SUB = {"pcsx2emu": "",
                  "pcsx2gfx": "",
                  "pcsx2osd": "",
                  "pcsx2aud": "",
                  "pcsx2adv": ""}


def _pcsx2_cat_section(ns: str) -> dict:
    from . import pcsx2_settings
    title = pcsx2_settings.CATEGORIES[ns][0]
    return {"label": title, "sublabel": _PCSX2_CAT_SUB.get(ns, ""),
            "kind": "settings", "arg": ns, "title": mad_tree.title("PlayStation 2", title)}


def _pcsx2_sections(s: dict) -> list[dict]:
    """The top-level PS2 rows for the tile's chooser (a NESTED MENU, not tiles), in the
    canonical Switch-emu shape (mad_tree.section_order): System / Video / Audio / Input /
    Per-game. Each PCSX2 category page keeps its own name as a sub-row; only the top-level
    bucketing is canonical -- Emulation + Advanced -> System; Graphics + On-Screen Display ->
    Video; Audio opens directly. A GROUP row = {label, sublabel, kind:"group", title,
    sections:[...sub rows...]}; the C++ chooser pushes a sub-chooser when kind=="group"."""
    label = s["label"]

    def grp(lbl, subs):
        return {"label": lbl, "sublabel": "", "kind": "group", "arg": "",
                "title": mad_tree.title(label, lbl), "sections": subs}

    system = [_pcsx2_cat_section(ns) for ns in ("pcsx2emu", "pcsx2adv")]
    video = [_pcsx2_cat_section(ns) for ns in ("pcsx2gfx", "pcsx2osd")]
    inp = [
        {"label": "Device visibility", "sublabel": "",
         "kind": "pads_hide", "arg": "pcsx2", "title": label + " - Device visibility"},
        {"label": "Mappings", "sublabel": "",
         "kind": "input_map", "arg": "pcsx2", "title": label + " - Mappings"},
        {"label": "Pads to players", "sublabel": "",
         "kind": "pads_map", "arg": "pcsx2", "title": label + " - Pads to players"},
        {"label": "Hotkeys", "sublabel": "",
         "kind": "input_map", "arg": "pcsx2hk", "title": label + " - Hotkeys"},
    ]
    # NOTE: the retail GunCon2 page moved to Namco 246/256 -> Retail -> Input (it is a
    # pcsx2x6-fork setup, not standard PCSX2), see _pcsx2x6_retail_input.
    # Per-game is GAME-FIRST (standing rule mad-pergame-game-first): ONE "Per-game" row -> pick a game
    # ONCE -> a sub-menu [Settings, Input->[Controllers, Mappings]], all editing the picked title. Same
    # settings_pergame_menu pattern as _eden_pergame_row / _cemu_pergame_row; the browser injects the
    # picked titleid into every leaf (two levels deep, so the Input group's children get it too). Row
    # arg=pcsx2pg drives the picker's game list (pcsx2pg.games == pcsx2pgin.games, identical PS2 titles).
    pergame_leaves = [
        {"label": "Settings", "sublabel": "",
         "kind": "pergame_settings", "arg": "pcsx2pg", "title": label + " - Settings"},
        {"label": "Input", "sublabel": "", "kind": "group", "arg": "",
         "title": label + " - Input", "sections": [
            {"label": "Controllers", "sublabel": "",
             "kind": "pergame_pads", "arg": "pcsx2pgin", "title": label + " - Controllers"},
            {"label": "Mappings", "sublabel": "",
             "kind": "pergame_input", "arg": "pcsx2pgin", "title": label + " - Mappings"},
         ]},
    ]
    return mad_tree.section_order(
        system=grp(mad_tree.L.SYSTEM, system),
        video=grp(mad_tree.L.VIDEO, video),
        audio=_pcsx2_cat_section("pcsx2aud"),   # Audio: opens the Audio page directly
        inp=grp(mad_tree.L.INPUT, inp),
        pergame=mad_tree.pergame_menu(label, "pcsx2pg", pergame_leaves),
    )


# ── PlayStation 3 (RPCS3) tile = a PCSX2-style grouped sub-menu: Input group + Settings
#    group (CPU / GPU / Audio / Advanced / Emulator category pages). Python-only grouping —
#    no C++ change; reuses the compiled GuiMadPageEmuSettings via kind:"settings". The
#    Per-game group is added in a later phase. ──
def _rpcs3_cat_section(ns: str) -> dict:
    from . import rpcs3_settings
    title = rpcs3_settings.CATEGORIES[ns][0]
    return {"label": title, "sublabel": "", "kind": "settings", "arg": ns,
            "title": mad_tree.title("PlayStation 3", title)}


def _rpcs3_sections(s: dict) -> list[dict]:
    """The PS3 tile's top-level rows, in the canonical Switch-emu shape (mad_tree.section_order):
    System / Video / Audio / Input / Per-game. Each RPCS3 category page keeps its own name as a
    sub-row; only the top-level bucketing is canonical -- CPU + Advanced + Emulator -> System;
    GPU -> Video (its single child, so it opens the GPU page directly); Audio opens directly. The
    Input group (Device visibility / Mappings / Pads to players) is unchanged. A GROUP row =
    {label, kind:"group", title, sections:[...]}; the C++ chooser pushes a sub-chooser when
    kind=="group"."""
    label = s["label"]

    def grp(lbl, subs):
        return {"label": lbl, "sublabel": "", "kind": "group", "arg": "",
                "title": mad_tree.title(label, lbl), "sections": subs}
    inp = [
        {"label": "Device visibility", "sublabel": "",
         "kind": "pads_hide", "arg": "rpcs3", "title": label + " - Device visibility"},
        {"label": "Mappings", "sublabel": "",
         "kind": "input_map", "arg": "rpcs3", "title": label + " - Mappings"},
        {"label": "Pads to players", "sublabel": "",
         "kind": "pads_map", "arg": "rpcs3", "title": label + " - Pads to players"},
    ]
    system = [_rpcs3_cat_section(ns) for ns in ("rpcs3cpu", "rpcs3adv", "rpcs3emu")]
    video = [_rpcs3_cat_section("rpcs3gpu")]   # single child -> _collapse_singletons opens GPU directly
    # Per-game is GAME-FIRST (standing rule mad-pergame-game-first): ONE "Per-game" row -> pick a
    # game ONCE -> its per-game pages (Settings, Mappings, Manage patches), all editing the picked
    # title. Same settings_pergame_menu pattern as PCSX2 / Eden. "Manage patches" is a game-scoped
    # settings page (arg=rpcs3patch) over RPCS3's patch.yml DB -> patch_config.yml.
    pergame_leaves = [
        {"label": "Settings", "sublabel": "",
         "kind": "pergame_settings", "arg": "rpcs3pg", "title": label + " - Settings"},
        {"label": "Mappings", "sublabel": "",
         "kind": "pergame_input", "arg": "rpcs3pgin", "title": label + " - Mappings"},
        {"label": "Manage patches", "sublabel": "",
         "kind": "pergame_settings", "arg": "rpcs3patch", "title": label + " - Manage patches"},
    ]
    return mad_tree.section_order(
        system=grp(mad_tree.L.SYSTEM, system),
        video=grp(mad_tree.L.VIDEO, video),
        audio=_rpcs3_cat_section("rpcs3aud"),   # Audio: opens the Audio page directly
        inp=grp(mad_tree.L.INPUT, inp),
        pergame=mad_tree.pergame_menu(label, "rpcs3pg", pergame_leaves),
    )


# ── Namco 246/256 (pcsx2x6) = a GROUP tile: Arcade + Retail members, each a full
#    settings tree (Graphics{Video tabs, Emulation, OSD} / Input / Audio / Advanced).
#    The settings pages come from pcsx2_fork_settings (one BufferedEngine per member,
#    x6a_* / x6r_* namespaces); the Input group reuses the existing input pages. ──
def _pcsx2x6_arcade_input(label: str) -> dict:
    """Arcade Input group mirroring pcsx2x6's own Controller Settings sidebar (Global settings,
    Controller Port 1/2, USB Port 1/2, JVS controls, Hotkeys), PLUS the MAD-specific pages that
    aren't in PCSX2's sidebar but must be preserved: 'Pads -> players' (device -> player assignment,
    drives the launch bind) and 'Lightgun' (crosshair/Sinden, when a USB port is a gun)."""
    def leaf(lbl, sub, kind, arg, title=None):
        return {"label": lbl, "sublabel": sub, "kind": kind, "arg": arg,
                "title": title or mad_tree.title(label, lbl)}

    leaves = [
        leaf("Global", "", "settings", "x6a_global"),
        leaf("Pads to players", "", "pads_map", "pcsx2x6",
             f"{label} - Controllers"),
        leaf("Controller Port 1", "", "input_map", "x6a_pad1"),
        leaf("Controller Port 2", "", "input_map", "x6a_pad2"),
        leaf("USB Port 1", "GunCon2 or mouse", "input_map", "x6a_usb1"),
        leaf("USB Port 2", "GunCon2 or mouse", "input_map", "x6a_usb2"),
        leaf("JVS controls", "", "settings", "pcsx2x6_jvs"),
        leaf("Hotkeys", "", "input_map", "x6a_hk"),
    ]
    if _pcsx2x6_has_guncon2():
        leaves.append(leaf("Lightgun", "", "settings", "pcsx2x6_lightgun"))
    return {"label": "Input", "sublabel": "", "kind": "group",
            "arg": "", "title": f"{label} - Input", "sections": leaves}


def _pcsx2x6_retail_input(label: str) -> dict:
    """Retail is lightgun-only, so its Input group is gun-focused: Global settings, the two
    GunCon2 gun USB ports, and Hotkeys. Each USB port is a single-gun view of the shipped
    guncon2_retail page (binds + per-gun crosshair + the Sinden toggle). The DualShock2
    [Pad1]/[Pad2] are still bound at launch by switch_bind (ps2guncon) but a gun setup drives
    movement from the gun, so no Controller Port leaves; no Pads -> players (guns sit on FIXED
    USB ports); no JVS (retail is PS2 discs, not Namco arcade)."""
    def leaf(lbl, sub, kind, arg, title=None):
        return {"label": lbl, "sublabel": sub, "kind": kind, "arg": arg,
                "title": title or mad_tree.title(label, lbl)}

    leaves = [
        leaf("Global", "", "settings", "x6r_global"),
        leaf("Gun 1 (USB Port 1)", "", "input_map", "x6r_usb1",
             "PS2 GunCon 2 - Gun 1"),
        leaf("Gun 2 (USB Port 2)", "", "input_map", "x6r_usb2",
             "PS2 GunCon 2 - Gun 2"),
        leaf("Hotkeys", "", "input_map", "x6r_hk"),
    ]
    return {"label": "Input", "sublabel": "", "kind": "group",
            "arg": "", "title": f"{label} - Input", "sections": leaves}


def _pcsx2x6_member_sections(member, label: str, retail: bool) -> list[dict]:
    """Canonical Switch-emu shape (mad_tree.section_order): System / Video / Audio / Input.
    pcsx2x6's arcade/retail setups are fixed, so there is legitimately no Per-game row. Each
    fork settings page keeps its own name as a sub-row; only the top-level bucketing is
    canonical -- Emulation + Advanced -> System; the graphics tab pages + On-Screen Display ->
    Video (the old 'Graphics' umbrella and its redundant Video > Video nesting are gone);
    Audio opens directly; the arcade/retail Input group is unchanged."""
    from . import pcsx2_fork_settings as fs
    inp = _pcsx2x6_retail_input(label) if retail else _pcsx2x6_arcade_input(label)

    def grp(lbl, subs):
        return {"label": lbl, "sublabel": "", "kind": "group", "arg": "",
                "title": mad_tree.title(label, lbl), "sections": subs}

    return mad_tree.section_order(
        system=grp(mad_tree.L.SYSTEM, fs.system_rows(member, label)),
        video=grp(mad_tree.L.VIDEO, fs.video_rows(member, label)),
        audio=fs.audio_row(member, label),
        inp=inp,
    )


def _pcsx2x6_members(art: str, arcade_present: bool = True) -> list[dict]:
    """The member tiles: Arcade (when the Namco arcade system has games) and Retail (when the
    -datapath GunCon2 setup is installed). Gated independently so a retail-only setup still
    surfaces the Retail tree."""
    from . import pcsx2_fork_settings as fs

    def tile(member, short: str, label: str, retail: bool) -> dict:
        return {"key": f"pcsx2x6_{member.key}", "label": short, "sublabel": "",
                "art": [art] if art else [],
                "sections": _pcsx2x6_member_sections(member, label, retail)}

    members = []
    if arcade_present:
        members.append(tile(fs.ARCADE, "Arcade", "Namco 246/256 (Arcade)", False))
    if _pcsx2x6_has_guncon2_retail():
        members.append(tile(fs.RETAIL, "Retail", "Namco 246/256 (Retail)", True))
    return members


# ── Citron (Switch, Yuzu fork) = a Switch group MEMBER with a bespoke section tree.
#    Citron's own Configure dialog pages (General / System / CPU / Graphics / Graphics Adv /
#    Audio / Input / Hotkeys) PLUS MAD's own "pads -> players", a launch-time docked-mode
#    auto-detect toggle, and the game-first per-game tree, GROUPED into five top-level rows
#    (System / Video / Input / Audio / Per-game) via kind:"group" sub-choosers -- the same
#    nested-group pattern _pcsx2_sections uses (the C++ chooser recurses on a group row). The
#    settings pages come from citron_settings (citron_* namespaces). This grouping is the
#    canonical Switch-emu section layout (memory switch-emu-menu-scheme) so Eden/Ryujinx reach
#    menu parity when their granular trees get built. ──
def _citron_pergame_row(label: str) -> dict:
    """The game-first per-game tree: pick a game -> a GROUPED sub-menu of its overrides,
    mirroring the top-level layout (memory switch-emu-menu-scheme): System{System,CPU,Linux} /
    Video{Graphics,Adv. Graphics} / Audio / Input / Add-Ons / Cheats -- each leaf carrying the
    picked title id. Rendered by the fork media+info browser (GuiMadPagePergameBrowser)
    `settingsmenu` target: it opens `citron.games`, then on pick injects the titleid into each
    leaf AND (recursively) each group's nested leaves before pushing a section chooser, so a
    page inside System/Video opens for the picked game. Single-page rows open directly; only
    System/Video are sub-choosers."""
    def leaf(lbl, sub, arg):
        return {"label": lbl, "sublabel": sub, "kind": "pergame_settings", "arg": arg,
                "title": f"Citron per-game - {lbl}"}

    def group(lbl, sub, subs):
        # Opens a sub-chooser; the browser injects the picked titleid into `subs` on pick.
        return {"label": lbl, "sublabel": sub, "kind": "group", "arg": "",
                "title": f"Citron per-game - {lbl}", "sections": subs}

    system = [
        leaf("System", "", "citron_pg_system"),
        leaf("CPU", "", "citron_pg_cpu"),
        leaf("GameMode", "", "citron_pg_linux"),
    ]
    video = [
        leaf("Graphics", "", "citron_pg_gfx"),
        leaf("Adv. Graphics", "", "citron_pg_gfxadv"),
    ]
    leaves = [
        group("System", "", system),
        group("Video", "", video),
        leaf("Audio", "", "citron_pg_audio"),
        leaf("Input", "", "citron_pg_input"),
        # key= lets citron.games hide these tiles for a game with no add-ons / cheats (empty page).
        {**leaf("Add-Ons", "", "citron_addons"), "key": "addons"},
        {**leaf("Cheats", "", "citron_cheats"), "key": "cheats"},
    ]
    return mad_tree.pergame_menu(label, "citron", leaves)


def _citron_sections(s: dict) -> list[dict]:
    label = s["label"]

    def row(lbl, sub, kind, arg, title=None):
        return {"label": lbl, "sublabel": sub, "kind": kind, "arg": arg,
                "title": title or mad_tree.title(label, lbl)}

    def group(lbl, sub, subs):
        # A group row opens a sub-chooser of `subs` (C++ recurses on kind:"group").
        return {"label": lbl, "sublabel": sub, "kind": "group", "arg": "",
                "title": mad_tree.title(label, lbl), "sections": subs}

    # Five top-level rows (canonical Switch-emu layout). Leaf rows are the former flat pages,
    # unchanged; only their nesting differs -- so every page opens exactly as before.
    system = [
        row("General", "", "settings", "citron_general"),
        row("CPU", "", "settings", "citron_cpu"),
        row("System", "", "settings", "citron_system"),
        row("Dock detection", "", "settings", "citron_dock"),
    ]
    video = [
        row("Graphics", "", "settings", "citron_gfx"),
        row("Adv. Graphics", "", "settings", "citron_gfxadv"),
    ]
    inp = [
        row("Controllers", "", "pads_map", "citron"),
        row("Input mapping", "", "input_map", "citron"),
        row("Hotkeys", "", "input_map", "citron_hk"),
    ]
    # Canonical Switch-emu order (mad_tree.section_order): System, Video, Audio, Input,
    # Per-game -- Audio precedes Input, matching this emu's own per-game tree and Cemu.
    return mad_tree.section_order(
        system=group("System", "", system),
        video=group("Video", "", video),
        audio=row("Audio", "", "settings", "citron_audio"),
        inp=group("Input", "", inp),
        pergame=_citron_pergame_row(label),
    )


def _eden_pergame_row(label: str) -> dict:
    """The game-first per-game tree for Eden: pick a game -> a GROUPED sub-menu of its overrides,
    mirroring the top-level layout (memory switch-emu-menu-scheme): System{System,CPU,Linux} /
    Video{Graphics,Adv. Graphics,GPU extensions} / Audio / Input / Add-Ons / Cheats -- each leaf
    carrying the picked title id. Rendered by the fork media+info browser (GuiMadPagePergameBrowser)
    `settingsmenu` target: it opens `eden.games`, then on pick injects the titleid into each leaf AND
    (recursively) each group's nested leaves before pushing a section chooser. Single-page rows open
    directly; only System/Video are sub-choosers."""
    def leaf(lbl, sub, arg):
        return {"label": lbl, "sublabel": sub, "kind": "pergame_settings", "arg": arg,
                "title": f"Eden per-game - {lbl}"}

    def group(lbl, sub, subs):
        return {"label": lbl, "sublabel": sub, "kind": "group", "arg": "",
                "title": f"Eden per-game - {lbl}", "sections": subs}

    system = [
        leaf("System", "", "eden_pg_system"),
        leaf("CPU", "", "eden_pg_cpu"),
        leaf("GameMode", "", "eden_pg_linux"),
    ]
    video = [
        leaf("Graphics", "", "eden_pg_gfx"),
        leaf("Adv. Graphics", "", "eden_pg_gfxadv"),
        leaf("GPU extensions", "", "eden_pg_gfxext"),
    ]
    leaves = [
        group("System", "", system),
        group("Video", "", video),
        leaf("Audio", "", "eden_pg_audio"),
        leaf("Input", "", "eden_pg_input"),
        # key= lets eden.games hide these tiles for a game with no add-ons / cheats (empty page).
        {**leaf("Add-Ons", "", "eden_addons"), "key": "addons"},
        {**leaf("Cheats", "", "eden_cheats"), "key": "cheats"},
    ]
    return mad_tree.pergame_menu(label, "eden", leaves)


def _eden_sections(s: dict) -> list[dict]:
    label = s["label"]

    def row(lbl, sub, kind, arg, title=None):
        return {"label": lbl, "sublabel": sub, "kind": kind, "arg": arg,
                "title": title or mad_tree.title(label, lbl)}

    def group(lbl, sub, subs):
        # A group row opens a sub-chooser of `subs` (C++ recurses on kind:"group").
        return {"label": lbl, "sublabel": sub, "kind": "group", "arg": "",
                "title": mad_tree.title(label, lbl), "sections": subs}

    # Five top-level rows (canonical Switch-emu layout, memory switch-emu-menu-scheme). Leaf rows
    # are the former flat pages, relocated verbatim (same kind+arg); only their nesting differs.
    system = [
        row("General", "", "settings", "eden_general"),
        row("CPU", "", "settings", "eden_cpu"),
        row("System", "", "settings", "eden_system"),
        row("Dock detection", "", "settings", "eden_dock"),
    ]
    video = [
        row("Graphics", "", "settings", "eden_gfx"),
        row("Adv. Graphics", "", "settings", "eden_gfxadv"),
        row("GPU extensions", "", "settings", "eden_gfxext"),
    ]
    inp = [
        row("Controllers", "", "pads_map", "eden"),
        row("Input mapping", "", "input_map", "eden"),
        row("Hotkeys", "", "input_map", "eden_hk"),
    ]
    # Canonical Switch-emu order (mad_tree.section_order): System, Video, Audio, Input,
    # Per-game -- Audio precedes Input, matching this emu's own per-game tree and Cemu.
    return mad_tree.section_order(
        system=group("System", "", system),
        video=group("Video", "", video),
        audio=row("Audio", "", "settings", "eden_audio"),
        inp=group("Input", "", inp),
        pergame=_eden_pergame_row(label),
    )


# ── Ryujinx (Switch, Ryubing) = a Switch group MEMBER with a bespoke section tree, mirroring
#    Ryujinx's own Settings sidebar (System / CPU / Graphics / Audio / Input / Hotkeys) PLUS MAD's
#    "pads -> players" and a launch-time docked-mode auto-detect toggle, GROUPED into the canonical
#    five Switch-emu rows (System / Video / Input / Audio / Per-game) via kind:"group" sub-choosers
#    -- the same nested pattern Citron/PCSX2 use (the C++ chooser recurses on a group row, so this is
#    pure Python, no fork rebuild). Settings pages come from ryujinx_settings (ryujinx_* namespaces);
#    Hotkeys is a bespoke settings page over the nested Config.json `hotkeys` object. ──
def _ryujinx_pergame_row(label: str) -> dict:
    """The game-first per-game tree: pick a game -> a grouped sub-menu of its overrides, mirroring
    the top-level layout: System{System,CPU} / Video{Graphics,Adv} / Audio / Add-Ons / Cheats --
    each leaf carrying the picked title id (injected by GuiMadPagePergameBrowser's settingsmenu
    target). There is intentionally NO per-game Input row: a Ryujinx profile is a device+mapping PIN
    that neither our bake nor the launch router honored cleanly; device -> player is owned by the
    global Controllers -> pads -> players routing. Settings pages come from ryujinx_pergame; Add-Ons /
    Cheats from ryujinx_addons_cmds / ryujinx_cheats_cmds."""
    def leaf(lbl, sub, arg):
        return {"label": lbl, "sublabel": sub, "kind": "pergame_settings", "arg": arg,
                "title": f"Ryujinx per-game - {lbl}"}

    def group(lbl, sub, subs):
        return {"label": lbl, "sublabel": sub, "kind": "group", "arg": "",
                "title": f"Ryujinx per-game - {lbl}", "sections": subs}

    system = [
        leaf("System", "", "ryujinx_pg_system"),
        leaf("CPU", "", "ryujinx_pg_cpu"),
    ]
    video = [
        leaf("Graphics", "", "ryujinx_pg_gfx"),
        leaf("Adv. Graphics", "", "ryujinx_pg_gfxadv"),
    ]
    leaves = [
        group("System", "", system),
        group("Video", "", video),
        leaf("Audio", "", "ryujinx_pg_audio"),
        # key= lets ryujinx.games hide these tiles for a game with no add-ons / cheats (empty page).
        {**leaf("Add-Ons", "", "ryujinx_addons"), "key": "addons"},
        {**leaf("Cheats", "", "ryujinx_cheats"), "key": "cheats"},
    ]
    return mad_tree.pergame_menu(label, "ryujinx", leaves)


def _ryujinx_sections(s: dict) -> list[dict]:
    label = s["label"]

    def row(lbl, sub, kind, arg, title=None):
        return {"label": lbl, "sublabel": sub, "kind": kind, "arg": arg,
                "title": title or mad_tree.title(label, lbl)}

    def group(lbl, sub, subs):
        return {"label": lbl, "sublabel": sub, "kind": "group", "arg": "",
                "title": mad_tree.title(label, lbl), "sections": subs}

    system = [
        row("System", "", "settings", "ryujinx_system"),
        row("CPU", "", "settings", "ryujinx_cpu"),
        row("Dock detection", "", "settings", "ryujinx_dock"),
    ]
    video = [
        row("Graphics", "", "settings", "ryujinx_gfx"),
        row("Adv. Graphics", "", "settings", "ryujinx_gfxadv"),
    ]
    inp = [
        row("Controllers", "", "pads_map", "ryujinx"),
        row("Input mapping", "", "input_map", "ryujinx"),
        row("Hotkeys", "", "settings", "ryujinx_hk"),
    ]
    # Canonical Switch-emu order (mad_tree.section_order): System, Video, Audio, Input,
    # Per-game -- Audio precedes Input, matching this emu's own per-game tree and Cemu.
    return mad_tree.section_order(
        system=group("System", "", system),
        video=group("Video", "", video),
        audio=row("Audio", "", "settings", "ryujinx_audio"),
        inp=group("Input", "", inp),
        pergame=_ryujinx_pergame_row(label),
    )


# ── Cemu (Wii U) = a normal top-level tile with a bespoke grouped section tree, mirroring the
#    Switch-emu five-row scheme WHERE IT APPLIES: General / Graphics (group: Graphics + Overlay +
#    Notifications) / Audio / Graphic packs (a DYNAMIC page listing only installed games' packs) /
#    Controllers (the EXISTING router profile-picker, kept verbatim) / Per-game. Cemu has no global
#    CPU/dock settings (cpuMode is per-game only), so General/Audio are bare leaves like Citron's
#    Audio. Settings pages come from cemu_settings (cemu_* namespaces); packs from cemu_packs_cmds;
#    per-game from cemu_pergame / cemu_pg_input_cmds. Pure Python - no fork rebuild (reuses the
#    settings / group / gamepad / settings_pergame_menu / pergame_settings kinds). ──
def _cemu_pergame_row(label: str) -> dict:
    """Game-first per-game tree: pick a game -> General / Graphics / Controller / Graphic packs, each
    carrying the picked title id (injected by GuiMadPagePergameBrowser). Graphic packs is a kind:"group"
    of Cemu's category sub-pages (Enhancements / Graphics / Mods / Workarounds / Cheats / Other), each a
    BUFFERED page of that category's packs (enable + per-option pickers) (cemu_packs_<category>). Every
    category row carries a stable `key` so
    the rebuilt browser can hide empty ones per game (cemu.games -> per-game `hide`); older AppImages
    ignore `key`/`hide` and just show them all. Two levels deep = works with the existing fork."""
    from . import cemu_packs_cmds as cp

    def leaf(lbl, sub, arg, key=""):
        row = {"label": lbl, "sublabel": sub, "kind": "pergame_settings", "arg": arg,
               "title": f"Wii U per-game - {lbl}"}
        if key:
            row["key"] = key
        return row

    packs = {"label": "Graphic packs", "sublabel": "", "kind": "group",
             "arg": "", "key": "packs", "title": "Wii U per-game - Graphic packs",
             "sections": [leaf(cat, "", f"cemu_packs_{cp.catkey(cat)}",
                               f"packs_{cp.catkey(cat)}") for cat in cp.CATEGORIES]}
    leaves = [
        leaf("General", "", "cemu_pg_general"),
        leaf("Graphics", "", "cemu_pg_gfx"),
        leaf("Controller", "", "cemu_pg_input"),
        packs,
    ]
    return mad_tree.pergame_menu(label, "cemu", leaves)


def _cemu_sections(s: dict) -> list[dict]:
    label = s["label"]

    def row(lbl, sub, kind, arg, title=None):
        return {"label": lbl, "sublabel": sub, "kind": kind, "arg": arg,
                "title": title or mad_tree.title(label, lbl)}

    def group(lbl, sub, subs):
        return {"label": lbl, "sublabel": sub, "kind": "group", "arg": "",
                "title": mad_tree.title(label, lbl), "sections": subs}

    graphics = [
        row("Graphics", "", "settings", "cemu_gfx"),
        row("Overlay", "", "settings", "cemu_overlay"),
        row("Notifications", "", "settings", "cemu_notif"),
    ]
    # Graphic packs are inherently per-game, so they live ONLY under Per-game (pick a game -> its
    # packs), NOT as a top-level row -- a global "all games" packs page just duplicated that.
    return [
        row("General", "", "settings", "cemu_general"),
        group("Graphics", "", graphics),
        row("Audio", "", "settings", "cemu_audio"),
        # Input = family x context assignment (the layout follows the controller, not the slot) PLUS the
        # cemu-global "Profiles folder" (config_dir) + X-Arcade warn, relocated here when the vestigial
        # "Controllers" backend-detail row was removed (peers have ONE Input node; cemu was the outlier).
        # The docked door; the handheld door lives under On-the-go -> Wii U -> Input.
        row("Input", "", "settings", "cemu_input_docked"),
        _cemu_pergame_row(label),
    ]


def _collapse_singletons(rows: list[dict]) -> list[dict]:
    """STANDING RULE (memory mad-collapse-single-child-groups): a group with exactly ONE
    child opens that child directly -- no redundant one-item submenu. Keeps the parent
    group's label/sublabel/title but adopts the child's kind/arg/sections. Recursive, so it
    also collapses nested singletons; auto-reverts to a real submenu when a group has >=2
    children."""
    out = []
    for r in rows:
        if r.get("kind") == "group" and isinstance(r.get("sections"), list):
            subs = _collapse_singletons(r["sections"])
            if len(subs) == 1:
                child = dict(subs[0])
                child["label"] = r.get("label", child.get("label"))
                child["sublabel"] = r.get("sublabel", child.get("sublabel", ""))
                child["title"] = r.get("title", child.get("title"))
                out.append(child)
            else:
                r = dict(r)
                r["sections"] = subs
                out.append(r)
        else:
            out.append(r)
    return out


def _dolphin_sections(s: dict, syss: list[str] | None = None) -> list[dict]:
    """Dolphin (GameCube + Wii) grouped section tree, mirroring _citron_sections.
    System / Video(->4 graphics tabs) / Input{GameCube, Wii, Hotkeys} / Audio.

    Controller-policy flag leaves are gated on the tile's PRESENT systems (`syss`) and on
    SYSFLAGS membership (tile_flag_sections filters both): the wii "Controller options"
    (DolphinBar / Sinden / hands-off) goes under Wii; the gc "X-Arcade warning" under GameCube
    (gc is standalone-launched now, so it is in SYSFLAGS and no longer reachable via RetroArch).
    Because both flag leaves are embedded here, standalones.list SKIPS the central
    tile_flag_sections append for dolphin. Single-child groups are collapsed by
    _collapse_singletons in _sections_for."""
    label = s["label"]
    if syss is None:
        syss = list(s.get("systems", []))

    def row(lbl, sub, kind, arg, title=None):
        return {"label": lbl, "sublabel": sub, "kind": kind, "arg": arg,
                "title": title or mad_tree.title(label, lbl)}

    def group(lbl, sub, subs):
        return {"label": lbl, "sublabel": sub, "kind": "group", "arg": "",
                "title": mad_tree.title(label, lbl), "sections": subs}

    def flags(sysname):   # the system's controller-policy flag leaf (or [] if not flagged)
        return policy_settings_cmds.tile_flag_sections([sysname] if sysname in syss else [], label)

    system = [
        row("General", "", "settings", "dolphin_general"),
        row("GameCube", "", "settings", "dolphin_gc"),
        row("Wii", "", "settings", "dolphin_wii"),
        row("Advanced", "", "settings", "dolphin_advanced"),
    ]
    graphics = [
        row("General", "", "settings", "dolphin_gfx_general"),
        row("Enhancements", "", "settings", "dolphin_gfx_enh"),
        row("Hacks", "", "settings", "dolphin_gfx_hacks"),
        row("Advanced", "", "settings", "dolphin_gfx_adv"),
    ]
    video = [group("Graphics", "", graphics)]

    gc_ctrl = [
        row("Button mapping", "", "input_map", "dolphin"),
        row("Pads to players", "", "pads_map", "dolphin_gc"),
        row("Dock / handheld", "", "settings", "dolphin_gc_dock"),
    ]
    # gc's lone X-Arcade warn now rides the Pads-to-players page (dolphin_gc pads.get -> `warn`),
    # NOT an inline chip here -- so the 3 clean leaves gridify GameCube into a tile grid.
    wii_ctrl = [row("Button mapping", "", "input_map", "dolphin_wii"),
                row("Wii Remotes to players", "", "gamepad", s.get("backend", "dolphin")),
                row("Classic controller order", "", "pads_map", "dolphin_wii")]
    wii_ctrl += flags("wii")
    inp = [
        group("GameCube", "", gc_ctrl),
        group("Wii", "", wii_ctrl),
        row("Hotkeys", "", "input_map", "dolphin_hk"),
    ]

    # Per-game overrides (both GameCube + Wii run standalone Dolphin, same GameSettings/<ID>.ini
    # mechanism): separate GameCube / Wii browsers, each opening the same per-game sub-menu (General
    # / Graphics{4 tabs} / AR codes / Gecko codes). The code leaves carry a `key` so the browser can
    # hide them per game (dolphinpg_*.games -> per-game `hide`) when the game has no such codes.
    def pg_leaf(lbl, sub, arg, key=""):
        d = {"label": lbl, "sublabel": sub, "kind": "pergame_settings", "arg": arg,
             "title": f"{label} - {lbl}"}
        if key:
            d["key"] = key
        return d

    def pergame_menu(lbl, games_ns):
        subs = [
            pg_leaf("General", "", "dolphin_pg_general"),
            group("Graphics", "", [
                pg_leaf("General", "", "dolphin_pg_gfx_general"),
                pg_leaf("Enhancements", "", "dolphin_pg_gfx_enh"),
                pg_leaf("Hacks", "", "dolphin_pg_gfx_hacks"),
                pg_leaf("Advanced", "", "dolphin_pg_gfx_adv"),
            ]),
            pg_leaf("AR codes", "", "dolphin_ar", key="dolphin_ar"),
            pg_leaf("Gecko codes", "", "dolphin_gecko", key="dolphin_gecko"),
        ]
        return mad_tree.pergame_menu(label, games_ns, subs, row_label=lbl, suffix=lbl)

    pergame = group("Per-game", "", [
        pergame_menu("GameCube games", "dolphinpg_gc"),
        pergame_menu("Wii games", "dolphinpg_wii"),
    ])

    # Canonical Switch-emu order (mad_tree.section_order): System, Video, Audio, Input,
    # Per-game -- Audio precedes Input, matching the Switch trio + Cemu. pergame here is a
    # group (GameCube/Wii games); section_order is kind-agnostic and just appends it last.
    return mad_tree.section_order(
        system=group("System", "", system),
        video=group("Video", "", video),
        audio=row("Audio", "", "settings", "dolphin_audio"),
        inp=group("Input", "", inp),
        pergame=pergame,
    )


def _sections_for(s: dict, syss: list[str] | None = None) -> list[dict]:
    """The config sections a tile offers, in display order. Single-child groups are
    collapsed to open their child directly (memory mad-collapse-single-child-groups)."""
    return _collapse_singletons(_sections_for_impl(s, syss))


def _sections_for_impl(s: dict, syss: list[str] | None = None) -> list[dict]:
    if s.get("key") == "dolphin":
        return _dolphin_sections(s, syss)
    if s.get("key") == "cemu":
        return _cemu_sections(s)
    if s.get("key") == "citron":
        return _citron_sections(s)
    if s.get("key") == "eden":
        return _eden_sections(s)
    if s.get("key") == "ryujinx":
        return _ryujinx_sections(s)
    if s.get("kind") == "model2":
        return [{"label": "Settings", "sublabel": "", "kind": "model2"}]
    if s.get("kind") == "daphne":
        return [
            {"label": "Button mapping", "sublabel": "",
             "kind": "daphne_map"},
            {"label": "Controllers", "sublabel": "",
             "kind": "gamepad", "arg": "hypseus"},
        ]
    if s.get("kind") == "lindbergh":
        # GAME-FIRST (standing rule mad-pergame-game-first): ONE "Per-game" row -> pick a game ONCE ->
        # [Settings, Controllers, Input mapping], every leaf editing the picked title. lindbergh-loader
        # has NO global config, so every leaf is per-game and this is the WHOLE tile (a single-section
        # tile opens the game picker directly). Same settings_pergame_menu pattern as PS2 / the Switch
        # emus; the C++ browser injects the picked titleid into every leaf's ctxVal. Settings reuses the
        # generic pergame_settings kind (GuiMadPageEmuSettings on ns "lindbergh", fed the titleid, same
        # page the old picker opened); Controllers + Input mapping use lindbergh-specific per-game kinds
        # so the already-picked game flows straight through (no second picker). Controllers carries a
        # stable `key` so lightgun / profile-less games (where pads->players is inert) hide it via the
        # hide list lindbergh.games returns.
        pergame_leaves = [
            {"label": "Settings", "sublabel": "",
             "kind": "pergame_settings", "arg": "lindbergh",
             "title": "Sega Lindbergh - Settings"},
            {"label": "Controllers", "sublabel": "", "key": "lindbergh_pads",
             "kind": "pergame_lindbergh_pads", "arg": "lindbergh",
             "title": "Sega Lindbergh - Controllers"},
            {"label": "Input mapping", "sublabel": "",
             "kind": "pergame_lindbergh_map", "arg": "lindbergh",
             "title": "Sega Lindbergh - Input mapping"},
        ]
        return [mad_tree.pergame_menu(s["label"], "lindbergh", pergame_leaves, suffix="Per-game")]
    if s.get("key") == "mugen":
        # Input: the "MAD Pad" merger knobs -- player pad families / seat priority, the
        # analog-stick gate (box vs radial 8-way) + deadzone, the handheld fallback pad,
        # and the X-Arcade warn toggle -- all on the shared backends.describe "gamepad"
        # page (kind gamepad, arg "mugen"; the tile carries backend:"mugen"). The warn
        # toggle rides describe's __sysflag__ knob, so mugen is excluded from the trailing
        # tile_flag_sections chip below (no duplicate). Per-game: game-first config tree,
        # one Settings leaf editing the picked game's save/config.ini via mugen_cmds.
        pergame_leaves = [
            {"label": "Settings", "sublabel": "",
             "kind": "pergame_settings", "arg": "mugen",
             "title": mad_tree.title(s["label"], "Settings")},
        ]
        return mad_tree.section_order(
            inp={"label": "Input", "sublabel": "", "kind": "gamepad", "arg": "mugen",
                 "title": mad_tree.title(s["label"], "Input")},
            pergame=mad_tree.pergame_menu(s["label"], "mugen", pergame_leaves,
                                          suffix="Per-game"))
    if s.get("key") == "pcsx2":
        # PS2 tile = a NESTED MENU: 4 top-level rows (Graphics/Input groups, Audio, Per-game
        # group); group rows carry nested `sections`. The C++ chooser renders these and opens
        # a sub-chooser when kind=="group".
        return _pcsx2_sections(s)
    if s.get("key") == "rpcs3":
        # PS3 tile = a PCSX2-style NESTED MENU: Input group + Settings group (5 category pages).
        return _rpcs3_sections(s)
    secs = []
    if "settings_ns" in s:
        secs.append({"label": "Settings", "sublabel": "",
                     "kind": "settings", "arg": s["settings_ns"],
                     "title": s["label"] + " - Settings"})
    if s.get("key") in _INPUT_MAP_EMUS:
        secs.append({"label": "Input mapping", "sublabel": "",
                     "kind": "input_map", "arg": s["key"],
                     "title": s["label"] + " - Input mapping"})
    if s.get("key") in _PADS_MAP_EMUS:
        # Per-emulator device assignment (writes the emulator's own config).
        secs.append({"label": "Controllers", "sublabel": "",
                     "kind": "pads_map", "arg": s["key"],
                     "title": s["label"] + " - Controllers"})
    elif "backend" in s:
        secs.append({"label": "Controllers", "sublabel": "",
                     "kind": "gamepad", "arg": s["backend"]})
    # pcsx2x6: the Lightgun page (crosshair / Sinden border / Start Sinden guns) appears
    # only when a USB port is set to the Light Gun (guncon2) controller type, chosen via
    # the Input-mapping page's USB-port Type selector. standalones.list re-runs per
    # tile-grid open, so picking the type then re-entering shows/hides this section.
    if s.get("key") == "pcsx2x6" and _pcsx2x6_has_guncon2():
        secs.append({"label": "Lightgun", "sublabel": "",
                     "kind": "settings", "arg": "pcsx2x6_lightgun",
                     "title": s["label"] + " lightgun"})
    return secs


def _emu_tile(emu: dict) -> dict | None:
    """Build a member tile (icon from router-config/icons via resolve_art)."""
    secs = _sections_for(emu)
    if not secs:
        return None
    icon = resolve_art([emu["icon"]]) if emu.get("icon") else ""
    return {"key": emu["key"], "label": emu["label"], "sublabel": "",
            "art": [icon] if icon else [], "sections": secs}


# ── grid presentation (P9): a section chooser with >=2 navigable rows renders as a tiled icon
#    GRID (a tile carrying `members`) instead of a vertical list. The shipping C++ already renders
#    `members` as a sub-grid (GuiMadPageStandalones::open), so this is PYTHON-ONLY, no rebuild. Each
#    navigable section becomes a member tile with a category icon (theme-first by label slug); a
#    group's children become that member's sub-chooser, a leaf/menu opens directly. A gridified
#    chooser DROPS the per-tile controller-policy warn TOGGLE (a chip has no grid-tile home). The
#    gamepad-backed emus (Cemu/Daphne/OpenBOR/Supermodel/xemu) instead show that warn flag as a knob
#    ON their gamepad page (backends.describe "__sysflag__" knob), so it stays reachable single-step.
#    pcsx2/rpcs3 have NO gamepad page (pads_map, not gamepad), so their warn flag is policy-config-only
#    (defaults ON, rarely fires). Dropping the chip makes gamepad Controllers single-step (no redundant
#    Controllers -> [Controllers, toggle]) and frees Input to a full grid. A chooser with <=1 navigable
#    section stays a list (opens direct / lone-toggle inline, e.g. MUGEN, which keeps its warn chip).
#    Deep category sub-menus tile too (fully recursive). ──
def _cat_slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


# A category tile's icon is derived from its label slug, but the theme's icon files use the
# artist's own shorthand (e.g. "Renderer & Display" -> render-display.png, "JVS controls" ->
# jvs-controls_.png) that the auto-slug does not match. This aliases the computed slug to the
# real file (or a semantic fallback). A dedicated <slug>.png ALWAYS wins if present (slug is
# tried first below), so dropping a "gamecube.png" later would override its console-art fallback.
# "console:<sys>" resolves to the active theme's per-system console.png.
_CAT_ART_ALIAS = {
    "renderer-display":   "render-display",
    "rendering-hardware": "rendering-hw",
    "rendering-software": "rendering-sw",
    "hardware-fixes":     "hw-fixes",
    "advanced-graphics":  "adv-graphics",
    "jvs-controls":       "jvs-controls_",
    "gpu-extensions":     "gpu",
    "gamecube-games":     "console:gc",   # Dolphin per-game menus: use each console's own art
    "wii-games":          "console:wii",
    "enhancements":       "post-processing",   # Dolphin Enhancements tab (AA/AF/post-processing)
    "hacks":              "hw-fixes",          # Dolphin Hacks tab (EFB/hardware hacks)
    "gamecube":           "console:gc",        # console partitions use the theme's console art
    "wii":                "console:wii",
    "pad-mapping":        "input-mapping",     # On-the-go RetroArch handheld-input sub-grid
    "hotkey-combos":      "hotkeys",
    "per-game-input":     "per-game",
    "per-game-resolution": "per-game",         # On-the-go MUGEN per-game resolution tile
    "classic-controller-order": "classic-controller-pads",  # renamed label keeps its icon
}


def _cat_art(label: str) -> list:
    slug = _cat_slug(label)
    cands = [f"icons/{slug}.png", f"{slug}.png"]   # a dedicated <slug>.png wins if it exists
    alias = _CAT_ART_ALIAS.get(slug)
    if alias:
        cands += ([alias] if alias.startswith("console:")
                  else [f"icons/{alias}.png", f"{alias}.png"])
    icon = resolve_art(cands)
    return [icon] if icon else []


def _members_from_sections(keyprefix: str, sections: list) -> list:
    """RECURSIVELY build member tiles: a GROUP with >=2 navigable children AND no toggle child
    becomes a sub-grid (recurse all the way down); anything else opens directly (a leaf/menu) or as
    a list (a group that holds a toggle chip needs a list context to render it). Art is the label
    slug, theme-first."""
    members = []
    for s in sections:
        label = s["label"]
        mkey = f"{keyprefix}__{_cat_slug(label)}"
        m = {"key": mkey, "label": label, "sublabel": "", "art": _cat_art(label)}
        kids = (s["sections"] if s.get("kind") == "group" and isinstance(s.get("sections"), list)
                else None)
        nav = [k for k in kids if k.get("kind") != "toggle"] if kids is not None else []
        has_toggle = any(k.get("kind") == "toggle" for k in kids) if kids is not None else False
        if kids is not None and len(nav) >= 2 and not has_toggle:
            m["members"] = _members_from_sections(mkey, kids)      # >=2 clean children -> sub-grid
        else:
            m["sections"] = kids if kids is not None else [s]      # toggle-group/<2 -> list; leaf -> direct
        members.append(m)
    return members


def _gridify_tile(t: dict) -> dict:
    """Turn a tile's section chooser into a members-grid when it has >=2 navigable (non-toggle)
    sections, recursively (every multi-item chooser becomes a grid); recurse into an existing group
    tile's members. <=1 nav section is left untouched (opens direct / lone-toggle inline)."""
    if isinstance(t.get("members"), list):
        return {**t, "members": [_gridify_tile(m) for m in t["members"]]}
    secs = t.get("sections") or []
    nav = [s for s in secs if s.get("kind") != "toggle"]
    if len(nav) < 2:
        return t
    # gridify the navigable sections; the per-tile warn toggle (if any) is dropped -- a chip has no
    # grid-tile home. For a standalone-only system this leaves the flag with NO in-UI control (it is
    # not in the RA Per-system editor); it defaults ON and is otherwise policy-config-only (see the
    # header note -- restoring a reachable control is a pending decision).
    members = _members_from_sections(t["key"], nav)
    out = {k: v for k, v in t.items() if k != "sections"}
    out["members"] = members
    return out


def _art_leaf(leaf: dict) -> dict:
    """Give a per-game menu leaf (and its sub-leaves, recursively) a tile icon via _cat_art, so the
    C++ renders the picked game's pages as a tiled grid. A leaf with no matching icon stays art-less
    (a label-only tile -- the grid always draws the label). Slug-first, so a dedicated <slug>.png the
    user later drops in auto-appears."""
    out = dict(leaf)
    if not out.get("art"):
        out["art"] = _cat_art(out.get("label", ""))
    if isinstance(out.get("sections"), list):
        out["sections"] = [_art_leaf(s) for s in out["sections"]]
    return out


def _decorate_pergame_section(s: dict) -> dict:
    """Recurse a section tree: a settings_pergame_menu gets art on its leaves (for the tiled per-game
    menu); a group is descended into (Dolphin nests its GameCube/Wii per-game menus inside a group).
    The menu ROW itself is untouched -- only its leaves gain art."""
    if s.get("kind") == "settings_pergame_menu" and isinstance(s.get("sections"), list):
        return {**s, "sections": [_art_leaf(lf) for lf in s["sections"]]}
    if isinstance(s.get("sections"), list):
        return {**s, "sections": [_decorate_pergame_section(c) for c in s["sections"]]}
    return s


def _decorate_pergame(t: dict) -> dict:
    """Decorate every settings_pergame_menu's leaves with tile art, recursing through member
    sub-grids AND nested group sections. Runs before _gridify_tile (which never descends into a
    settings_pergame_menu's leaves, so the art survives gridification)."""
    out = dict(t)
    if isinstance(out.get("sections"), list):
        out["sections"] = [_decorate_pergame_section(s) for s in out["sections"]]
    if isinstance(out.get("members"), list):
        out["members"] = [_decorate_pergame(m) for m in out["members"]]
    return out


@method("standalones.list", slow=True)
def _standalones_list(params):
    """Tiles for the standalone emulators present in ES-DE. A normal tile carries
    its config `sections`; a GROUP tile (e.g. Switch) carries `members` — a
    sub-grid of emulator tiles the C++ opens on tile press. Tiles use the
    system's console.png; member tiles use their router-config/icons art."""
    # "Present" = systems the user ACTUALLY has games for -- at least one VISIBLE game, not merely a
    # gamelist.xml on disk (ES-DE leaves an empty gamelist.xml behind after you delete a system's
    # last game, so a bare file check would keep an emptied system on the grid). es_gamelist
    # .visible_records is the same "has games" signal the RetroArch hub uses. None = couldn't
    # determine -> don't filter (show all, the old fallback).
    try:
        present = {s for s in es_systems.load_systems()
                   if es_systems._has_gamelist(s) and es_gamelist.visible_records(s)}
    except Exception:
        present = None
    tiles = []
    for s in STANDALONES:
        syss = ([sy for sy in s["systems"] if sy in present]
                if present is not None else list(s["systems"]))
        if s["key"] == "pcsx2x6":
            # A GROUP tile: Arcade + Retail members. Show the tile if the Namco arcade system has
            # games OR the retail (-datapath) GunCon2 setup is installed -- retail games live under
            # the ps2 system, NOT pcsx2x6, so gating the whole tile on the pcsx2x6 gamelist would
            # hide the Retail tree from a retail-only user. Members are gated independently, and it
            # collapses to a single tile when only one is present (like the Switch group).
            art = console_art("pcsx2x6")
            members = _pcsx2x6_members(art, arcade_present=bool(syss))
            if not members:
                continue
            if len(members) == 1:
                tiles.append({"key": s["key"], "label": s["label"], "sublabel": "",
                              "art": [art] if art else [], "sections": members[0]["sections"]})
            else:
                tiles.append({"key": s["key"], "label": s["label"], "sublabel": "",
                              "art": [art] if art else [], "members": members})
            continue
        if not syss:
            continue
        art = next((a for a in (console_art(sy) for sy in syss) if a), None)
        if "members" in s:
            # Only offer the Switch emulators that are actually installed, so the tile is
            # DYNAMIC: 2+ present → a sub-grid ordered ALPHABETICALLY by emulator name
            # (Citron, Eden, Ryujinx); exactly one → collapse to a normal tile that opens
            # straight into that emulator's sections (no mid-step); neither → drop the tile.
            members = [t for t in (_emu_tile(_EMUS[m]) for m in s["members"]
                                   if _emu_installed(m)) if t]
            members.sort(key=lambda t: (t.get("label") or "").lower())   # alphabetical sub-grid
            if not members:
                continue
            if len(members) == 1:
                # Collapse: keep the group's label + console art, but carry the lone
                # member's sections (its arg=<emu> section kinds open the emu's pages).
                tiles.append({"key": s["key"], "label": s["label"], "sublabel": "",
                              "art": [art] if art else [], "sections": members[0]["sections"]})
                continue
            tiles.append({"key": s["key"], "label": s["label"], "sublabel": "",
                          "art": [art] if art else [], "members": members})
            continue
        sections = _sections_for(s, syss)
        # Append the per-system controller-policy toggles (X-Arcade warning; wii
        # also gets DolphinBar/Sinden/hands-off) for the systems this tile drives.
        # Done centrally here so it also lands on tiles with bespoke section
        # builders (pcsx2/daphne/lindbergh) and on the section-less MUGEN tile.
        # EXCEPT dolphin, which embeds its wii AND gc flag leaves inside _dolphin_sections
        # (gated on present systems) so the flags nest with each console's other controls.
        # dolphin embeds its own gc/wii flags; lindbergh is a single per-game menu that should open
        # STRAIGHT to the game picker (single-step), so it skips the trailing warn toggle -- its
        # X-Arcade warn flag defaults ON and is policy-config-only (lindbergh is X-Arcade-driven, so
        # the warning rarely matters); no in-UI control, same pending decision as the gridified tiles.
        # openbor skips it for the OPPOSITE reason: its warn flag is ALREADY on its Controllers page
        # (backends.describe emits it as __sysflag__openbor__warn_when_no_xarcade for every
        # single-system gamepad backend). Appending it here rendered the SAME control twice -- openbor
        # was the only tile that did, because _gridify_tile drops the chip at >=2 nav sections and
        # openbor has exactly one. The duplicate was also what forced a chooser in front of a
        # single-page tile; without it secs.size()==1 and GuiMadPageStandalones opens Controllers
        # directly. mugen is now the SAME case: it has an Input (gamepad) page whose
        # backends.describe emits __sysflag__mugen__warn_when_no_xarcade, so appending the chip here
        # would render it twice -- exclude mugen too.
        # cemu: its "Controllers" backends.describe page was removed and the X-Arcade warn relocated onto
        # the cemu Input page (cemu_input_cmds), so exclude cemu too -- else the (gridified-dropped) chip
        # is appended for nothing and would double the control if the tile ever de-gridified.
        if s.get("key") not in ("dolphin", "lindbergh", "openbor", "mugen", "cemu"):
            sections = sections + policy_settings_cmds.tile_flag_sections(syss, s["label"])
        if not sections:
            continue
        # Tiles show ONLY the system name (no sublabel) per user request; the
        # section breakdown is shown on the tile's chooser page after opening.
        tiles.append({"key": s["key"], "label": s["label"], "sublabel": "",
                      "art": [art] if art else [], "sections": sections})
    tiles.sort(key=lambda t: (t.get("label") or "").lower())   # alphabetical by label
    return {"tiles": [_gridify_tile(_decorate_pergame(t)) for t in tiles]}
