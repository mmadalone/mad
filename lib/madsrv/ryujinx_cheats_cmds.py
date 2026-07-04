"""ryujinx_cheats.* — Ryujinx (Switch) per-game cheat enable/disable.

Ryujinx cheats live at  <mods>/contents/<TitleId-UPPER>/cheats/<BuildId>.txt  as `[Cheat Name]`
bracketed headers. Enable state is a WHITELIST at  cheats/enabled.txt , one line per enabled cheat
in the form "<BUILDID-UPPER>-<CheatName>" (Ryujinx: buildId = the cheat file stem uppercased, name =
the [Section] header). If enabled.txt is ABSENT, every cheat is loaded but INERT (all off). There is
NO JSON index -- we enumerate cheats by parsing the *.txt files. (Primary user mods dir only;
sdcard/atmosphere cheats are not covered here.) Rendered per game by GuiMadPageEmuSettings; writes
refuse while Ryujinx runs. Row key: cheat:<buildid>:<name>.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import fsutil, proc_guard
from . import cfgutil, ryujinx_json
from .rpc import RpcError, method

_PROC = "ryujinx"
_TID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")
_HDR = re.compile(r"^\s*\[([^\]]+)\]\s*$")           # [Cheat Name] header line


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _tid(params) -> str:
    t = params.get("titleid") or ""
    if not _TID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


def _cheats_dir(tid: str) -> Path:
    # Computed from the live config path (test-friendly). Ryujinx keys the contents dir UPPERCASE.
    return Path(ryujinx_json.CONFIG).parent / "mods" / "contents" / tid.upper() / "cheats"


def _enabled_lines(cdir: Path) -> list[str]:
    try:
        txt = (cdir / "enabled.txt").read_text(encoding="utf-8")
    except OSError:
        return []
    # Keep each line VERBATIM (only the newline is dropped by splitlines); Ryujinx matches the
    # "<BUILDID>-<CheatName>" whitelist key EXACTLY, and a cheat name may carry significant inner
    # whitespace (e.g. "[ Moon Jump ]"). Skip only whitespace-only lines.
    return [ln for ln in txt.splitlines() if ln.strip()]


def _write_enabled(cdir: Path, lines: list[str]) -> None:
    cdir.mkdir(parents=True, exist_ok=True)
    path = cdir / "enabled.txt"
    cfgutil.ensure_bak(path)
    fsutil.atomic_write_text(path, ("\n".join(lines) + "\n") if lines else "")   # bumps staterev


def _cheat_names(txt: Path) -> list[str]:
    # Capture the bracket content VERBATIM (Ryujinx does line.Trim() then line[1..^1] -- it trims the
    # outer line but NOT inside the brackets, so " Moon Jump " keeps its inner spaces). _HDR allows
    # outer whitespace; m.group(1) is the inner content unstripped, matching Ryujinx's whitelist key.
    try:
        return [m.group(1) for line in
                txt.read_text(encoding="utf-8", errors="replace").splitlines()
                if (m := _HDR.match(line))]
    except OSError:
        return []


@method("ryujinx_cheats.get", slow=True)
def _get(params):
    tid = _tid(params)
    cdir = _cheats_dir(tid)
    enabled = set(_enabled_lines(cdir))
    rows = []
    try:
        txts = sorted(p for p in cdir.glob("*.txt") if p.name.lower() != "enabled.txt")
    except OSError:
        txts = []
    for txt in txts:
        bid = txt.stem.upper()                        # Ryujinx uppercases the cheat file stem
        for name in _cheat_names(txt):
            rows.append({"key": f"cheat:{bid}:{name}", "label": name, "type": "bool",
                         "value": f"{bid}-{name}" in enabled})
    note = ("Enable/disable cheats for this game. Off = disabled." if rows else
            "No cheats found. Put a <BuildId>.txt cheat file under "
            "mods/contents/<TitleId>/cheats/ to see them here.")
    return {"exists": True, "running": _running(), "note": note,
            "groups": [{"title": "Cheats", "note": "", "settings": rows}]}


@method("ryujinx_cheats.set", slow=True)
def _set(params):
    if _running():
        raise RpcError("EBUSY", "close Ryujinx first — it rewrites its config on exit.")
    _tid(params)
    key = params.get("key", "")
    if not key.startswith("cheat:") or key.count(":") < 2:
        raise RpcError("EINVAL", f"{key!r} is not a cheat toggle")
    _, bid, name = key.split(":", 2)
    on = str(params.get("value", "")).strip().lower() in cfgutil._TRUE
    cdir = _cheats_dir(_tid(params))
    lines = _enabled_lines(cdir)
    line = f"{bid}-{name}"
    if on and line not in lines:
        lines.append(line)
    elif not on:
        lines = [ln for ln in lines if ln != line]
    _write_enabled(cdir, lines)
    return {"key": key, "value": on}
