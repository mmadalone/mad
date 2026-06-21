"""lib/install_conf.py — read/write MAD's install.conf (the installer picker + the MAD
"Sidebar" page's single source of truth). Plain shell KEY=VALUE; the shell twin that
install.sh / deck-post-update.sh source is lib/install-conf.sh.

The backend uses this to read FORCE_SHOW_*/FORCE_HIDE_* sidebar overrides and to write
them (the Sidebar page's sidebar.set). Tiny hand parser — no shell spawned, stdlib only,
so it works even when other deps are wiped. Writes go through fsutil.atomic_write_text
(which also bumps staterev "config", so cached panel data refreshes).

INVARIANT mirrored from the shell want(): an ABSENT install.conf means "do everything"
(want() -> True), so existing setups are unaffected.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import fsutil

_LINE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')
_TRUE = {"1", "on", "yes", "true", "auto"}


def _conf_path() -> Path:
    """install.conf lives in the launchers repo root (lib/ is one level down).
    $MAD_INSTALL_CONF overrides (tests)."""
    env = os.environ.get("MAD_INSTALL_CONF")
    return Path(env) if env else Path(__file__).resolve().parent.parent / "install.conf"


def _clean(raw: str) -> str:
    """Match shell sourcing: drop a trailing ' # inline comment', then strip matching
    surrounding quotes."""
    v = re.sub(r'\s+#.*$', '', raw).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


def load(path: Path | None = None) -> dict:
    """Parse install.conf into {KEY: value}. Missing file -> {}."""
    p = path or _conf_path()
    out: dict[str, str] = {}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        m = _LINE.match(line)
        if m:
            out[m.group(1)] = _clean(m.group(2))
    return out


def want(key: str, conf: dict | None = None) -> bool:
    """Mirror of the shell want(): an absent install.conf file => True (do everything)."""
    if conf is None:
        if not _conf_path().exists():
            return True
        conf = load()
    return conf.get(key, "").strip().lower() in _TRUE


def get(key: str, default: str = "", conf: dict | None = None) -> str:
    conf = load() if conf is None else conf
    return conf.get(key, default)


def set_value(key: str, value: str, path: Path | None = None) -> None:
    """Set KEY=value, PRESERVING every other line + comment (so the picker's keys and
    the panel's FORCE_* keys coexist). Appends if the key is absent; creates the file if
    needed. Atomic."""
    p = path or _conf_path()
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    pat = re.compile(r'^\s*' + re.escape(key) + r'\s*=')
    for i, line in enumerate(lines):
        if pat.match(line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    fsutil.atomic_write_text(p, "\n".join(lines) + "\n")
