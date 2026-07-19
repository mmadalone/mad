"""mugen_res - on-the-go handheld render-resolution downshift for MUGEN / Ikemen GO.

Ikemen renders at [Video] GameWidth x GameHeight; lowering that in HANDHELD saves battery
(gamescope upscales to the panel). Game aspect ratios differ (4:3 motifs vs 16:9), so this
downshifts by a SCALE PERCENT of the game's OWN resting size (aspect preserved), NOT to a
fixed absolute resolution -- unlike the multiplier rail in lib/handheld_res (which snaps an
emulator's internal-res knob). MUGEN therefore deliberately stays OUT of that rail.

Store (controller-policy, .local-overridable):
  general : [systems.mugen.handheld].res            = full|high|medium|low
  per-game: [backends.mugen.pergame.<folder>].hhres = full|high|medium|low  (overrides general)
Keyed by the game's CONFIG FOLDER name (what mugen.sh has at launch). Gated on the master
on-the-go feature ([handheld].enabled) AND a physical/forced handheld.

apply() snapshots the resting GameWidth/Height to a sidecar next to config.ini and writes the
downshift; restore() puts the resting size back (the engine rewrites config.ini with the
downshift on exit, so restore runs AFTER it quits). A leftover sidecar from a crashed launch
is swept on the next apply(), so a downshift can never stick into the docked resting value.

CLI (mugen.sh):
    python3 -m lib.mugen_res apply   <folder> <path-to-save/config.ini>
    python3 -m lib.mugen_res restore <path-to-save/config.ini>
    python3 -m lib.mugen_res effective <folder>      # print the percent (diagnostics)
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from .madsrv import cfgutil
    from . import deck_state
    from .policy import load_merged
except ImportError:                       # run as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lib.madsrv import cfgutil
    from lib import deck_state
    from lib.policy import load_merged

# scale token -> render-size percent of the game's resting GameWidth/Height
PCT = {"full": 100, "high": 80, "medium": 65, "low": 50}
_SIDE = ".mad-hhres-restore"      # sits in save/, next to config.ini


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "on", "yes")


def _dig(d, *keys):
    for k in keys:
        d = d.get(k, {}) if isinstance(d, dict) else {}
    return d


def effective(folder: str) -> int:
    """Scale percent (100 = no downshift) for this game right now: per-game hhres over the
    general res, but only when the on-the-go feature is on AND we are handheld."""
    pol = load_merged() if isinstance(load_merged(), dict) else {}
    hh = pol.get("handheld", {}) if isinstance(pol.get("handheld"), dict) else {}
    if not _truthy(hh.get("enabled", False)):
        return 100
    if not deck_state.is_handheld(deck_state.resolve_force(hh)):
        return 100
    pg = _dig(pol, "backends", "mugen", "pergame", folder)
    tok = (pg.get("hhres") if isinstance(pg, dict) else None) \
        or _dig(pol, "systems", "mugen", "handheld").get("res")
    return PCT.get(str(tok).strip().lower(), 100)


def _read_gwh(text):
    try:
        return (int(cfgutil.ini_read(text, "Video", "GameWidth")),
                int(cfgutil.ini_read(text, "Video", "GameHeight")))
    except (TypeError, ValueError):
        return None


def scale_dims(gw: int, gh: int, pct: int):
    """The downshifted (GameWidth, GameHeight) for a scale percent -- even values, aspect
    preserved. The ONE place this math lives, so the on-the-go picker's per-game labels can
    show exactly what apply() will write (a label can never disagree with the real render size)."""
    return (max(2, (gw * pct // 100 + 1) // 2 * 2),
            max(2, (gh * pct // 100 + 1) // 2 * 2))


def resting_dims(config_ini: Path):
    """(GameWidth, GameHeight) resting size from a game's config.ini, or None if it has no
    config yet / is unreadable. Used to label the per-game handheld-resolution picker."""
    text = cfgutil.read_text(config_ini)
    return _read_gwh(text) if text is not None else None


def _write_gwh(config_ini: Path, gw, gh) -> None:
    text = cfgutil.read_text(config_ini)
    if text is None:
        return
    for k, v in (("GameWidth", gw), ("GameHeight", gh)):
        nt = cfgutil.ini_replace(text, "Video", k, str(int(v)))
        if nt is not None:
            text = nt
    cfgutil.atomic_write(config_ini, text)


def restore(config_ini: Path) -> str:
    side = config_ini.parent / _SIDE
    if not side.is_file():
        return "no downshift to restore"
    try:
        gw, gh = side.read_text().split()[:2]
        int(gw), int(gh)
    except Exception:
        side.unlink(missing_ok=True)
        return "bad sidecar - dropped"
    _write_gwh(config_ini, gw, gh)
    side.unlink(missing_ok=True)
    return f"restored {gw}x{gh}"


def apply(folder: str, config_ini: Path) -> str:
    # crash-sweep any leftover downshift first, so we snapshot a PRISTINE resting size
    if (config_ini.parent / _SIDE).is_file():
        restore(config_ini)
    pct = effective(folder)
    if pct >= 100:
        return "no downshift (docked / off / full)"
    text = cfgutil.read_text(config_ini)
    if text is None:
        return "skip - no config.ini"
    gwh = _read_gwh(text)
    if not gwh:
        return "skip - no GameWidth/Height"
    gw, gh = gwh
    ngw, ngh = scale_dims(gw, gh, pct)             # even, aspect preserved
    (config_ini.parent / _SIDE).write_text(f"{gw} {gh}\n")   # snapshot the resting size
    _write_gwh(config_ini, ngw, ngh)
    return f"downshift {gw}x{gh} -> {ngw}x{ngh} ({pct}%)"


def main(argv) -> int:
    if len(argv) >= 2 and argv[0] == "apply":
        cfg = Path(argv[2]) if len(argv) > 2 else Path("save/config.ini")
        print(apply(argv[1], cfg))
        return 0
    if len(argv) >= 2 and argv[0] == "restore":
        print(restore(Path(argv[1])))
        return 0
    if len(argv) >= 2 and argv[0] == "effective":
        print(effective(argv[1]))
        return 0
    print("usage: mugen_res apply <folder> <config.ini> | restore <config.ini> "
          "| effective <folder>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
