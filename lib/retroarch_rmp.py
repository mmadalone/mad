"""
Native RetroArch per-game input remap (.rmp) writer.

RetroArch's OWN per-game input remap mechanism — NOT a cfg block. A remap file
lives at

    <RA config>/remaps/<Core Display Name>/<rom_basename>.rmp

(docs.libretro.com/guides/overrides/, 2026-07-02: "Input remaps use the same
logic as core/directory/game overrides and use the .rmp extension" and are
saved under "/config/remaps/<name-of-core>/" by default). RA fully OWNS and
round-trips this file (Quick Menu "Save Game Remap File" rewrites it whole) —
there is no comment/sentinel convention to splice, unlike the shared
`<Core>/<rom>.cfg` retroarch_cfg.py writes into. So MAD owns the WHOLE `.rmp`
for a game it manages: `set_game_remap` REPLACES the file's full content from
`mapping`, never merges lines into an existing one.

Key format and the RETRO_DEVICE_ID_JOYPAD_*/RETRO_DEVICE_* constants below are
CONFIRMED two ways (2026-07-02), not guessed:
  * libretro.h (github.com/libretro/RetroArch, master) — the numeric #defines.
  * RetroArch's OWN writer, input_remapping_save_file() in configuration.c
    (github.com/libretro/RetroArch/blob/master/configuration.c, ~line 7278):
    the `key_strings[RARCH_ANALOG_BIND_LIST_END][8]` table ("b","y","select",
    "start","up","down","left","right","a","x","l","r","l2","r2","l3","r3", ...
    stick suffixes) — index == the RETRO_DEVICE_ID_JOYPAD_* value, and each
    saved key is "input_player<N>_btn_<key_strings[j]>". Cross-checked against
    27 REAL .rmp files already on this Deck under
    ~/.var/app/org.libretro.RetroArch/config/retroarch/config/remaps/ (e.g.
    Mesen/Duck Hunt (World).rmp: "input_player1_btn_a = \"0\"" — 0 = B, matches
    key_strings[0] == "b"... i.e. the CORE's slot named "a" is remapped to read
    RetroPad button id 0 (B)). input_libretro_device_pN / *_analog_dpad_mode /
    input_remap_port_pN keys and their flat (no [section]) INI shape are
    likewise taken straight from those live files, not invented.

Reuses retroarch_cfg's core-dir/core-name resolution (core_dirs_for_system,
the SYSTEM_CORE_MAP + derived-from-ES-DE-commands union) — re-rooted under
`remaps/` instead of the config-override tree — and its atomic_write pattern
via lib.fsutil.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from . import fsutil
from . import retroarch_cfg

# ── libretro RetroPad button ids (RETRO_DEVICE_ID_JOYPAD_*, libretro.h) ──────
# Index IS the numeric id AND the .rmp key-suffix order RetroArch itself
# writes (configuration.c's key_strings[] table — see module docstring).
BUTTON_NAMES = ["b", "y", "select", "start", "up", "down", "left", "right",
                "a", "x", "l", "r", "l2", "r2", "l3", "r3"]
BUTTON_LABELS = ["B", "Y", "Select", "Start", "D-pad Up", "D-pad Down",
                  "D-pad Left", "D-pad Right", "A", "X", "L", "R",
                  "L2", "R2", "L3", "R3"]

# ── libretro base device types (RETRO_DEVICE_*, libretro.h) ─────────────────
DEVICE_NONE = 0
DEVICE_JOYPAD = 1
DEVICE_MOUSE = 2
DEVICE_KEYBOARD = 3
DEVICE_LIGHTGUN = 4
DEVICE_ANALOG = 5

# The 5 offered on the per-game Device selector (spec-picked subset — Keyboard
# is a real RETRO_DEVICE_* value but not a controller remap target, so it's
# left out of this UI list; the raw constant above stays available if needed).
DEVICE_OPTIONS = [("RetroPad", DEVICE_JOYPAD), ("Analog", DEVICE_ANALOG),
                  ("Light gun", DEVICE_LIGHTGUN), ("Mouse", DEVICE_MOUSE),
                  ("None", DEVICE_NONE)]


def core_remap_dirs_for_system(system: str) -> list[Path]:
    """Remap-tree dirs (config/remaps/<Core Name>/) for a system's REAL cores.
    Reuses retroarch_cfg.core_dirs_for_system for the core-NAME resolution (a
    core's CONFIG dir existing on disk is what proves the core is real) and
    re-roots each name under `<RA config>/remaps/` — read live via
    retroarch_cfg.RA_CONFIG_BASE on every call (not cached at import time) so
    tests can monkeypatch it exactly like retroarch_cfg's own tests do. The
    remaps/<Core> dir itself need not pre-exist; set_game_remap creates it
    (mkdir -p, via fsutil's atomic writer) on first save."""
    base = retroarch_cfg.RA_CONFIG_BASE / "remaps"
    return [base / core_dir.name for core_dir in retroarch_cfg.core_dirs_for_system(system)]


def _rmp_path(core_dir: Path, rom_basename: str) -> Path:
    return core_dir / f"{rom_basename}.rmp"


# Flat "key = "value"" INI, no [section] headers — matches every .rmp on disk.
_KV_RE = re.compile(r'^[ \t]*([A-Za-z0-9_+\-]+)[ \t]*=[ \t]*"?([^"\r\n]*)"?[ \t]*$')


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _KV_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _render(mapping: dict[str, str]) -> str:
    return "".join(f'{k} = "{v}"\n' for k, v in sorted(mapping.items()))


def _ensure_backed_up(target: Path) -> None:
    """One-time MOVE of a pre-existing, not-yet-MAD-managed `.rmp` to a
    recoverable _TMP (rule #5) before MAD's first write to this exact path.

    A .rmp carries no sentinel (RA fully round-trips the whole file), so there
    is no way to tell "MAD's own previous write" apart from "a foreign file"
    by content alone — a zero-byte sibling `.mad-managed` marker records that
    MAD now owns this path; its absence is what "first managed write" means."""
    marker = target.with_name(target.name + ".mad-managed")
    if marker.exists():
        return
    if target.exists():
        tmp = fsutil.recoverable_delete(
            target, tmp_base=Path.home() / "Downloads" / "_TMP", tag="retroarch-rmp",
            recovery_note=(
                f"MAD is taking over per-game RetroArch input-remap management for "
                f"{target.name}. The pre-existing file (hand-made, or saved earlier "
                "from RetroArch's own Quick Menu) was moved here first so nothing is "
                "lost. Restore: move it back to its original path if you want the "
                "old remap instead of MAD's per-game input editor."))
        print(f"retroarch_rmp: moved pre-existing {target} to {tmp} before MAD "
              "took over its per-game input remap", file=sys.stderr)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")


def set_game_remap(system: str, stem: str, mapping: dict[str, str]) -> list[Path]:
    """Write/replace the WHOLE `<stem>.rmp` across every core dir the system
    resolves to. MAD owns the whole file for a game it manages, so `mapping`
    is the COMPLETE desired key set (not a delta) — any key not present in it
    is simply absent from the written file. An empty mapping means "no
    per-game remap" and removes the file. On the first write to a given path
    (no `.mad-managed` marker yet), a real pre-existing file is moved to a
    recoverable _TMP first (see `_ensure_backed_up`) — never clobbered.
    Returns the paths touched (written or removed)."""
    touched: list[Path] = []
    for core_dir in core_remap_dirs_for_system(system):
        target = _rmp_path(core_dir, stem)
        if not mapping and not target.exists():
            continue                                   # nothing to manage, nothing to touch
        _ensure_backed_up(target)
        if mapping:
            fsutil.atomic_write_text(target, _render(mapping))
            touched.append(target)
        else:                                          # empty mapping = clear this game's remap
            if target.exists():                        # _ensure_backed_up may have moved it away
                target.unlink()
                touched.append(target)
            # drop the ownership marker too, so the path returns to fully
            # unmanaged and any future foreign .rmp is backed up on the next write
            target.with_name(target.name + ".mad-managed").unlink(missing_ok=True)
    return touched


def get_game_remap(system: str, stem: str) -> dict[str, str]:
    """The `.rmp` key/value map for a game (from the first core dir whose
    remap file exists). {} if none is set."""
    for core_dir in core_remap_dirs_for_system(system):
        target = _rmp_path(core_dir, stem)
        if target.is_file():
            return _parse(target.read_text(encoding="utf-8", errors="replace"))
    return {}


def has_game_remap(system: str, stem: str) -> bool:
    """True if any core dir carries a non-empty `.rmp` for the game."""
    return bool(get_game_remap(system, stem))


if __name__ == "__main__":
    # Self-test: write, re-write (idempotent, no marker churn), a pre-existing
    # foreign file gets backed up ONCE, then clearing removes the file.
    import shutil
    import tempfile

    tmpdir = Path(tempfile.mkdtemp(prefix="retroarch-rmp-test-"))
    fake_core = tmpdir / "FakeCore"
    fake_core.mkdir()

    saved = (retroarch_cfg.RA_CONFIG_BASE, retroarch_cfg.SYSTEM_CORE_MAP)
    retroarch_cfg.RA_CONFIG_BASE = tmpdir
    retroarch_cfg.SYSTEM_CORE_MAP = {"testsys": ["FakeCore"]}

    # A foreign (hand-made / RA Quick-Menu-saved) file already exists, at the
    # REAL remap path (<RA_CONFIG_BASE>/remaps/<Core>/<rom>.rmp) — NOT under
    # the core's own config dir (that's the separate <Core>/<rom>.cfg tree).
    target = tmpdir / "remaps" / "FakeCore" / "Test Game (USA).rmp"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('input_player1_btn_a = "1"\n', encoding="utf-8")

    tmp_base = tmpdir / "tmp_base"
    orig_recoverable_delete = fsutil.recoverable_delete

    def _patched(*a, **kw):
        kw["tmp_base"] = tmp_base
        return orig_recoverable_delete(*a, **kw)

    fsutil.recoverable_delete = _patched
    try:
        written = set_game_remap("testsys", "Test Game (USA)", {
            "input_player1_btn_a": "0", "input_libretro_device_p1": "1"})
        assert len(written) == 1, written
        moved = list(tmp_base.glob("_TMP_retroarch-rmp-*/*.rmp"))
        assert len(moved) == 1 and moved[0].read_text() == 'input_player1_btn_a = "1"\n', \
            "pre-existing file wasn't preserved byte-for-byte"
        print("OK: pre-existing foreign file backed up, not clobbered")

        got = get_game_remap("testsys", "Test Game (USA)")
        assert got == {"input_player1_btn_a": "0", "input_libretro_device_p1": "1"}, got
        assert has_game_remap("testsys", "Test Game (USA)")
        print("OK: round-trip write -> read")

        before = target.read_text()
        set_game_remap("testsys", "Test Game (USA)", {
            "input_player1_btn_a": "0", "input_libretro_device_p1": "1"})
        assert target.read_text() == before
        assert len(list(tmp_base.glob("_TMP_retroarch-rmp-*"))) == 1, \
            "a second identical write re-triggered the backup"
        print("OK: idempotent re-write, no repeat backup")

        # Multi-core system -> multi-write.
        retroarch_cfg.SYSTEM_CORE_MAP = {"testsys": ["FakeCore", "FakeCore2"]}
        (tmpdir / "FakeCore2").mkdir()
        written2 = set_game_remap("testsys", "Multi Game", {"input_player1_btn_a": "8"})
        assert len(written2) == 2, written2
        print("OK: multi-core system writes to every core dir")

        # Empty mapping removes the file.
        set_game_remap("testsys", "Test Game (USA)", {})
        assert not target.exists()
        assert get_game_remap("testsys", "Test Game (USA)") == {}
        print("OK: empty mapping removes the file")
    finally:
        fsutil.recoverable_delete = orig_recoverable_delete
        retroarch_cfg.RA_CONFIG_BASE, retroarch_cfg.SYSTEM_CORE_MAP = saved
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("retroarch_rmp self-test OK")
