"""Apply / restore a handheld TDP watt cap for the on-the-go feature.

The cap is a GLOBAL hardware setting (the amdgpu package power limit), so it does
not fit the per-config-file `.mad-restore` rail the input/resolution writers use.
Instead we snapshot the resting cap to ONE sidecar and restore from it, with a
crash-safe sweep for launches that die before the game-end hook runs.

Privilege: power1_cap is root-owned. We write it through Valve's whitelisted
`steamos-priv-write` helper (no password / sudoers / setuid; part of the base
image, so it survives SteamOS updates). After that helper's first write of a boot
the node becomes group-writable by 'deck', so we try a direct write first and fall
back to the helper. A reboot re-creates the cap at its default, so a stuck-low cap
cannot survive a reboot even if every software layer failed.

Lifecycle (driven by the game-start / game-end hooks):
  apply <system> : sweep any orphan, then (feature on + handheld) snapshot the resting
                   cap and lower it to the global default watt cap -- or, for an enabled
                   per-system entry, its override. Docked / feature disabled -> sweep
                   only, no cap.
  restore        : restore from the sidecar and delete it (sidecar-gated no-op).
  sweep          : restore an orphan left by a crashed launch (same as restore).
This module writes ONLY the power cap; it deliberately does not touch the DPM
performance level (left at whatever Steam/the user set).
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
from pathlib import Path

PRIV = "/usr/bin/steamos-polkit-helpers/steamos-priv-write"
SIDECAR = Path.home() / "Emulation" / "storage" / "controller-router" / ".mad-power-restore"
_KEYS = ("power1_cap", "power2_cap")     # slowPPT (sustained) + fastPPT (boost)
_FLOOR_UW = 4_000_000                     # never cap below 4 W (self-floor; node min is 0)


# ── sysfs helpers ────────────────────────────────────────────────────────────
def _amdgpu_hwmon() -> str | None:
    """The hwmonN dir owned by amdgpu (hwmonN is not boot-stable; resolve by name)."""
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            with open(os.path.join(d, "name")) as f:
                if f.read().strip() == "amdgpu":
                    return d
        except OSError:
            pass
    return None


def _read_int(path: str) -> int | None:
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _atomic_write(path: Path, text: str) -> None:
    """Write the sidecar atomically so a crash mid-write can never leave a partial
    (unrestorable) sidecar — the file is either the complete old one or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _restore_value(raw) -> int | None:
    """Sanitize a sidecar cap value: a positive int, floored so a corrupted tiny
    value can never drive the cap toward 0. None if unparseable (skip that key)."""
    try:
        return max(_FLOOR_UW, int(str(raw).strip()))
    except (TypeError, ValueError):
        return None


def _write(path: str, value) -> bool:
    """Direct write (node is group-writable after the first priv-write of a boot);
    fall back to Valve's polkit helper, which always works and re-chmods the node."""
    try:
        with open(path, "w") as f:
            f.write(str(value))
        return True
    except OSError:
        pass
    if os.path.exists(PRIV):
        try:
            r = subprocess.run([PRIV, path, str(value)],
                               capture_output=True, text=True, timeout=15)
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    return False


# ── apply / restore ──────────────────────────────────────────────────────────
def _apply(watts: int) -> tuple[bool, str]:
    d = _amdgpu_hwmon()
    if not d:
        return False, "no amdgpu hwmon found"
    p1 = os.path.join(d, "power1_cap")
    cur1 = _read_int(p1)
    if cur1 is None:
        return False, "cannot read power1_cap"
    default1 = _read_int(os.path.join(d, "power1_cap_default")) or cur1
    # Only ever LOWER, never above stock; self-floor so we cannot brick the APU.
    target = max(_FLOOR_UW, min(int(watts) * 1_000_000, default1))
    if target >= cur1:
        return False, f"no downshift needed (target {target/1e6:.1f}W >= now {cur1/1e6:.1f}W)"

    # Snapshot the RESTING cap once (a prior sweep guarantees no stale sidecar).
    if not SIDECAR.exists():
        lines = []
        for k in _KEYS:
            v = _read_int(os.path.join(d, k))
            if v is not None:
                lines.append(f"{k}={v}")
        _atomic_write(SIDECAR, "\n".join(lines) + "\n")

    ok = _write(p1, target)
    # Clamp boost (power2_cap) to the same ceiling so it cannot spike past the cap.
    p2 = os.path.join(d, "power2_cap")
    cur2 = _read_int(p2)
    if cur2 is not None and target < cur2:
        _write(p2, target)
    return ok, f"cap {target/1e6:.1f}W (was {cur1/1e6:.1f}W){'' if ok else ' [WRITE FAILED]'}"


def restore() -> tuple[bool, str]:
    """Restore the snapshotted resting cap. The sidecar is removed ONLY when the
    writes actually succeed (or there was nothing writable) — if the hwmon node is
    missing or a write is rejected, the sidecar is KEPT so the next sweep retries.
    An orphaned low cap must never become permanent. No-op if no sidecar."""
    if not SIDECAR.exists():
        return False, "no sidecar (nothing to restore)"
    d = _amdgpu_hwmon()
    if not d:
        return False, "amdgpu hwmon not found; keeping sidecar for a later sweep"
    vals: dict[str, str] = {}
    for line in SIDECAR.read_text().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip()
    ok_all = True
    for k in _KEYS:
        if k not in vals:
            continue
        p = os.path.join(d, k)
        if not os.path.exists(p):
            continue
        v = _restore_value(vals[k])
        if v is None:                            # unparseable -> nothing sane to write
            continue
        if not _write(p, v):                     # hardware rejected -> retry on next sweep
            ok_all = False
    if not ok_all:
        return False, f"restore write failed; keeping sidecar ({vals})"
    SIDECAR.unlink(missing_ok=True)
    return True, f"restored {vals}"


def _status() -> str:
    d = _amdgpu_hwmon()
    if not d:
        return "no amdgpu hwmon"
    cur = _read_int(os.path.join(d, "power1_cap"))
    dfl = _read_int(os.path.join(d, "power1_cap_default"))
    sc = "sidecar:present" if SIDECAR.exists() else "sidecar:none"
    return f"power1_cap={(cur or 0)/1e6:.1f}W default={(dfl or 0)/1e6:.1f}W {sc}"


# ── policy-driven entry point (called by the game-start hook) ────────────────
def _load_policy() -> dict:
    try:
        from . import policy                    # package context (hooks use `from lib import`)
        return policy.load_merged()
    except Exception:
        return {}


def _dget(d, key, default=None):
    """dict.get that tolerates a non-dict (a malformed hand-edited TOML scalar)."""
    return d.get(key, default) if isinstance(d, dict) else default


def _coerce_watts(raw, fallback: int = 12) -> int:
    """Parse a policy watt value that may be int, float, or a stray string; fall back
    to a sane default rather than aborting the cap on a bad hand-edit."""
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return fallback


def _cli_apply(system: str) -> str:
    # Crash-sweep FIRST, unconditionally, before touching the policy — so an orphan
    # cap from a prior crashed launch is cleared even if the policy is malformed.
    restore()
    pol = _load_policy()
    hh = pol.get("handheld") if isinstance(pol, dict) else {}
    if not _dget(hh, "enabled", False):
        return "handheld feature disabled"

    try:
        from . import deck_state
    except ImportError:                          # pragma: no cover
        import deck_state                         # type: ignore
    if not deck_state.is_handheld(deck_state.resolve_force(hh)):
        return "docked -> no cap"

    systems = pol.get("systems") if isinstance(pol, dict) else {}
    sys_hh = _dget(_dget(systems, system, {}), "handheld", {})
    default_cap = _dget(hh, "default_watt_cap", 12)
    # The global default watt cap applies to EVERY handheld launch (battery), so a system that is not
    # in the Per-system list still gets it. An ENABLED per-system entry overrides it with its own
    # watt_cap (or the default, if it left the cap inherited).
    if _dget(sys_hh, "enabled", False):
        watts = _coerce_watts(_dget(sys_hh, "watt_cap", default_cap))
    else:
        watts = _coerce_watts(default_cap)
    _ok, msg = _apply(watts)
    return f"{system} handheld: {msg}"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "status"
    if cmd == "apply":
        print(_cli_apply(argv[1] if len(argv) > 1 else ""))
        return 0
    if cmd == "restore":
        print(restore()[1])
        return 0
    if cmd == "sweep":
        ok, msg = restore()
        print(msg if ok else "no orphan")
        return 0
    if cmd == "status":
        print(_status())
        return 0
    print("usage: deck_power.py [apply <system>|restore|sweep|status]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
