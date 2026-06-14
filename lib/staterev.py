"""
staterev.py — named monotonic "revision" counters for the MAD backend.

The native panel rebuilds a page (and re-fires its RPCs) on every section
switch, so an expensive method like ``preview.all`` (~10 s: SDL init + Wiimote
probe + per-system route resolution) re-ran every time the user came back to a
page that hadn't changed. The fix is a revision-invalidated response cache
(see ``lib/madsrv/rpc.py``): a cached method returns its previous result until
one of the *named pieces of state it depends on* actually changes.

This module is just those named counters. A counter is bumped at the point
where the underlying state changes:

  * ``"config"``  — any router-policy / per-emulator-settings write
    (``lib.localpolicy.dump``, ``lib.fsutil.atomic_write_text``).
  * ``"devices"`` — the connected-input set changed (the hotplug watch in
    ``lib.madsrv.device_cmds``).
  * ``"bezels"``  — a Bezel Project pack was installed / removed / reset.

A method declares which counters it depends on; the cache compares the counter
values it saw when it stored a result against the current values, and recomputes
only if any advanced. Over-bumping is always SAFE — the worst case is one extra
(correct) recompute — so writers bump liberally at a chokepoint rather than
trying to decide whether a given write is "relevant".

Deliberately dependency-free (only ``threading``): the low-level writers
``fsutil``/``localpolicy`` import it, so it must never import ``madsrv``/``rpc``
or anything that would create a cycle. Pure in-process state; resets to zero
when the daemon restarts (each MAD open = a fresh daemon = a cold cache, by
design).
"""
from __future__ import annotations

import threading

_LOCK = threading.Lock()
_REVS: dict[str, int] = {}


def bump(key: str) -> int:
    """Record that the state named by ``key`` changed; returns the new value.
    Safe to call from any thread (worker pool, watch thread, inline dispatch)."""
    with _LOCK:
        v = _REVS.get(key, 0) + 1
        _REVS[key] = v
        return v


def get(key: str) -> int:
    """Current revision of ``key`` (0 if it has never been bumped)."""
    with _LOCK:
        return _REVS.get(key, 0)


def snapshot(keys) -> dict[str, int]:
    """{key: current rev} for ``keys`` — the stamp a cache stores alongside a
    result and later compares against to decide whether to recompute. Taken
    atomically so a concurrent bump can't split the snapshot across keys."""
    with _LOCK:
        return {k: _REVS.get(k, 0) for k in keys}
