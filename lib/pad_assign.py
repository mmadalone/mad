"""
Shared 4-way pad-assignment pipeline for the SDL-by-class controller backends
(pcsx2 / xemu / eden / rpcs3).

All four `*_cfg.assign()` functions ran the SAME five-phase pipeline and differed
only in (a) how a chosen device becomes the value written to disk and (b) the
collision rule when a pin lands on a slot an auto-pick already filled. This module
owns the pipeline; each backend injects its encoders + collision unit-count.

    slot map = assign_slots(sdl, manage, pins, devs,
                            pad_classes=…, handheld=…,
                            encode_auto=…, encode_pin=…,
                            unit_count=…, rank_key=…, base_index=…)

Phases:
  1. prio/ps   — PlayStation-class pads in `pad_classes` priority, then SDL index.
  2. pins      — resolve {port: evdev Device} to {slot: value} via `encode_pin`.
  3. auto fill — first `manage` `ps` pads → slots via `encode_auto` (+ per-group rank).
  4. handheld  — ps empty & no pins → bind slot 0 to the Deck, else leave untouched.
  5. collide   — pins win their slots; drop a NON-pinned slot only when the pinned
                 value's class has no spare physical unit (`unit_count`). With
                 unit_count==1 that is plain value-membership (eden/rpcs3/pcsx2);
                 xemu passes a per-class unit count so two identical pads on two
                 ports survive while a single pad can't phantom-duplicate.

Return: `{slot: value}` (slot keys in the backend's base — `base_index` 1 for
pcsx2/xemu/rpcs3, 0 for eden), or `None` for the leave-file-untouched case
(handheld wanted but no Deck present). An empty `{}` is distinct from `None`: it
means "write every managed slot as disconnected" (e.g. an out-of-range-only pin).

Two flags preserve pcsx2's historical quirks verbatim (NOT bugs in scope here —
only xemu's collision is being fixed):
  * `filter_pins_at_resolve=False` (pcsx2) — an over-`manage` pin still counts
    toward the handheld gate (so it suppresses the Deck fallback) even though it
    is never applied. xemu/eden/rpcs3 filter such pins out at resolve time.
  * `dedup_pins=True` (pcsx2) — pins are applied with an interleaved per-slot drop
    (pcsx2's original loop), so two players pinned to the SAME pad keep only the
    higher slot. eden/rpcs3 use the batch drop and keep both.
"""
from __future__ import annotations

from collections import Counter


def assign_slots(sdl, manage, pins, devs, *,
                 pad_classes, handheld,
                 encode_auto, encode_pin,
                 unit_count=lambda v: 1,
                 rank_key=None,
                 base_index=1,
                 filter_pins_at_resolve=True,
                 dedup_pins=False):
    """Compute the {slot: value} pad assignment. See module docstring."""
    manage = int(manage)

    # 1. PlayStation pads in priority order (pad_classes), then SDL index.
    prio = {c: i for i, c in enumerate(pad_classes)}
    ps = sorted((d for d in sdl if d.vidpid in prio),
                key=lambda d: (prio[d.vidpid], d.index))

    # 2. Resolve pins ({port: evdev Device}) to {slot: value}. `resolved` keeps
    #    EVERY resolvable pin (even out-of-range) so the pcsx2 gate quirk works;
    #    `pinned` is the in-range subset that is actually applied.
    resolved: dict[int, object] = {}     # slot (base_index + port-1) -> value
    if pins and devs:
        for port, pdev in pins.items():
            v = encode_pin(pdev, sdl, devs)
            if v is not None:
                resolved[base_index + (port - 1)] = v
    lo, hi = base_index, base_index + manage
    pinned = {s: v for s, v in resolved.items() if lo <= s < hi}
    gate_pins = pinned if filter_pins_at_resolve else resolved

    # 3. Auto-fill the first `manage` ps pads; 4. or the handheld fallback.
    assigned: dict[int, object] = {}     # slot -> value
    if ps:
        seen: dict[object, int] = {}
        for i in range(manage):
            if i < len(ps):
                d = ps[i]
                if rank_key is None:
                    rank = 0
                else:
                    key = rank_key(d)
                    rank = seen.get(key, 0)
                    seen[key] = rank + 1
                assigned[base_index + i] = encode_auto(d, rank)
    elif not gate_pins:
        deck = next((d for d in sdl if d.vidpid == handheld), None)
        if not handheld or deck is None:
            return None                  # leave the config file untouched
        assigned[base_index] = encode_auto(deck, 0)

    # 5. Apply pins (they win their slot) + drop colliding non-pinned slots.
    if dedup_pins:
        # pcsx2's original interleaved drop: each pin removes its value from every
        # other slot (auto OR earlier pin) before taking its own slot.
        for slot, v in sorted(pinned.items()):
            for k in [k for k, val in assigned.items() if val == v and k != slot]:
                del assigned[k]
            assigned[slot] = v
    else:
        for slot, v in sorted(pinned.items()):
            assigned[slot] = v
        for slot in _collision_drops(assigned, set(pinned), unit_count):
            del assigned[slot]

    return assigned


def _collision_drops(assigned, pinned_slots, unit_count):
    """Non-pinned slots to drop: keep an auto slot only while its value's class
    still has a spare physical unit after the pins claim theirs. With
    unit_count==1 this is value-membership (any pin/auto duplicate drops)."""
    pinned_count = Counter(assigned[s] for s in pinned_slots)
    remaining = {v: unit_count(v) - pinned_count.get(v, 0)
                 for v in set(assigned.values())}
    drop = set()
    for slot in sorted(assigned):
        if slot in pinned_slots:
            continue
        v = assigned[slot]
        if remaining.get(v, 0) > 0:
            remaining[v] -= 1            # a spare unit covers this auto slot
        else:
            drop.add(slot)
    return drop
