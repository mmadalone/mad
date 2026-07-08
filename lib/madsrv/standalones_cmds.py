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
import shutil
from pathlib import Path

from .. import es_systems
from . import cfgutil
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
    {"key": "dolphin",    "label": "Wii",                "systems": ["wii", "gc"],
     "backend": "dolphin", "settings_ns": "dolphin"},
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
    {"key": "rpcs3",      "label": "PlayStation 3",      "systems": ["ps3"],
     "backend": "rpcs3", "settings_ns": "rpcs3"},
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
    # M.U.G.E.N (Ikemen GO / native): a script-launched system that routes via
    # controller-router (inherits snes pad priority). It has no global/per-game
    # config editor, so its only tile section is the X-Arcade warning toggle
    # (injected by policy_settings_cmds.tile_flag_sections in standalones.list);
    # controllers use the inherited router priority. Tile shows only when present.
    {"key": "mugen",      "label": "M.U.G.E.N",          "systems": ["mugen"]},
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

# Emulators with a per-game settings editor (a game picker → the settings page targeting that
# game's override) on the DEFAULT flat path. Empty now: every Switch emu (Citron/Eden/Ryujinx)
# carries its per-game browser inside its own bespoke section tree.
_PERGAME_EMUS = set()

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
_PCSX2_CAT_SUB = {"pcsx2emu": "speed, frame pacing, save states",
                  "pcsx2gfx": "renderer, display, upscaling, capture",
                  "pcsx2osd": "on-screen display overlays",
                  "pcsx2aud": "volume, backend, latency, expansion",
                  "pcsx2adv": "EE / VU recompiler, clamping"}


def _pcsx2_cat_section(ns: str) -> dict:
    from . import pcsx2_settings
    title = pcsx2_settings.CATEGORIES[ns][0]
    return {"label": title, "sublabel": _PCSX2_CAT_SUB.get(ns, ""),
            "kind": "settings", "arg": ns, "title": "PlayStation 2 — " + title}


def _pcsx2_sections(s: dict) -> list[dict]:
    """The 4 top-level PS2 rows for the tile's chooser (a NESTED MENU, not tiles):
    Graphics (group), Input (group), Audio (opens directly), Per-game (group). A GROUP row
    = {label, sublabel, kind:"group", title, sections:[...sub rows...]}; the C++ chooser
    pushes a sub-chooser of `sections` when kind=="group"."""
    label = s["label"]
    graphics = [_pcsx2_cat_section(ns) for ns in ("pcsx2gfx", "pcsx2emu", "pcsx2osd", "pcsx2adv")]
    inp = [
        {"label": "Device visibility", "sublabel": "hide controllers from PCSX2",
         "kind": "pads_hide", "arg": "pcsx2", "title": label + " — Device visibility"},
        {"label": "Mappings", "sublabel": "remap controller buttons",
         "kind": "input_map", "arg": "pcsx2", "title": label + " — Mappings"},
        {"label": "Pads → players", "sublabel": "which pad is each player",
         "kind": "pads_map", "arg": "pcsx2", "title": label + " — Pads → players"},
        {"label": "Hotkeys", "sublabel": "fullscreen, save states, pause, fast-forward…",
         "kind": "input_map", "arg": "pcsx2hk", "title": label + " — Hotkeys"},
    ]
    # NOTE: the retail GunCon2 page moved to Namco 246/256 -> Retail -> Input (it is a
    # pcsx2x6-fork setup, not standard PCSX2), see _pcsx2x6_retail_input.
    pergame = [
        {"label": "Settings", "sublabel": "per-title setting overrides",
         "kind": "settings_pergame", "arg": "pcsx2pg", "title": label + " — Per-game settings"},
        # Input -> game picker -> a sub-menu [Controllers, Mappings] carrying the picked game
        # (the C++ "inputmenu" game-picker mode; controllers lead).
        {"label": "Input", "sublabel": "controllers + button mapping, per title",
         "kind": "input_pergame_menu", "arg": "pcsx2pgin", "title": label + " — Per-game input"},
    ]
    return [
        {"label": "Graphics", "sublabel": "video, emulation, OSD, advanced", "kind": "group",
         "arg": "", "title": label + " — Graphics", "sections": graphics},
        {"label": "Input", "sublabel": "controllers, mapping, device visibility", "kind": "group",
         "arg": "", "title": label + " — Input", "sections": inp},
        _pcsx2_cat_section("pcsx2aud"),   # Audio: a plain settings row -> opens the Audio page directly
        {"label": "Per-game", "sublabel": "per-title overrides", "kind": "group",
         "arg": "", "title": label + " — Per-game", "sections": pergame},
    ]


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
                "title": title or f"{label} — {lbl}"}

    leaves = [
        leaf("Global settings", "SDL source, enhanced mode, mouse mapping, multitap",
             "settings", "x6a_global"),
        leaf("Pads → players", "which controller is each player", "pads_map", "pcsx2x6",
             f"{label} — Controllers"),
        leaf("Controller Port 1", "DualShock2 button map", "input_map", "x6a_pad1"),
        leaf("Controller Port 2", "DualShock2 button map", "input_map", "x6a_pad2"),
        leaf("USB Port 1", "device type + binds (GunCon2 / HID mouse)", "input_map", "x6a_usb1"),
        leaf("USB Port 2", "device type + binds (GunCon2 / HID mouse)", "input_map", "x6a_usb2"),
        leaf("JVS controls", "Testmode: boot the Gun Adjust calibration screen",
             "settings", "pcsx2x6_jvs"),
        leaf("Hotkeys", "fullscreen, save states, pause, fast-forward…", "input_map", "x6a_hk"),
    ]
    if _pcsx2x6_has_guncon2():
        leaves.append(leaf("Lightgun", "crosshair image/size, Sinden border, start guns",
                           "settings", "pcsx2x6_lightgun"))
    return {"label": "Input", "sublabel": "global, ports, USB, JVS, hotkeys", "kind": "group",
            "arg": "", "title": f"{label} — Input", "sections": leaves}


def _pcsx2x6_retail_input(label: str) -> dict:
    """Retail is lightgun-only, so its Input group is gun-focused: Global settings, the two
    GunCon2 gun USB ports, and Hotkeys. Each USB port is a single-gun view of the shipped
    guncon2_retail page (binds + per-gun crosshair + the Sinden toggle). The DualShock2
    [Pad1]/[Pad2] are still bound at launch by switch_bind (ps2guncon) but a gun setup drives
    movement from the gun, so no Controller Port leaves; no Pads -> players (guns sit on FIXED
    USB ports); no JVS (retail is PS2 discs, not Namco arcade)."""
    def leaf(lbl, sub, kind, arg, title=None):
        return {"label": lbl, "sublabel": sub, "kind": kind, "arg": arg,
                "title": title or f"{label} — {lbl}"}

    leaves = [
        leaf("Global settings", "SDL source, enhanced mode, mouse mapping, multitap",
             "settings", "x6r_global"),
        leaf("USB Port 1", "GunCon 2 (Gun 1): binds, crosshair", "input_map", "x6r_usb1",
             "PS2 GunCon 2 — Gun 1"),
        leaf("USB Port 2", "GunCon 2 (Gun 2): binds, crosshair", "input_map", "x6r_usb2",
             "PS2 GunCon 2 — Gun 2"),
        leaf("Hotkeys", "fullscreen, save states, pause, fast-forward…", "input_map", "x6r_hk"),
    ]
    return {"label": "Input", "sublabel": "global, guns, hotkeys", "kind": "group",
            "arg": "", "title": f"{label} — Input", "sections": leaves}


def _pcsx2x6_member_sections(member, label: str, retail: bool) -> list[dict]:
    from . import pcsx2_fork_settings as fs
    inp = _pcsx2x6_retail_input(label) if retail else _pcsx2x6_arcade_input(label)
    return [fs.graphics_group(member, label), inp,
            fs.audio_row(member, label), fs.advanced_row(member, label)]


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
                "title": f"Citron per-game — {lbl}"}

    def group(lbl, sub, subs):
        # Opens a sub-chooser; the browser injects the picked titleid into `subs` on pick.
        return {"label": lbl, "sublabel": sub, "kind": "group", "arg": "",
                "title": f"Citron per-game — {lbl}", "sections": subs}

    system = [
        leaf("System", "region, language, docked mode…", "citron_pg_system"),
        leaf("CPU", "accuracy, unsafe optimizations", "citron_pg_cpu"),
        leaf("Linux", "GameMode", "citron_pg_linux"),
    ]
    video = [
        leaf("Graphics", "renderer, resolution, filters", "citron_pg_gfx"),
        leaf("Adv. Graphics", "accuracy, async, VRAM", "citron_pg_gfxadv"),
    ]
    leaves = [
        group("System", "system, CPU, GameMode", system),
        group("Video", "graphics + advanced graphics", video),
        leaf("Audio", "output engine, volume", "citron_pg_audio"),
        leaf("Input", "per-player named input profile", "citron_pg_input"),
        leaf("Add-Ons", "enable/disable mods, updates, DLC", "citron_addons"),
        leaf("Cheats", "enable/disable cheats", "citron_cheats"),
    ]
    return {"label": "Per-game", "sublabel": "pick a game, then its overrides",
            "kind": "settings_pergame_menu", "arg": "citron",
            "title": f"{label} — Per-game settings", "sections": leaves}


def _citron_sections(s: dict) -> list[dict]:
    label = s["label"]

    def row(lbl, sub, kind, arg, title=None):
        return {"label": lbl, "sublabel": sub, "kind": kind, "arg": arg,
                "title": title or f"{label} — {lbl}"}

    def group(lbl, sub, subs):
        # A group row opens a sub-chooser of `subs` (C++ recurses on kind:"group").
        return {"label": lbl, "sublabel": sub, "kind": "group", "arg": "",
                "title": f"{label} — {lbl}", "sections": subs}

    # Five top-level rows (canonical Switch-emu layout). Leaf rows are the former flat pages,
    # unchanged; only their nesting differs -- so every page opens exactly as before.
    system = [
        row("General", "core, memory, GameMode", "settings", "citron_general"),
        row("CPU", "accuracy, unsafe optimizations", "settings", "citron_cpu"),
        row("System", "region, language, docked mode, clock", "settings", "citron_system"),
        row("Dock detection", "auto docked/handheld at launch", "settings", "citron_dock"),
    ]
    video = [
        row("Graphics", "renderer, resolution, filters", "settings", "citron_gfx"),
        row("Graphics (Adv)", "accuracy, async, VRAM", "settings", "citron_gfxadv"),
    ]
    inp = [
        row("Controllers", "pads → players", "pads_map", "citron"),
        row("Input mapping", "remap controller buttons + profiles", "input_map", "citron"),
        row("Hotkeys", "fullscreen, save states, pause, fast-forward…", "input_map", "citron_hk"),
    ]
    return [
        group("System", "general, CPU, system, dock detection", system),
        group("Video", "graphics + advanced graphics", video),
        group("Input", "controllers, mapping, hotkeys", inp),
        row("Audio", "output engine, volume", "settings", "citron_audio"),
        _citron_pergame_row(label),
    ]


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
                "title": f"Eden per-game — {lbl}"}

    def group(lbl, sub, subs):
        return {"label": lbl, "sublabel": sub, "kind": "group", "arg": "",
                "title": f"Eden per-game — {lbl}", "sections": subs}

    system = [
        leaf("System", "region, language, docked mode…", "eden_pg_system"),
        leaf("CPU", "accuracy, unsafe optimizations", "eden_pg_cpu"),
        leaf("Linux", "GameMode", "eden_pg_linux"),
    ]
    video = [
        leaf("Graphics", "renderer, resolution, filters", "eden_pg_gfx"),
        leaf("Adv. Graphics", "accuracy, async, VRAM", "eden_pg_gfxadv"),
        leaf("GPU extensions", "Vulkan extensions, hacks", "eden_pg_gfxext"),
    ]
    leaves = [
        group("System", "system, CPU, GameMode", system),
        group("Video", "graphics, advanced, GPU extensions", video),
        leaf("Audio", "output engine, volume", "eden_pg_audio"),
        leaf("Input", "per-player named input profile", "eden_pg_input"),
        leaf("Add-Ons", "enable/disable mods, updates, DLC", "eden_addons"),
        leaf("Cheats", "enable/disable cheats", "eden_cheats"),
    ]
    return {"label": "Per-game", "sublabel": "pick a game, then its overrides",
            "kind": "settings_pergame_menu", "arg": "eden",
            "title": f"{label} — Per-game settings", "sections": leaves}


def _eden_sections(s: dict) -> list[dict]:
    label = s["label"]

    def row(lbl, sub, kind, arg, title=None):
        return {"label": lbl, "sublabel": sub, "kind": kind, "arg": arg,
                "title": title or f"{label} — {lbl}"}

    def group(lbl, sub, subs):
        # A group row opens a sub-chooser of `subs` (C++ recurses on kind:"group").
        return {"label": lbl, "sublabel": sub, "kind": "group", "arg": "",
                "title": f"{label} — {lbl}", "sections": subs}

    # Five top-level rows (canonical Switch-emu layout, memory switch-emu-menu-scheme). Leaf rows
    # are the former flat pages, relocated verbatim (same kind+arg); only their nesting differs.
    system = [
        row("General", "core, memory, GameMode", "settings", "eden_general"),
        row("CPU", "accuracy, unsafe optimizations", "settings", "eden_cpu"),
        row("System", "region, language, docked mode, clock", "settings", "eden_system"),
        row("Dock detection", "auto docked/handheld at launch", "settings", "eden_dock"),
    ]
    video = [
        row("Graphics", "renderer, resolution, filters", "settings", "eden_gfx"),
        row("Graphics (Adv)", "accuracy, async, VRAM", "settings", "eden_gfxadv"),
        row("GPU extensions", "Vulkan extensions, hacks", "settings", "eden_gfxext"),
    ]
    inp = [
        row("Controllers", "pads → players", "pads_map", "eden"),
        row("Input mapping", "remap controller buttons + profiles", "input_map", "eden"),
        row("Hotkeys", "fullscreen, save states, pause, fast-forward…", "input_map", "eden_hk"),
    ]
    return [
        group("System", "general, CPU, system, dock detection", system),
        group("Video", "graphics, advanced, GPU extensions", video),
        group("Input", "controllers, mapping, hotkeys", inp),
        row("Audio", "output engine, volume", "settings", "eden_audio"),
        _eden_pergame_row(label),
    ]


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
                "title": f"Ryujinx per-game — {lbl}"}

    def group(lbl, sub, subs):
        return {"label": lbl, "sublabel": sub, "kind": "group", "arg": "",
                "title": f"Ryujinx per-game — {lbl}", "sections": subs}

    system = [
        leaf("System", "region, language, docked mode", "ryujinx_pg_system"),
        leaf("CPU", "memory manager, PPTC, memory", "ryujinx_pg_cpu"),
    ]
    video = [
        leaf("Graphics", "API, resolution, filters", "ryujinx_pg_gfx"),
        leaf("Adv. Graphics", "shader cache, threading", "ryujinx_pg_gfxadv"),
    ]
    leaves = [
        group("System", "system + CPU", system),
        group("Video", "graphics + advanced graphics", video),
        leaf("Audio", "backend, volume", "ryujinx_pg_audio"),
        leaf("Add-Ons", "enable/disable mods, update, DLC", "ryujinx_addons"),
        leaf("Cheats", "enable/disable cheats", "ryujinx_cheats"),
    ]
    return {"label": "Per-game", "sublabel": "pick a game, then its overrides",
            "kind": "settings_pergame_menu", "arg": "ryujinx",
            "title": f"{label} — Per-game settings", "sections": leaves}


def _ryujinx_sections(s: dict) -> list[dict]:
    label = s["label"]

    def row(lbl, sub, kind, arg, title=None):
        return {"label": lbl, "sublabel": sub, "kind": kind, "arg": arg,
                "title": title or f"{label} — {lbl}"}

    def group(lbl, sub, subs):
        return {"label": lbl, "sublabel": sub, "kind": "group", "arg": "",
                "title": f"{label} — {lbl}", "sections": subs}

    system = [
        row("System", "region, language, docked mode", "settings", "ryujinx_system"),
        row("CPU", "memory manager, PPTC, console memory", "settings", "ryujinx_cpu"),
        row("Dock detection", "auto docked/handheld at launch", "settings", "ryujinx_dock"),
    ]
    video = [
        row("Graphics", "API, resolution, filters, VSync", "settings", "ryujinx_gfx"),
        row("Graphics (Adv)", "shader cache, threading", "settings", "ryujinx_gfxadv"),
    ]
    inp = [
        row("Controllers", "pads → players", "pads_map", "ryujinx"),
        row("Input mapping", "remap controller buttons", "input_map", "ryujinx"),
        row("Hotkeys", "vsync, screenshot, pause, mute…", "settings", "ryujinx_hk"),
    ]
    return [
        group("System", "system, CPU, dock detection", system),
        group("Video", "graphics + advanced graphics", video),
        group("Input", "controllers, mapping, hotkeys", inp),
        row("Audio", "backend, volume", "settings", "ryujinx_audio"),
        _ryujinx_pergame_row(label),
    ]


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
    BUFFERED page of plain on/off toggles (cemu_packs_<category>). Every row carries a stable `key` so
    the rebuilt browser can hide empty ones per game (cemu.games -> per-game `hide`); older AppImages
    ignore `key`/`hide` and just show them all. Two levels deep = works with the existing fork."""
    from . import cemu_packs_cmds as cp

    def leaf(lbl, sub, arg, key=""):
        row = {"label": lbl, "sublabel": sub, "kind": "pergame_settings", "arg": arg,
               "title": f"Wii U per-game - {lbl}"}
        if key:
            row["key"] = key
        return row

    packs = {"label": "Graphic packs", "sublabel": "on / off, by category", "kind": "group",
             "arg": "", "key": "packs", "title": "Wii U per-game - Graphic packs",
             "sections": [leaf(cat, "on / off toggles", f"cemu_packs_{cp.catkey(cat)}",
                               f"packs_{cp.catkey(cat)}") for cat in cp.CATEGORIES]}
    leaves = [
        leaf("General", "shared libraries, CPU mode, thread quantum, audio", "cemu_pg_general"),
        leaf("Graphics", "graphics API, shader multiply, precompiled shaders", "cemu_pg_gfx"),
        leaf("Controller", "named controller profiles per port", "cemu_pg_input"),
        packs,
    ]
    return {"label": "Per-game", "sublabel": "pick a game, then its overrides",
            "kind": "settings_pergame_menu", "arg": "cemu",
            "title": f"{label} - Per-game settings", "sections": leaves}


def _cemu_sections(s: dict) -> list[dict]:
    label = s["label"]

    def row(lbl, sub, kind, arg, title=None):
        return {"label": lbl, "sublabel": sub, "kind": kind, "arg": arg,
                "title": title or f"{label} - {lbl}"}

    def group(lbl, sub, subs):
        return {"label": lbl, "sublabel": sub, "kind": "group", "arg": "",
                "title": f"{label} - {lbl}", "sections": subs}

    graphics = [
        row("Graphics", "API, VSync, filters, scaling", "settings", "cemu_gfx"),
        row("Overlay", "performance overlay (FPS, CPU, RAM…)", "settings", "cemu_overlay"),
        row("Notifications", "on-screen notifications", "settings", "cemu_notif"),
    ]
    # Graphic packs are inherently per-game, so they live ONLY under Per-game (pick a game -> its
    # packs), NOT as a top-level row -- a global "all games" packs page just duplicated that.
    return [
        row("General", "startup, updates, language, GameMode", "settings", "cemu_general"),
        group("Graphics", "graphics + overlay + notifications", graphics),
        row("Audio", "volume, channels, latency", "settings", "cemu_audio"),
        # Controllers = the EXISTING router backend profile-picker (device-agnostic; works today).
        row("Controllers", "profiles per port (router-managed)", "gamepad", "cemu"),
        _cemu_pergame_row(label),
    ]


def _sections_for(s: dict) -> list[dict]:
    """The config sections a tile offers, in display order."""
    if s.get("key") == "cemu":
        return _cemu_sections(s)
    if s.get("key") == "citron":
        return _citron_sections(s)
    if s.get("key") == "eden":
        return _eden_sections(s)
    if s.get("key") == "ryujinx":
        return _ryujinx_sections(s)
    if s.get("kind") == "model2":
        return [{"label": "Settings", "sublabel": "emulator settings", "kind": "model2"}]
    if s.get("kind") == "daphne":
        return [
            {"label": "Button mapping", "sublabel": "map keys/buttons to Daphne actions",
             "kind": "daphne_map"},
            {"label": "Controllers", "sublabel": "which pads Daphne uses",
             "kind": "gamepad", "arg": "hypseus"},
        ]
    if s.get("kind") == "lindbergh":
        # Settings = the per-game picker (settings_pergame -> lindbergh.games/get/set,
        # buffered Save/Cancel). Input mapping = the per-game live binder (lindbergh_map
        # -> GuiMadPageLindbergh). Both are per-game; lindbergh-loader has no global config.
        return [
            {"label": "Settings", "sublabel": "per-game: region, aspect, crosshairs…",
             "kind": "settings_pergame", "arg": "lindbergh",
             "title": "Sega Lindbergh — Settings"},
            {"label": "Input mapping", "sublabel": "map controls to JVS buttons",
             "kind": "lindbergh_map", "arg": "lindbergh",
             "title": "Sega Lindbergh — Input mapping"},
            # Per-game per-pad profiles + player priority (non-lightgun games), so the
            # connected pad drives its player with its own bindings -> seamless fallback.
            {"label": "Controllers", "sublabel": "pads → players (per game)",
             "kind": "lindbergh_pads", "arg": "lindbergh",
             "title": "Sega Lindbergh — Controllers"},
        ]
    if s.get("key") == "pcsx2":
        # PS2 tile = a NESTED MENU: 4 top-level rows (Graphics/Input groups, Audio, Per-game
        # group); group rows carry nested `sections`. The C++ chooser renders these and opens
        # a sub-chooser when kind=="group".
        return _pcsx2_sections(s)
    secs = []
    if "settings_ns" in s:
        secs.append({"label": "Settings", "sublabel": "video / audio / render",
                     "kind": "settings", "arg": s["settings_ns"],
                     "title": s["label"] + " — Settings"})
    if s.get("key") in _PERGAME_EMUS:
        secs.append({"label": "Per-game settings", "sublabel": "override settings for one game",
                     "kind": "settings_pergame", "arg": s["key"],
                     "title": s["label"] + " — Per-game settings"})
    if s.get("key") in _INPUT_MAP_EMUS:
        secs.append({"label": "Input mapping", "sublabel": "remap controller buttons",
                     "kind": "input_map", "arg": s["key"],
                     "title": s["label"] + " — Input mapping"})
    if s.get("key") in _PADS_MAP_EMUS:
        # Per-emulator device assignment (writes the emulator's own config).
        secs.append({"label": "Controllers", "sublabel": "pads → players",
                     "kind": "pads_map", "arg": s["key"],
                     "title": s["label"] + " — Controllers"})
    elif "backend" in s:
        secs.append({"label": "Controllers", "sublabel": "pads → players",
                     "kind": "gamepad", "arg": s["backend"]})
    # pcsx2x6: the Lightgun page (crosshair / Sinden border / Start Sinden guns) appears
    # only when a USB port is set to the Light Gun (guncon2) controller type, chosen via
    # the Input-mapping page's USB-port Type selector. standalones.list re-runs per
    # tile-grid open, so picking the type then re-entering shows/hides this section.
    if s.get("key") == "pcsx2x6" and _pcsx2x6_has_guncon2():
        secs.append({"label": "Lightgun", "sublabel": "crosshair, border, start guns",
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


@method("standalones.list", slow=True)
def _standalones_list(params):
    """Tiles for the standalone emulators present in ES-DE. A normal tile carries
    its config `sections`; a GROUP tile (e.g. Switch) carries `members` — a
    sub-grid of emulator tiles the C++ opens on tile press. Tiles use the
    system's console.png; member tiles use their router-config/icons art."""
    # "Present" = systems the user ACTUALLY has games for (a gamelist exists) — the
    # same signal ES-DE uses to show a system (and es_systems.quit_combo_systems). So
    # an emulator with no games drops off the grid. None = couldn't determine → don't
    # filter (show all, the old fallback).
    try:
        present = {s for s in es_systems.load_systems() if es_systems._has_gamelist(s)}
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
        sections = _sections_for(s)
        # Append the per-system controller-policy toggles (X-Arcade warning; wii
        # also gets DolphinBar/Sinden/hands-off) for the systems this tile drives.
        # Done centrally here so it also lands on tiles with bespoke section
        # builders (pcsx2/daphne/lindbergh) and on the section-less MUGEN tile.
        sections = sections + policy_settings_cmds.tile_flag_sections(syss, s["label"])
        if not sections:
            continue
        # Tiles show ONLY the system name (no sublabel) per user request; the
        # section breakdown is shown on the tile's chooser page after opening.
        tiles.append({"key": s["key"], "label": s["label"], "sublabel": "",
                      "art": [art] if art else [], "sections": sections})
    tiles.sort(key=lambda t: (t.get("label") or "").lower())   # alphabetical by label
    return {"tiles": tiles}
