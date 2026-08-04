"""The PER-GAME input STORE for RPCS3, keyed by disc SERIAL (no RPC pages of its own).

The store (``~/Emulation/storage/rpcs3/pergame-input.json``) carries each game's input
intent, read by the launch rail (lib/switch_bind.py) at game start and applied to the
resting input config transiently (snapshotted + reverted on exit):

  profiles          {"docked": <stem>, "handheld": <stem>} — the per-game input-profile
                    picks, EDITED by lib/madsrv/rpcs3_profile_cmds (rpcs3profpg /
                    rpcs3profpghh); resolved per context by lib/rpcs3_profiles.resolve
  binds             LEGACY per-button remaps (context-keyed {context: {player: {key:
                    token}}}; an even older flat {player: {...}} folds under docked):
                    tolerated + counted in _is_empty so an old entry is never auto-pruned
                    (never destroy user data), but NO LONGER read at launch — input
                    profiles superseded the per-button editors (2026-08-04).

RPCS3 has no native per-game NAMED-profile pick (a per-title custom config is a fixed
``input_configs/<SERIAL>/Default.yml`` file), so per-game intent lives in THIS store and
the launch binder applies it to the ladder-resolved target transiently.

This module owns the store file — other modules import its _load/_save/_LOCK/
_normalize_entry/_is_empty/_has_input_override helpers. There is NO EBUSY guard by design:
the store is decoupled from RPCS3's live config and applied at the game's next launch.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import threading

from .. import mad_paths
from . import cfgutil
from .rpc import RpcError

_STORE = mad_paths.storage("rpcs3", "pergame-input.json")
_SERIAL_RE = re.compile(r"^[A-Z]{4}[0-9]{5}\Z")
_LOCK = threading.Lock()


# ── store ─────────────────────────────────────────────────────────────────────
def _load() -> dict:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except OSError:
        return {}
    except ValueError:
        # Corrupt store (external / hand edit): preserve it for recovery rather than silently
        # overwriting every other game's overrides on the next save (rule #5: never destroy data).
        # Name the backup by a content hash so EACH DISTINCT corruption is preserved (not just the
        # first) and the same corruption isn't re-copied on every load.
        try:
            import hashlib
            digest = hashlib.sha1(_STORE.read_bytes()).hexdigest()[:8]
            bad = _STORE.with_name(f"{_STORE.name}.{digest}.bad")
            if not bad.exists():
                shutil.copy2(_STORE, bad)
                print(f"rpcs3pgin: {_STORE.name} corrupt; backed up to {bad.name}, starting fresh",
                      file=sys.stderr)
        except OSError:
            pass
        return {}


def _save(data: dict) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    cfgutil.atomic_write(_STORE, json.dumps(data, indent=2, sort_keys=True))


# ── entry shape ───────────────────────────────────────────────────────────────
_CONTEXTS = ("docked", "handheld")
_ENTRY_KEYS = {"binds", "profiles"}


def _is_context_keyed(binds) -> bool:
    """True if `binds` is context-keyed (every top-level key is a context). Empty counts as
    new; a legacy flat map keyed by player number ("1", "2", ...) is False."""
    return isinstance(binds, dict) and all(k in _CONTEXTS for k in binds)


def _profiles(e) -> dict:
    """The entry's valid per-context input-profile picks ({context: stem}), husk-tolerant
    (a non-dict `profiles` or non-string/blank stems read as absent). See rpcs3_profile_cmds."""
    if not isinstance(e, dict):
        return {}
    profiles = e.get("profiles")
    if not isinstance(profiles, dict):
        return {}
    return {c: v.strip() for c, v in profiles.items()
            if c in _CONTEXTS and isinstance(v, str) and v.strip()}


def _legacy_binds(e) -> dict:
    """Every context's LEGACY per-player binds from entry["binds"], normalised to
    {context: {player_str: {key: token}}}. STRUCTURAL clean only (non-dict / empty cruft
    dropped) — token-level validation retired with the per-button editors; the launch path no
    longer reads binds, this exists for emptiness checks so an old entry is never auto-pruned."""
    if not isinstance(e, dict):
        return {}
    binds = e.get("binds")
    if not isinstance(binds, dict):
        return {}
    if _is_context_keyed(binds):
        out = {}
        for c in _CONTEXTS:
            s = binds.get(c)
            if isinstance(s, dict):
                clean = {p: v for p, v in s.items() if isinstance(v, dict) and v}
                if clean:
                    out[c] = clean
        return out
    clean = {p: v for p, v in binds.items() if isinstance(v, dict) and v}
    return {"docked": clean} if clean else {}


def _normalize_entry(entry) -> dict:
    """An entry in the NEW shape ({"binds": ..., "profiles": ...}), migrating the legacy
    shapes losslessly: a pre-migration entry (context-keyed or flat per-player binds at the
    TOP level) folds under "binds"; a corrupt non-dict `profiles` husk is healed away.
    Returns a fresh dict (never the caller's)."""
    if not isinstance(entry, dict):
        return {}
    if set(entry) <= _ENTRY_KEYS:
        out: dict = {}
        binds = _legacy_binds(entry)
        if binds:
            out["binds"] = binds
        profiles = _profiles(entry)
        if profiles:
            out["profiles"] = profiles
        return out
    # Legacy top-level entry: every key is a context (or a player number, older still) — the
    # same shapes _legacy_binds normalises under "binds". Structural fold (no token
    # validation — that retired with the editors).
    slices = _legacy_binds({"binds": entry})
    return {"binds": slices} if slices else {}


def _is_empty(e: dict) -> bool:
    """True when the entry carries neither profile picks nor legacy binds. Legacy binds KEEP
    an entry alive (never destroy user data) even though the launch path ignores them."""
    return not _legacy_binds(e) and not _profiles(e)


def _has_input_override(e: dict) -> bool:
    """True if the entry carries a per-game INPUT override — the 'Custom input' badge on the
    per-game picker. Only PROFILE picks badge: legacy `binds` no longer apply at launch
    (superseded by input profiles), so advertising them would claim a remap that isn't in
    effect. They still count in _is_empty so an old entry is never auto-pruned."""
    return bool(_profiles(e))


def load_entry(serial) -> dict | None:
    """The per-game input entry for one disc serial in the NEW shape, or None. Public: the
    launch rail (lib/switch_bind) feeds it to rpcs3_profiles.resolve (only the "profiles"
    picks matter there; legacy binds are inert)."""
    if not serial or not isinstance(serial, str) or not _SERIAL_RE.match(serial):
        return None
    e = _load().get(serial)
    if not isinstance(e, dict):
        return None
    return _normalize_entry(e) or None


def _serial(params) -> str:
    s = params.get("titleid") or ""
    if not _SERIAL_RE.match(s):
        raise RpcError("EINVAL", f"bad game id {s!r}")
    return s


def _titleid(params) -> str:
    """Param-validated serial (name-parity with pcsx2_pergame_input_cmds._titleid, so the
    profile pages can mirror the PS2 code path)."""
    return _serial(params)
