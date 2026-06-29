"""Controller-routing resolution — policy load/merge, inherits chains, device
pins and per-port priority resolution.

Moved VERBATIM from controller-router.py (MAD native-panel phase 0, R1) so the
mad-backend daemon can run the router's REAL resolution logic read-only for the
Preview page (ending the old GUI-side duplicate that ignored pins). The router
imports these names back — it stays the game-launch entry point forever.

Daemon-safety change vs the original: no process-lifetime caches. The old
`_XARCADE_PORT_CACHE` assumed policy is static per launch; a long-lived daemon
would miss a re-identify. State is explicit instead: callers load the policy
once per operation and pass `xport` (the identified X-Arcade USB port) through.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Optional

from .devices import (Device, pin_id, pin_kind, port_of,
                      usb_iface_num)

_LAUNCHERS = Path(__file__).resolve().parent.parent     # lib/.. = launchers dir
POLICY_FILE = _LAUNCHERS / "controller-policy.toml"
# Machine-written overrides from the config GUI. Deep-merged over the (commented,
# human-edited) defaults so the GUI never has to mangle the documented file.
LOCAL_POLICY_FILE = _LAUNCHERS / "controller-policy.local.toml"


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------

def deep_merge(base: dict, over: dict) -> dict:
    """Recursively merge `over` into `base` (override wins); dict values merge,
    everything else (lists, scalars) is replaced wholesale. Returns base."""
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_policy() -> dict:
    base: dict = {"systems": {}}
    if POLICY_FILE.is_file():
        try:
            with POLICY_FILE.open("rb") as f:
                base = tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError) as exc:
            # A broken BASE file must never abort a launch (the LOCAL parse below
            # is already fail-soft). base keeps {"systems": {}} → every system
            # resolves to None → the game launches un-routed (RetroArch defaults).
            print(f"controller-router: controller-policy.toml parse error "
                  f"({exc}); routing disabled (RetroArch defaults).",
                  file=sys.stderr)
    if LOCAL_POLICY_FILE.is_file():
        try:
            with LOCAL_POLICY_FILE.open("rb") as f:
                deep_merge(base, tomllib.load(f))
        except (tomllib.TOMLDecodeError, OSError):
            pass   # a broken local file must never break routing
    return base


def resolve_system(policy: dict, key: str) -> Optional[dict]:
    """Look up a system entry, resolving its full `inherits` chain (parent-most
    first, each child overriding its parent). Guards against cycles (A→B→A) and
    undefined/non-string parents — both are surfaced on stderr (never stdout,
    which the sdl-ignore callers capture) and the chain is truncated at the bad
    link so a misconfigured policy degrades gracefully instead of looping."""
    systems = policy.get("systems", {})
    if key not in systems:
        return None
    chain: list[str] = []          # child-most -> parent-most
    seen: set[str] = set()
    cur = key
    referrer = None                # the entry whose `inherits` pointed at `cur`
    while cur is not None:
        if not isinstance(cur, str):
            print(f"controller-router: [systems.{referrer}] inherits a non-string "
                  f"value {cur!r} — ignoring inherit.", file=sys.stderr)
            break
        if cur in seen:
            print(f"controller-router: circular inherits detected — "
                  f"{' -> '.join(chain)} -> {cur} — breaking the cycle.",
                  file=sys.stderr)
            break
        entry = systems.get(cur)
        if entry is None:
            print(f"controller-router: [systems.{referrer}] inherits "
                  f"'{cur}', which is not defined — ignoring inherit.",
                  file=sys.stderr)
            break
        if not isinstance(entry, dict):              # scalar [systems.x] (e.g. nes = "arcade"):
            print(f"controller-router: [systems.{cur}] is {type(entry).__name__}, not a "
                  f"table; ignoring it.", file=sys.stderr)   # degrade, never crash the launch
            break
        seen.add(cur)
        chain.append(cur)
        referrer = cur
        cur = entry.get("inherits")
    merged: dict = {}
    for name in reversed(chain):                       # parent-most first; child wins
        merged.update({k: v for k, v in systems[name].items() if k != "inherits"})
    return merged


def resolve_policy(policy: dict, system: str,
                   collection: Optional[str] = None) -> Optional[dict]:
    """The policy entry that governs this launch. A `[collections.<name>]` rule
    for the matched enabled collection WINS over the launched system's policy
    (e.g. a Duck Hunt launch from NES routes by the lightgun collection). If the
    collection has no rule, fall through to the system's `[systems.<name>]`."""
    if collection:
        ent = policy.get("collections", {}).get(collection)
        if ent is not None:
            # A collection rule may `inherits` a system's config — resolve it
            # (parity with systems, which resolve their full chain in
            # resolve_system); the collection's own keys override the parent.
            if "inherits" in ent:
                parent = resolve_system(policy, ent["inherits"]) or {}
                merged = dict(parent)
                merged.update({k: v for k, v in ent.items() if k != "inherits"})
                return merged
            return ent
    return resolve_system(policy, system)


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------

def pin_items(pins: dict):
    """Yield (int player, str tagged-pin_id) from the global [pins] table,
    skipping malformed keys/values and out-of-range player numbers."""
    for k, v in (pins or {}).items():
        try:
            p = int(k)
        except (ValueError, TypeError):
            continue
        if 1 <= p <= 16:                     # valid player slot (RA supports up to 8/16)
            yield p, str(v)


def resolve_pins(pins: dict, devs: list[Device]) -> tuple[dict[int, Device], set[str]]:
    """Resolve the GLOBAL [pins] table (player -> tagged pin_id, e.g.
    'uniq:054c:0ce6:<mac>') against connected pads. Returns ({port: Device},
    claimed_paths). The first unclaimed pad whose `pin_id` matches the key wins.
    Claiming: a `uniq:`/`port:` key claims every node sharing that key (the whole
    physical unit / that one interface); a `vidpid:` key (ambiguous model-only)
    claims ONLY the matched node, so a second model-only pin can still resolve to
    a different device. A pin whose pad isn't connected is skipped silently.
    Sinden guns are never matched here (their PID udev pinning is separate)."""
    assigned: dict[int, Device] = {}
    claimed: set[str] = set()
    for player, key in sorted(pin_items(pins)):
        match = next((d for d in devs
                      if d.is_joypad and not d.is_sinden and not d.is_steam_virtual and not d.is_mad_virtual
                      and d.path not in claimed and pin_id(d) == key), None)
        if match is None:
            continue
        assigned[player] = match
        if pin_kind(key) == "vidpid":
            claimed.add(match.path)          # ambiguous model — claim only this node
        else:
            for d in devs:                   # uniq → whole unit; port → that interface
                if d.is_joypad and not d.is_sinden and pin_id(d) == key:
                    claimed.add(d.path)
    return assigned, claimed


def resolve_ports(ports: list[list[str]], devs: list[Device],
                  with_fallback: bool = True,
                  preassigned: Optional[dict[int, Device]] = None,
                  preclaimed: Optional[set[str]] = None,
                  xport: str = "") -> dict[int, Device]:
    """For each port (1-indexed), walk its priority-list substrings, find the
    FIRST present joypad whose name contains one (and isn't yet claimed by an
    earlier port), and assign THAT device to the port. Policy tokens are only
    used to *pick* the device here. Returns {port: Device}; the caller derives
    the RetroArch reserve-value (`reserve_value`) and any device-specific
    binds (`device_binds.binds_for`) from the chosen Device.

    `xport` is the identified X-Arcade USB port ([hardware].xarcade_port; pass
    `xarcade_port(policy)`) — "" means no X-Arcade identified, matching the old
    lazy `_xarcade_port()` behavior.

    Claim tracking matters when the user has e.g. one 8BitDo + one DualSense
    launching a NES game with policy ["8BitDo", "DualSense"]: P1 takes the
    8BitDo, then P2 must fall through to "DualSense" because the only 8BitDo
    is gone. Without claim-tracking, both ports would reserve "8BitDo" and
    P2 would end up unassigned (RetroArch's first-match-not-yet-assigned
    logic only fires when MULTIPLE devices match the same reserved name).

    When TWO 8BitDo pads are present, claim-tracking picks both distinct
    devices; each port reserves its own `reserve_value` (identical vid:pid for
    same-model pads) and RetroArch's sequential cascade pairs them to P1/P2.
    """
    claimed: set[str] = set(preclaimed or ())   # paths already taken (incl. global pins)
    # Only honor pins for ports this system actually has — a global P3 pin is a
    # no-op on a 2-player game (don't write input_playerN beyond the port count).
    out: dict[int, Device] = {p: d for p, d in (preassigned or {}).items()
                              if 1 <= p <= len(ports)}
    for i, priority in enumerate(ports, start=1):
        if i in out:                            # pinned → keep it, skip token resolution
            continue
        for substr in priority:
            # Exclude Steam-virtual phantom pads (28de:11ff) so a token can't bind a
            # player to a shadow device ahead of the real one — matches the fallback path.
            tok = substr.strip().lower()
            if tok in ("x-arcade", "xarcade"):
                # The X-Arcade in Xbox mode enumerates as "Xbox 360 Wireless Receiver"
                # (045e:02a1), so a NAME substring search for "x-arcade" finds nothing and
                # the port falls through to the DualSense. Resolve it the port-aware way the
                # MAD GUI already uses (is_xarcade), so the stick actually lands on P1/P2.
                hits = [d for d in devs
                        if d.is_joypad and not d.is_steam_virtual and not d.is_mad_virtual and is_xarcade(d, xport)]
                # Order the two cab sides by parent USB interface (00 < 01) — stable
                # across replugs, unlike enumeration (= event-node) order. Makes the
                # router's pick per port deterministic (preview/logs/per-device binds,
                # standalone backends). NOTE: for RetroArch the in-game side still
                # follows RA's own cascade — both sides share one vid:pid reserve
                # value, and index-pinning was tried + reverted (#37, note below).
                hits.sort(key=xa_iface_rank)
            else:
                # A real 045e Xbox pad still matches "Xbox"; the X-Arcade must NOT — it is
                # owned solely by the "X-Arcade" token above (mirrors the GUI's class split).
                xbox_tok = tok in ("xbox", "x-box")
                # Match by device NAME substring (back-compat) OR by family classification
                # (family_of), so a pad whose name lacks its family word — e.g. a DS4 that
                # enumerates as "Wireless Controller" — is still picked by its family token.
                hits = [d for d in devs
                        if d.is_joypad and not d.is_steam_virtual and not d.is_mad_virtual
                        and not (xbox_tok and is_xarcade(d, xport))
                        and (tok in d.name.lower()
                             or _family_token(d) == tok
                             or (xbox_tok and _family_token(d) == "xbox"))]
            # First not-yet-claimed match wins this port
            for d in hits:
                if d.path not in claimed:
                    out[i] = d
                    claimed.add(d.path)
                    break
            if i in out:
                break

    # ── fallback ──
    # Standalone backends (Cemu) want STRICT matching — only pads named in the
    # policy tokens, never a catch-all — so the empty-port falls through to the
    # backend's own handheld/none handling. RetroArch keeps the rescue below.
    if not with_fallback:
        return out
    # If a port matched no policy token but a real (non-Sinden) gamepad is
    # connected, bind the next unclaimed one so the port never lands on
    # RetroArch's "N/A" (an unassigned reserved/empty port). This rescues the
    # case observed post-reboot: only the X-Arcade was connected, but it had
    # not yet enumerated as "Xbox 360 Wireless Receiver" (it appeared only as a
    # Steam virtual "Microsoft X-Box 360 pad"), so no token matched and the
    # router wrote nothing → P1 = N/A. Writing a best-guess token is never
    # worse than N/A: if RetroArch's SDL2 name doesn't contain it, the port is
    # left unassigned exactly as it would have been without us.
    real_pads = [d for d in devs if d.is_joypad and not d.is_sinden and not d.is_steam_virtual and not d.is_mad_virtual]
    for i in range(1, len(ports) + 1):
        if i in out:
            continue
        for d in real_pads:
            if d.path in claimed:
                continue
            if fallback_token(d) is None:
                continue
            out[i] = d
            claimed.add(d.path)
            break
    return out


def reserve_value(d: Device) -> str:
    """The exact string RetroArch's reservation matcher will accept for `d`.

    RetroArch (PR libretro/RetroArch#16647, in the user's 1.22.2) does NOT
    substring-match `input_playerN_reserved_device`. Its matcher first tries
    `sscanf("%04x:%04x ", ...)`; on success it compares vid:pid, otherwise it
    falls back to `string_is_equal()` (EXACT full device name). A bare token
    like "Xbox" is neither a vid:pid nor the exact name, so it NEVER matches —
    the reserved port stays empty (RetroArch "N/A") while the real pad spills
    into a later port. Verified live 2026-05-29 in the udev-driver verbose log.

    We emit the canonical form RetroArch itself writes — "<vid>:<pid> <name>" —
    which matches by vid:pid (robust to name variants) and dual-assigns two
    physically-identical pads (same vid:pid) to two same-reserved ports via
    RetroArch's sequential cascade. The trailing name is informational; sscanf
    stops after the two hex fields."""
    return f"{d.vid:04x}:{d.pid:04x} {d.name}"


# The X-Arcade Tankstick in Xbox mode enumerates as "Xbox 360 Wireless Receiver"
# (USB vid 045e, two interfaces) — BYTE-IDENTICAL to a real Xbox 360 pad, so it
# can't be told apart by vid:pid or name. It's identified ONLY by the user-set USB
# port ([hardware].xarcade_port) — see is_xarcade below.

def xarcade_port(policy: dict) -> str:
    """Configured USB port of the X-Arcade ([hardware].xarcade_port), or "" if
    unset. Read from an already-loaded policy dict (no caching — daemon-safe)."""
    try:
        return str(policy.get("hardware", {}).get("xarcade_port", "") or "")
    except Exception:
        return ""


def is_xarcade(d: Device, xport: str) -> bool:
    # The X-Arcade in Xbox mode is 045e:02a1 — IDENTICAL to a real Xbox 360 pad.
    # A pad is the X-Arcade ONLY when it's a 045e device at the user-IDENTIFIED USB
    # port ([hardware].xarcade_port). Not identified → it's just an Xbox-looking pad
    # (safe default: never assume an 045e is the stick). Set via MAD's Preview
    # "Identify X-Arcade"; re-cabling the stick → re-identify.
    return bool(xport) and d.vid == 0x045e and port_of(d.phys) == xport


def xa_iface_rank(d: Device) -> int:
    """Sort key for the cab's two gamepad sides: parent USB interface number (00 < 01),
    the only identity that survives replug/re-enumeration (their name/vid:pid/phys/uniq
    are byte-identical — see lib.devices.usb_iface_num). Unknown → 99 so non-resolving
    nodes keep enumeration order under the stable sort."""
    n = usb_iface_num(d.path)
    return 99 if n is None else n


# Steam Input's virtual gamepad (vid 28de, pid 11ff) — appears as
# "Microsoft X-Box 360 pad N". It's never a real "plug in a better controller"
# alternative: it's either the X-Arcade wrapped by Steam Input, or the Deck's
# built-in pad surfaced through Steam Input. For the only-X-Arcade warning it
# must NOT count as a present real gamepad (it masked the warning in test 2,
# 2026-05-29 — the dialog never showed because the phantom looked like a pad).
def is_steam_virtual_pad(d: Device) -> bool:
    return d.vid == 0x28de and d.pid == 0x11ff


# Sony product ids by model. DualShock 4 (PS4) and DualSense (PS5) share vendor
# 0x054c but are SEPARATE controller families here, so they can be ordered as
# distinct players in the priority list. Anything else under 054c (DualSense
# Edge, DS3, future Sony pads) defaults to "DualSense" — the historical catch-all.
_DS4_PIDS = frozenset({0x05c4, 0x09cc, 0x0ba0})   # DS4 v1, DS4 v2, DS4 USB wireless adapter


def family_of(d: Device) -> Optional[str]:
    """Canonical controller-family name for `d` (8BitDo / DualSense / DualShock 4
    / Xbox), or None for a pad we can't confidently map. Used both to gate the
    fallback reservation (`fallback_token`) and, in `resolve_ports`, to match a
    priority token to a pad by vendor:product id — so a DS4 that enumerates with
    a generic name (e.g. "Wireless Controller") is still recognised by family."""
    n = d.name.lower()
    if "8bitdo" in n:
        return "8BitDo"
    if d.vid == 0x054c:
        if d.pid in _DS4_PIDS or "dualshock" in n:
            return "DualShock 4"
        return "DualSense"                         # 0ce6 / Edge / unknown Sony
    if "dualshock" in n:
        return "DualShock 4"
    if "dualsense" in n:
        return "DualSense"
    # X-Arcade (raw "Xbox 360 Wireless Receiver") or a Steam-virtual
    # "Microsoft X-Box 360 pad" standing in for it: RetroArch shows the
    # X-Arcade as "X-Arcade Xbox 360 wireless controller", so "Xbox" matches.
    if d.vid == 0x045e or "xbox" in n or "x-box" in n:
        return "Xbox"
    return None


def _family_token(d: Device) -> str:
    """`family_of(d)` lowercased for comparison against a priority token, or ""
    when the pad is unclassified (so it can never equal a real token)."""
    fam = family_of(d)
    return fam.lower() if fam else ""


def fallback_token(d: Device) -> Optional[str]:
    """Gate: is `d` a pad we can confidently reserve as a fallback? Returns a
    non-None family marker for pads we recognize, or None for ones we can't map
    — in which case we leave the port to RetroArch's own autoconfig instead of
    pinning it. The actual value written is `reserve_value(d)`, not this marker."""
    return family_of(d)


def only_xarcade_present(devs: list[Device], xport: str) -> bool:
    # Real gamepads only — the Sinden guns classify as joypads (they expose
    # gamepad-style buttons + an absolute axis) but are not controllers anyone
    # plays a console game with, so they must not defeat the all() test.
    pads = [d for d in devs if d.is_joypad and not d.is_sinden]
    if not pads:
        return False
    # A Steam-virtual pad is the X-Arcade/Deck wrapped by Steam Input, not a
    # real alternative controller — treat it as "X-Arcade" so it doesn't defeat
    # the warning when only the stick (+ its Steam-virtual shadow) is present.
    return all(is_xarcade(d, xport) or is_steam_virtual_pad(d) for d in pads)


def xarcade_present(devs: list[Device], xport: str) -> bool:
    """True if ANY connected real gamepad is the X-Arcade — the inverse signal
    used by the arcade 'no X-Arcade detected' warning."""
    return any(is_xarcade(d, xport) for d in devs if d.is_joypad and not d.is_sinden)
