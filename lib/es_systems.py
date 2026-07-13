"""
Read ES-DE's *active* system set straight from its config so other tools don't
hardcode (and drift) lists of systems.

This is the single source of truth for the hold-to-quit combo feature: which
systems are standalone-emulator systems (RetroArch has its own quit hotkey, so
it's excluded), and the shell command that quits each one.

Sources, read-only:
  * Bundled  es_systems.xml  (every system ES-DE ships)
  * Custom   ~/ES-DE/custom_systems/es_systems.xml  (overrides bundled by <name>)
  * Per-system gamelist <alternativeEmulator><label> — the emulator the user
    actually picked, which decides *which* <command> is the active one.

A system's quit command is resolved curated-first, derived-as-fallback:
  1. RetroArch command            -> "" (RA quits itself)
  2. backend == "dolphin" (Wii)   -> "" (real Wiimotes are HID; separate watcher)
  3. [backends.<backend>].quit_cmd -> use it (the only place a flatpak appid can
     live — appids can't be derived from a command)
  4. otherwise derive `pkill -TERM -f <token>` from the emulator token, guarded
  5. system not in ES-DE          -> ""

Stdlib only (no pip): xml.etree + re + os + pathlib.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .esde_paths import bundled_es_systems              # noqa: E402
from . import esde_settings                             # noqa: E402
BUNDLED = bundled_es_systems()   # running AppImage mount → AppDir → /usr/share
CUSTOM = esde_settings.APPDATA / "custom_systems" / "es_systems.xml"   # honors $ESDE_APPDATA_DIR
GAMELISTS = esde_settings.APPDATA / "gamelists"

_RA_MACRO = "%EMULATOR_RETROARCH%"


def _parse(path: Path) -> dict[str, list[tuple[str, str]]]:
    """system name -> [(command label, command text), ...] for one XML file."""
    out: dict[str, list[tuple[str, str]]] = {}
    if not path.is_file():
        return out
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return out
    for sysel in root.findall("system"):
        name = (sysel.findtext("name") or "").strip()
        if not name:
            continue
        cmds: list[tuple[str, str]] = []
        for c in sysel.findall("command"):
            label = (c.get("label") or "").strip()
            text = " ".join((c.text or "").split())   # collapse whitespace/newlines
            if text:
                cmds.append((label, text))
        out[name] = cmds
    return out


def load_systems() -> dict[str, list[tuple[str, str]]]:
    """Effective system set: bundled, then custom overrides wholesale by name."""
    systems = _parse(BUNDLED)
    systems.update(_parse(CUSTOM))
    return systems


_ALT_RE = re.compile(r"<alternativeEmulator>\s*<label>(.*?)</label>", re.DOTALL)


def _has_gamelist(system: str) -> bool:
    """True if ES-DE has a gamelist for this system — the practical 'active in
    ES-DE / the user actually has games for it' signal (ES-DE hides empty
    systems, and a freshly-scraped system gets a gamelist)."""
    return (GAMELISTS / system / "gamelist.xml").is_file()


def _active_label(system: str) -> str | None:
    """The system-level alternativeEmulator <label> the user chose (or None).
    Read via regex on the file head — robust where ET.parse trips on a big or
    quirky gamelist, and the tag is always written near the top by ES-DE."""
    gl = GAMELISTS / system / "gamelist.xml"
    if not gl.is_file():
        return None
    try:
        head = gl.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return None
    mobj = _ALT_RE.search(head)
    return mobj.group(1).strip() if mobj else None


def default_command(system: str, systems: dict | None = None) -> str:
    """The active emulator's command for a system: the <command> whose label
    matches the gamelist's alternativeEmulator, else the first <command>."""
    if systems is None:
        systems = load_systems()
    cmds = systems.get(system) or []
    if not cmds:
        return ""
    want = _active_label(system)
    if want:
        for label, text in cmds:
            if label == want:
                return text
    return cmds[0][1]


def is_standalone(cmd: str) -> bool:
    return bool(cmd) and _RA_MACRO not in cmd


def resolved_command(system: str, stem: str, systems: dict | None = None) -> str:
    """The <command> THIS launch actually runs: the per-game <altemulator> command if the
    gamelist carries one for this game, else the system's active default_command. Mirrors the
    per-game resolution retroarch_cfg.launched_core uses, so RA-vs-standalone detection agrees."""
    if systems is None:
        systems = load_systems()
    alt = None
    try:
        from . import es_gamelist
        alt = es_gamelist.record(system, stem).get("altemulator")
    except Exception:
        alt = None
    if alt:
        for label, text in systems.get(system, []):
            if label == alt:
                return text
    return default_command(system, systems)


# A resolved STANDALONE command -> a stable backend id. The MAD launch wrappers pass the emulator
# as their first argument (`mad-standalone-launch.py pcsx2 ...`, `mad-switch-launch.py eden ...`),
# which is the most reliable signal; else fall back to the %EMULATOR_*% macro / binary. Used by the
# handheld-resolution rail to route to the right config; an unrecognized emulator -> None -> no-op.
_WRAPPER_RE = re.compile(r"mad-(?:standalone|switch)-launch\.py\s+(\S+)")
_STANDALONE_MACRO_BACKENDS = (
    ("dolphin",               ("%EMULATOR_DOLPHIN%", "org.DolphinEmu", "dolphin-emu")),
    ("redream",               ("%EMULATOR_REDREAM%", "/redream")),
    ("flycast-standalone",    ("%EMULATOR_FLYCAST%",)),
    ("duckstation-standalone",("%EMULATOR_DUCKSTATION%", "duckstation")),
    ("yabasanshiro",          ("%EMULATOR_YABASANSHIRO%", "yabasanshiro")),
    ("kronos-standalone",     ("%EMULATOR_KRONOS%",)),
)


def standalone_backend_id(cmd: str) -> str | None:
    """A stable backend id for a resolved STANDALONE command, or None for a RetroArch command or an
    emulator we can't name. Prefers the MAD wrapper's emu argument, else a %EMULATOR_*%/binary scan."""
    if not cmd or _RA_MACRO in cmd:
        return None
    m = _WRAPPER_RE.search(cmd)
    if m:
        return m.group(1).strip().lower()
    for backend_id, needles in _STANDALONE_MACRO_BACKENDS:
        if any(n in cmd for n in needles):
            return backend_id
    return None


def _resolve_backend(policy: dict, system: str) -> str | None:
    """The system's backend, resolved via the FULL `inherits` chain (delegates to
    the router's routing.resolve_system, which also guards cycles/bad parents)."""
    from . import routing
    return (routing.resolve_system(policy, system) or {}).get("backend")


def _derive_quit(cmd: str) -> str:
    """Best-effort `pkill -TERM -f <token>` from a standalone command, for a new
    emulator with no curated backend quit_cmd. Guarded so it never produces a
    dangerously broad or meaningless pattern; "" means "can't derive safely"."""
    if " -- " in cmd:                       # wrapped: take the real command
        cmd = cmd.split(" -- ", 1)[1]
    toks = cmd.split()
    i = 0
    # skip ES-DE env-injection prefixes: %INJECT%=…, %STARTDIR%=…, %ENABLESHORTCUTS%
    while i < len(toks) and (
            (toks[i].startswith("%") and "=" in toks[i]) or toks[i] == "%ENABLESHORTCUTS%"):
        i += 1
    if i >= len(toks):
        return ""
    tok = toks[i]
    up = tok.upper()
    if "SHELL%" in up or "SHORTCUT" in up or "ENABLESHORTCUTS" in up:
        return ""
    name = ""
    if tok.startswith("%EMULATOR_") and tok.endswith("%"):
        inner = tok[len("%EMULATOR_"):-1]               # CEMU, PCSX2, HYPSEUS-SINGE…
        if "SHELL" in inner.upper():
            return ""
        name = re.split(r"[^A-Za-z0-9]", inner)[0].lower()   # HYPSEUS-SINGE -> hypseus
    elif tok.startswith("/") or tok.startswith("~"):
        stem = re.sub(r"\.appimage$", "", os.path.basename(tok), flags=re.I)
        name = re.split(r"[^A-Za-z0-9]", stem)[0]        # Eden-Linux-… -> Eden
    if len(name) < 3:                                    # avoid catastrophic broad pkills
        return ""
    return f"pkill -TERM -f {name}"


def quit_cmd(system: str, policy: dict, systems: dict | None = None) -> str:
    """Resolve the hold-to-quit shell command for a system (see module docstring).
    Returns "" when the system isn't a standalone evdev-quit system."""
    cmd = default_command(system, systems)
    if not is_standalone(cmd):
        return ""                                        # RetroArch / unknown
    backend = _resolve_backend(policy, system)
    if backend:
        be = policy.get("backends", {}).get(backend, {})
        if "quit_cmd" in be:          # explicit value wins, even "" = opt OUT
            return str(be["quit_cmd"])  # (no derived fallback, no watcher started)
    if backend == "dolphin":
        return ""                                        # Wii DEFAULT (no explicit quit_cmd): real
                                                         # Wii Remotes quit via HID +/- (separate
                                                         # watcher). A [backends.dolphin].quit_cmd,
                                                         # set for gamepad/Classic-Controller play,
                                                         # wins above and enables the pad-combo quit.
    return _derive_quit(cmd)


def quit_combo_systems(policy: dict) -> list[str]:
    """The GUI list: every system the user ACTUALLY has (a gamelist exists) that
    resolves to a non-empty quit command — i.e. standalone-emulator systems with
    a way to quit them. Excludes RetroArch systems and Wii (HID) inherently, and
    bundled-but-unused systems (no gamelist). A newly-added/scraped standalone
    system appears here automatically. Stable-sorted for a steady GUI order.

    Note: `quit_cmd()` itself is NOT gamelist-gated — the game-start hook still
    gets a quit command for any standalone system it actually launches."""
    systems = load_systems()
    return sorted(s for s in systems
                  if _has_gamelist(s) and quit_cmd(s, policy, systems))


# RetroArch's own quit command, for the ONE case RA needs the evdev quit watcher:
# a lightgun game (P1/P2 mouse = the Sinden guns), where RA's mouse-button quit
# hotkey can't fire because RA polls hotkeys on player-1's mouse only. SIGTERM lets
# RA save+exit cleanly; the watcher arms a +6s SIGKILL backstop on top.
_RA_QUIT_CMD = "pkill -TERM -f retroarch"


def lightgun_ra_quit_cmd(rom: str, policy: dict, system: str,
                         systems: dict | None = None) -> str:
    """Quit command for a RETROARCH lightgun game — a ROM in a `require_sinden`
    custom collection (e.g. "Pew-Pew-Pew!!!") launched on a RetroArch core. Returns
    "" for everything else, so the game-start hook only starts the red-button quit
    watcher where it's actually needed:
      * standalone systems (incl. Wii/dolphin, supermodel) -> "" (they use their own
        quit_cmd() / Dolphin's own hotkey; never the RA killer);
      * non-lightgun RA games -> "" (they use RA's native exit hotkey, see Feature ②).
    """
    if is_standalone(default_command(system, systems)):
        return ""                       # not RetroArch — owned by quit_cmd()/HID watcher
    from . import es_collections as colls
    cfg_c = policy.get("collections", {})
    # A ROM can belong to SEVERAL enabled collections; collection_for_rom returns only the
    # FIRST (by CollectionSystemsCustom order), so checking it alone would miss a lightgun
    # game whose require_sinden collection isn't first. Start the watcher if ANY containing
    # collection is a lightgun (require_sinden) one.
    for name in colls.enabled_collections():
        if cfg_c.get(name, {}).get("require_sinden") and colls.rom_in_collection(rom, name):
            return _RA_QUIT_CMD
    return ""
