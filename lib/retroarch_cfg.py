"""
Per-game RetroArch override (.cfg) writer for the controller router.

Each ROM-launch may produce a tiny block of `input_player[N]_reserved_device`
and (for lightgun games) `input_player[N]_mouse_index` lines, written into
the per-core per-game override at:

    ~/.var/app/org.libretro.RetroArch/config/retroarch/config/<CoreName>/<ROM_basename>.cfg

These files often ALREADY exist — the bezel-project pipeline wrote ~15k of
them with `input_overlay`, `aspect_ratio_index`, and similar non-input
settings. We must preserve all of that. Our block is wrapped in sentinel
comments so it can be added, refreshed, or stripped without touching the
surrounding lines.

Writes are atomic (tmp + rename in the same directory) and idempotent
(re-running with the same input produces an identical file).
"""
from __future__ import annotations

import re
from pathlib import Path

from . import fsutil

# Sentinel markers — anything between BEGIN and END (inclusive) is owned by
# the router and may be rewritten/removed at will.
BEGIN = "# >>> controller-router begin (auto-managed) >>>"
END = "# <<< controller-router end <<<"

RA_CONFIG_BASE = (
    Path.home() / ".var/app/org.libretro.RetroArch/config/retroarch/config"
)


# System → list of RetroArch CoreDisplayName dirs we should write into. Same
# rom_basename gets the same block in each — RetroArch picks whichever core
# it actually launches and reads the matching override. Multi-core systems
# get multi-write; that's intentional.
#
# Verified against the user's actual core dirs (see ls of ~/.var/app/.../config).
SYSTEM_CORE_MAP: dict[str, list[str]] = {
    "3do":          ["Opera"],
    "amigacd32":    ["PUAE", "PUAE 2021"],
    "arcade":       ["FinalBurn Neo", "MAME", "MAME 2010", "FB Alpha 2012"],
    "atomiswave":   ["Flycast"],
    "daphne":       [],   # Hypseus-Singe, not RetroArch
    "dreamcast":    ["Flycast"],
    "famicom":      ["Nestopia", "Mesen", "FCEUmm", "QuickNES"],
    "fba":          ["FinalBurn Neo", "FB Alpha 2012"],
    "gameandwatch": ["Game & Watch"],
    "gb":           ["Gambatte", "SameBoy", "Gearboy", "TGB Dual", "mGBA"],
    "gba":          ["mGBA", "VBA-M", "VBA Next", "gpSP", "NooDS", "SkyEmu"],
    "gbc":          ["Gambatte", "SameBoy", "Gearboy", "TGB Dual", "mGBA"],
    "gc":           ["dolphin_emu"],   # GameCube — Dolphin libretro core
    "genesis":      ["Genesis Plus GX", "BlastEm", "PicoDrive"],
    "genh":         ["Genesis Plus GX"],
    "mame":         ["MAME", "MAME 2010", "MAME 2003-Plus"],
    "mastersystem": ["Gearsystem", "Genesis Plus GX"],
    "megadrive":    ["Genesis Plus GX", "BlastEm", "PicoDrive"],
    "model3":       [],   # Supermodel standalone
    "mugen":        [],   # mugen.sh wrapper
    "n64":          ["Mupen64Plus-Next", "ParaLLEl N64"],
    "naomi":        ["Flycast"],
    "naomi2":       ["Flycast"],
    "neogeo":       ["FinalBurn Neo", "FB Alpha 2012"],
    "nes":          ["Nestopia", "Mesen", "FCEUmm"],
    "pcengine":     ["Beetle PCE", "Beetle PCE Fast", "Beetle SuperGrafx"],
    "pcenginecd":   ["Beetle PCE", "Beetle PCE Fast", "Beetle SuperGrafx"],
    "pcfx":         ["Beetle PC-FX"],
    "ps2":          ["LRPS2", "PCSX2"],            # also has a PCSX2-standalone backend
    "psx":          ["Beetle PSX HW", "Beetle PSX", "SwanStation"],
    "saturn":       ["Beetle Saturn", "Kronos", "YabaSanshiro"],
    "sega32x":      ["PicoDrive"],
    "segacd":       ["Genesis Plus GX", "PicoDrive"],
    "sfc":          ["Snes9x", "bsnes", "bsnes-hd beta"],
    "snes":         ["Snes9x", "bsnes", "bsnes-hd beta"],
    "snesh":        ["Snes9x", "bsnes"],
    "snesmsu1":     ["Snes9x", "bsnes"],
    "supergrafx":   ["Beetle SuperGrafx"],
    "wii":          [],   # Dolphin (Standalone)
    "x68000":       ["PX68K"],
}


_INFO_DIR = RA_CONFIG_BASE.parent / "info"   # …/retroarch/info/<stem>_libretro.info
_corename_cache: dict[str, str | None] = {}
_CORE_SO_RE = re.compile(r"([A-Za-z0-9_]+)_libretro\.so")


def _corename(stem: str) -> str | None:
    """The libretro core's display name (= the name RetroArch uses for its
    per-game-override config dir), read from <stem>_libretro.info's `corename`
    line. Cached. None if the info file/line is absent."""
    if stem in _corename_cache:
        return _corename_cache[stem]
    cn = None
    try:
        for line in (_INFO_DIR / f"{stem}_libretro.info").read_text(
                encoding="utf-8", errors="replace").splitlines():
            if line.lstrip().startswith("corename"):
                m = re.search(r'corename\s*=\s*"?([^"]+?)"?\s*$', line)
                cn = m.group(1).strip() if m else None
                break
    except OSError:
        cn = None
    _corename_cache[stem] = cn
    return cn


def _derived_core_names(system: str) -> set[str]:
    """Core-dir names derived from the system's ES-DE RetroArch commands — the
    dynamic complement to SYSTEM_CORE_MAP, so a newly-added/wrapped RA system
    routes with no hand-edit. Empty if es_systems / the info dir is unavailable."""
    try:
        from . import es_systems        # lazy — no import cycle
        cmds = es_systems.load_systems().get(system, [])
    except Exception:
        return set()
    names = set()
    for _label, cmd in cmds:
        for m in _CORE_SO_RE.finditer(cmd):
            cn = _corename(m.group(1))
            if cn:
                names.add(cn)
    return names


def core_dirs_for_system(system: str) -> list[Path]:
    """Core dirs to write the per-game override into, restricted to those that
    actually exist on disk. UNION of the curated SYSTEM_CORE_MAP (exceptions /
    legacy baseline — covers corename≠dir cases like dolphin_emu and MAME 2010)
    and dirs DERIVED from the system's active ES-DE commands (covers new systems
    + cores the map missed). Multi-write is intentional so per-game
    <altemulator> overrides keep working. Degrades to exactly the old map result
    when derivation yields nothing."""
    names = set(SYSTEM_CORE_MAP.get(system, [])) | _derived_core_names(system)
    return [RA_CONFIG_BASE / n for n in sorted(names) if (RA_CONFIG_BASE / n).is_dir()]


# RetroArch device-reservation type written for every resolved player port.
#   "1" = RESERVED (exclusive): the port accepts ONLY its reserved device; no
#         other device may occupy it, and if the reserved device is absent the
#         port is left empty.
#   "2" = PREFERRED: the reserved device prefers the port, but ANY other device
#         may squat it when assignment order gets there first.
#
# We use RESERVED ("1"). PREFERRED ("2") was the original choice but it fails the
# router's whole purpose when MORE devices are connected than there are reserved
# ports — exactly the user's target setup (13 gamepads + X-Arcade + 2 Sinden
# guns all plugged in at once). Verified live 2026-06-04 (Ninjawarriors/Snes9x,
# Sindens unplugged, 3 pads present): the router correctly reserved P1=DualSense
# / P2=X-Arcade, yet RetroArch left the DualSense in port 2 and logged
#   "Preferred slot was taken earlier by (null), reassigning that to 1"
# — the preferred-cascade mis-bumped and the P1 reservation never took. The
# Sinden guns (which enumerate as joypads) jam ports 0/1 the same way. RESERVED
# makes the player ports exclusive, so guns / Wii-Pro / Steam-virtual pads are
# forced into the unreserved ports 3+ and can never displace the chosen pad.
# We only ever reserve devices we just enumerated as PRESENT (see
# controller-router._resolve_ports + its fallback), so "left empty if absent"
# can't strand a port. Same-vid:pid cascade (two X-Arcade ifaces → P1+P2, two
# identical pads → P1+P2) still works: RA fills reserved ports of a shared
# vid:pid in connection order.
_RESERVATION_TYPE = "1"


def _build_block(port_names: dict[int, str],
                 mouse_indices: dict[int, int] | None = None,
                 port_binds: dict[int, dict[str, str]] | None = None) -> str:
    """Generate the body of the sentinel block (no sentinels themselves).

    `port_binds` maps a port → {bind_suffix: value} for devices whose reserved
    port needs explicit physical→RetroPad binds (RetroArch does not carry a
    device's autoconfig binds onto a reserved port — see lib/device_binds.py).
    These override the global `input_player{N}_*` binds for the launch only.
    """
    lines = []
    for port in sorted(port_names):
        name = port_names[port]
        lines.append(f'input_player{port}_device_reservation_type = "{_RESERVATION_TYPE}"')
        lines.append(f'input_player{port}_reserved_device = "{name}"')
    if mouse_indices:
        for port in sorted(mouse_indices):
            idx = mouse_indices[port]
            lines.append(f'input_player{port}_mouse_index = "{idx}"')
    if port_binds:
        for port in sorted(port_binds):
            for suffix in sorted(port_binds[port]):
                val = port_binds[port][suffix]
                lines.append(f'input_player{port}_{suffix} = "{val}"')
    return "\n".join(lines) + "\n" if lines else ""


_SENTINEL_RE = re.compile(
    re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?",
    re.DOTALL,
)


def _strip_block(text: str) -> str:
    return _SENTINEL_RE.sub("", text)


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".router-tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)


def write_override(system: str, rom_basename: str,
                   port_names: dict[int, str],
                   mouse_indices: dict[int, int] | None = None,
                   port_binds: dict[int, dict[str, str]] | None = None,
                   ) -> list[Path]:
    """Write/refresh the router-managed sentinel block in each per-game
    override file under the system's core dirs.

    Returns the list of paths actually written. If the system has no
    configured cores (e.g. Daphne, MUGEN — non-RetroArch launches), returns
    an empty list and writes nothing.

    Atomic: tmp + rename in the same dir. Idempotent.
    """
    if not port_names and not mouse_indices and not port_binds:
        # Nothing to write — caller had no policy hits.
        return []

    block_body = _build_block(port_names, mouse_indices, port_binds)
    if not block_body:
        return []

    written: list[Path] = []
    for core_dir in core_dirs_for_system(system):
        target = core_dir / f"{rom_basename}.cfg"
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        cleaned = _strip_block(existing).rstrip("\n")
        block = f"{BEGIN}\n{block_body}{END}\n"
        if cleaned:
            merged = f"{cleaned}\n\n{block}"
        else:
            merged = block
        _atomic_write(target, merged)
        written.append(target)
    return written


def clear_override(system: str, rom_basename: str) -> list[Path]:
    """Strip the router-managed sentinel block from each per-game override.
    If the file is then empty (or comments-only), delete it.

    Returns the list of paths actually touched (stripped or deleted).
    """
    touched: list[Path] = []
    for core_dir in core_dirs_for_system(system):
        target = core_dir / f"{rom_basename}.cfg"
        if not target.exists():
            continue
        existing = target.read_text(encoding="utf-8")
        if BEGIN not in existing:
            continue  # nothing to do
        cleaned = _strip_block(existing).rstrip("\n")
        # Drop file if nothing meaningful left (only whitespace or comments)
        meaningful = any(
            line.strip() and not line.strip().startswith("#")
            for line in cleaned.splitlines()
        )
        if meaningful:
            _atomic_write(target, cleaned + "\n")
        elif any(line.strip() for line in cleaned.splitlines()):
            # Non-blank lines remain but none are real settings, so only USER COMMENTS are
            # left. That IS user data: MOVE to a recoverable _TMP (rule #5), never rm.
            fsutil.recoverable_delete(
                target, tmp_base=Path.home() / "Downloads" / "_TMP", tag="clear-override",
                recovery_note=f"Cleared router block from {target.name}; only comments remained.")
        else:
            # Nothing left at all: the file was a pure router-owned block (the common case,
            # rewritten fresh each launch for a game with no user cfg). Not user data, so plain
            # rm; otherwise we'd litter _TMP with a new dir on every RA game-end.
            target.unlink()
        touched.append(target)
    return touched


# ── MAD per-SYSTEM RetroArch options (Systems-page toggles) ──────────────────
# Distinct from the router's per-GAME block above. Written to the PER-CONTENT-
# DIRECTORY cfg `config/<Core>/<system>.cfg`, so it applies to every game of the
# system. Managed inside its own sentinel; preserves the bezel/overlay lines the
# bezel pipeline left there, and de-dups any pre-existing STANDALONE line for a
# managed key (e.g. the hand-added `video_driver = "glcore"` n64 fix).
SYS_BEGIN = "# >>> MAD system options (auto-managed) >>>"
SYS_END = "# <<< MAD system options end <<<"

_SYS_SENTINEL_RE = re.compile(
    re.escape(SYS_BEGIN) + r".*?" + re.escape(SYS_END) + r"\n?", re.DOTALL)


def _sys_managed(text: str) -> dict[str, str]:
    """The key→value pairs currently inside the MAD sentinel block."""
    m = _SYS_SENTINEL_RE.search(text)
    out: dict[str, str] = {}
    if not m:
        return out
    for line in m.group(0).splitlines():
        if line.strip().startswith("#"):
            continue
        mm = re.match(r'\s*(\w+)\s*=\s*"?([^"\n]*)"?\s*$', line)
        if mm:
            out[mm.group(1)] = mm.group(2)
    return out


def set_system_option(system: str, key: str, value: str | None) -> list[Path]:
    """Set (value) or clear (None) ONE RetroArch option for ALL of a system's
    cores, in `config/<Core>/<system>.cfg`. Idempotent + atomic; preserves
    unrelated lines; removes any standalone duplicate of the key so the managed
    value wins. Returns the cfg paths touched."""
    touched: list[Path] = []
    for core_dir in core_dirs_for_system(system):
        target = core_dir / f"{system}.cfg"
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        managed = _sys_managed(existing)
        body = _SYS_SENTINEL_RE.sub("", existing)
        drop = set(managed) | {key}
        body = "\n".join(
            ln for ln in body.splitlines()
            if not any(re.match(rf'\s*{re.escape(k)}\s*=', ln) for k in drop)
        ).rstrip("\n")
        if value is None:
            managed.pop(key, None)
        else:
            managed[key] = value
        parts = []
        if body:
            parts.append(body)
        if managed:
            block = "\n".join(f'{k} = "{v}"' for k, v in sorted(managed.items()))
            parts.append(f"{SYS_BEGIN}\n{block}\n{SYS_END}")
        new_text = ("\n\n".join(parts) + "\n") if parts else ""
        if new_text != existing:
            if new_text:
                _atomic_write(target, new_text)
            elif target.exists():
                target.unlink()
        touched.append(target)
    return touched


def get_system_option(system: str, key: str) -> str | None:
    """Effective value of `key` for the system (last occurrence wins, as RA
    layers it). Returns None if unset. Reads the first core cfg that has it."""
    for core_dir in core_dirs_for_system(system):
        target = core_dir / f"{system}.cfg"
        if not target.exists():
            continue
        val = None
        for ln in target.read_text(encoding="utf-8").splitlines():
            if ln.strip().startswith("#"):
                continue
            mm = re.match(rf'\s*{re.escape(key)}\s*=\s*"?([^"\n]*)"?\s*$', ln)
            if mm:
                val = mm.group(1)  # last wins
        if val is not None:
            return val
    return None


# ── global retroarch.cfg ──────────────────────────────────────────────────────
# The "configure RetroArch without desktop mode" surface. retroarch.cfg holds the
# GLOBAL defaults RA applies to every core; per-system overrides live in the
# config/<Core>/<system>.cfg files handled above. RA reads this file at startup
# and REWRITES THE WHOLE FILE on exit, so callers must refuse to write while it is
# running (use proc_guard.retroarch_running()).
RA_GLOBAL_CFG = RA_CONFIG_BASE.parent / "retroarch.cfg"
_GLOBAL_BAK = RA_CONFIG_BASE.parent / "retroarch.cfg.mad-bak"


def _ensure_global_bak(original: str) -> None:
    """One-time backup of retroarch.cfg before MAD's first edit — House rule #5:
    never clobber user data without a recoverable copy."""
    if original and not _GLOBAL_BAK.exists():
        try:
            _GLOBAL_BAK.write_text(original, encoding="utf-8")
        except OSError:
            pass


def get_global_option(key: str) -> str | None:
    """Effective value of `key` in the global retroarch.cfg (last line wins, the
    way RA reads it). None if the file or key is absent."""
    if not RA_GLOBAL_CFG.exists():
        return None
    val = None
    for ln in RA_GLOBAL_CFG.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.lstrip().startswith("#"):
            continue
        mm = re.match(rf'\s*{re.escape(key)}\s*=\s*"?([^"\n]*)"?\s*$', ln)
        if mm:
            val = mm.group(1)  # last wins
    return val


def get_global_options(keys) -> dict:
    """Read retroarch.cfg ONCE and return {key: value|None} for every requested
    key. Pages that need many keys (the input/keybindings page reads ~40) must use
    this instead of get_global_option per key, which re-reads the whole ~3000-line
    file each call."""
    result = {k: None for k in keys}
    if not RA_GLOBAL_CFG.exists():
        return result
    wanted = set(keys)
    for ln in RA_GLOBAL_CFG.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.lstrip().startswith("#"):
            continue
        mm = re.match(r'\s*(\w+)\s*=\s*"?([^"\n]*)"?\s*$', ln)
        if mm and mm.group(1) in wanted:
            result[mm.group(1)] = mm.group(2)  # last occurrence wins (as RA reads it)
    return result


# System-hotkey mouse-button keys — a non-nul value = a hotkey is bound to a mouse
# button (e.g. the X-Arcade red button). Mirrors the "System hotkeys" group in
# madsrv/retroarch_cmds.py (the _mbtn variant of each).
RA_HOTKEY_MBTN_KEYS = (
    "input_enable_hotkey_mbtn", "input_menu_toggle_mbtn", "input_exit_emulator_mbtn",
    "input_save_state_mbtn", "input_load_state_mbtn", "input_toggle_fast_forward_mbtn",
    "input_rewind_mbtn", "input_screenshot_mbtn", "input_pause_toggle_mbtn",
    "input_state_slot_increase_mbtn", "input_state_slot_decrease_mbtn",
)


def ra_mouse_hotkey_bound() -> bool:
    """True if any RetroArch system hotkey is bound to a MOUSE button (non-nul *_mbtn)
    in the global cfg. The controller-router uses this to decide whether to pin
    player-1's mouse to the X-Arcade trackball (RA polls hotkeys on player-1's mouse
    only); the Preview page surfaces the resulting mouse assignment."""
    vals = get_global_options(list(RA_HOTKEY_MBTN_KEYS))
    return any(v not in (None, "", "nul") for v in vals.values())


def set_global_option(key: str, value: str) -> Path:
    """Set ONE key in the global retroarch.cfg in place, preserving every other
    line (the file is thousands of lines). Rewrites the LAST existing occurrence
    (so the effective value changes), or appends `key = "value"` if absent. Atomic;
    makes a one-time .mad-bak first. RetroArch must be CLOSED — it rewrites the
    whole file on exit."""
    text = (RA_GLOBAL_CFG.read_text(encoding="utf-8", errors="replace")
            if RA_GLOBAL_CFG.exists() else "")
    line = f'{key} = "{value}"'
    pat = re.compile(rf'^([^\S\n]*){re.escape(key)}[^\S\n]*=.*$', re.MULTILINE)
    matches = list(pat.finditer(text))
    if matches:
        m = matches[-1]                       # last wins, mirrors get_global_option
        new = text[:m.start()] + m.group(1) + line + text[m.end():]
    else:
        new = (text.rstrip("\n") + "\n" + line + "\n") if text else line + "\n"
    if new != text:
        _ensure_global_bak(text)
        _atomic_write(RA_GLOBAL_CFG, new)
    return RA_GLOBAL_CFG


if __name__ == "__main__":
    # Self-test: write, re-write (idempotent), then clear. Use a throwaway
    # path so we don't touch a real .cfg.
    import tempfile, sys
    tmpdir = Path(tempfile.mkdtemp(prefix="router-cfg-test-"))
    fake_core = tmpdir / "FakeCore"
    fake_core.mkdir()
    # Pretend a bezel-project file already exists
    existing_path = fake_core / "Test Game (USA).cfg"
    existing_path.write_text(
        "# bezelproject — auto-generated, safe to delete\n"
        "input_overlay = \"/path/to/overlay.cfg\"\n"
        "aspect_ratio_index = \"22\"\n"
    )

    # Monkey-patch the base path so write_override targets our tmp dir
    import lib.retroarch_cfg as rcfg
    rcfg.RA_CONFIG_BASE = tmpdir
    rcfg.SYSTEM_CORE_MAP = {"testsys": ["FakeCore"]}

    paths = rcfg.write_override("testsys", "Test Game (USA)", {
        1: "X-Arcade", 2: "DualSense",
    }, mouse_indices={1: 3, 2: 4})
    print(f"wrote {len(paths)} files")
    after = existing_path.read_text()
    print("--- after write ---")
    print(after)

    # Re-write should be idempotent
    rcfg.write_override("testsys", "Test Game (USA)", {
        1: "X-Arcade", 2: "DualSense",
    }, mouse_indices={1: 3, 2: 4})
    if existing_path.read_text() != after:
        sys.exit("FAIL: not idempotent")
    print("OK: idempotent")

    # Clear should strip our block and leave bezel content intact
    rcfg.clear_override("testsys", "Test Game (USA)")
    after_clear = existing_path.read_text()
    print("--- after clear ---")
    print(after_clear)
    assert "controller-router" not in after_clear
    assert "bezelproject" in after_clear
    assert "input_overlay" in after_clear
    print("OK: clear preserved bezel lines")

    # Cleanup
    import shutil
    shutil.rmtree(tmpdir)
