"""On-the-go handheld RetroArch HOTKEY combos (transient).

When the Deck is HANDHELD, the built-in pad drives RetroArch via the sdl2 joypad driver
(full, stable mapping -- flipped by controller-router._ra_handheld_driver). This module sets
the RetroArch HOTKEYS to Deck-pad gamepad COMBOS so rewind / fast-forward / slow-motion / menu
are reachable without the docked X-Arcade's hotkey buttons (which misfire on the Deck pad --
e.g. Start=rewind). WHY combos and not the paddles: RetroArch can't read Steam Input's
synthetic paddle KEYS without putting its input_driver on sdl2/x11, and that kills the sdl2
joypad. So we stay on the stable pad and use a modifier button + buttons instead.

Default scheme (sdl2 pad button indices from the "Steam Virtual Gamepad" profile; configurable
in [handheld.retroarch]): hold L3 (modifier) + L1=rewind / R1=fast-forward / Select=quick menu
/ R2-trigger=slow-motion. The hotkey buttons keep their normal gameplay function when L3 isn't
held (RetroArch's input_enable_hotkey gates them).

TRANSIENT: snapshot the resting value of every touched key to ONE sidecar, apply the handheld
values, restore on game-end (mirrors lib/deck_power.py). apply() sweeps a crash-orphan first.
Writes go through retroarch_cfg.set_global_option (atomic, one-time .mad-bak). RetroArch is
closed at game-start; every error degrades to "leave the config alone" so the launch continues.

Called by controller-router._setup (handheld + a real RA launch, same gated block as the joypad
flip) and ._cleanup (every game-end). Scheme read from [handheld.retroarch].
"""
from __future__ import annotations

import json
from pathlib import Path

SIDECAR = Path.home() / "Emulation" / "storage" / "controller-router" / ".mad-ra-hotkeys-restore"
# Last known DOCKED resting values of the keys we touch, refreshed by apply() at the one moment we
# provably have them (orphan swept + no sidecar = the config is at rest), and deliberately NOT
# deleted at game-end. Only restore()'s corrupt-sidecar path reads it.
#
# WHY NOT retroarch.cfg.mad-bak, which this path used until 2026-07-17: that file has a DIFFERENT
# job. retroarch_cfg._ensure_global_bak freezes it before MAD's FIRST edit and never rewrites it --
# it is the house-rule-#5 "never clobber user data without a recoverable copy" net, and it must stay
# frozen to be worth anything. Recovery wants the OPPOSITE: the CURRENT resting values. One file
# cannot be both, and using the frozen one as a live baseline is a slow-acting landmine: by
# 2026-07-17 it still held June's pre-6.16 d-pad (up=13 -> the rotation, see memory
# xarcade-dpad-kernel-flip-2026-07-17) and P2 unbound, so a recovery would have RESTORED a rotated
# stick and killed P2 -- silently, months after the values stopped being true.
BASELINE = SIDECAR.parent / ".mad-ra-resting-baseline"

# Policy field -> (retroarch.cfg key, shipped default). The defaults are the sdl2-pad indices for
# L3 (modifier) + L1/R1/Select and the R2 trigger axis.
_SCHEME = (
    ("modifier_btn",     "input_enable_hotkey_btn",     "7"),    # L3 (left stick click)
    ("rewind_btn",       "input_rewind_btn",            "9"),    # + L1 -> rewind (hold)
    ("fast_forward_btn", "input_hold_fast_forward_btn", "10"),   # + R1 -> fast-forward (hold)
    ("menu_btn",         "input_menu_toggle_btn",       "4"),    # + Select -> quick menu
    ("slowmotion_axis",  "input_toggle_slowmotion_axis", "+5"),  # + R2 trigger -> slow-mo (toggle)
    ("quit_btn",         "input_exit_emulator_btn",     "6"),    # + Start -> quit (WS-G)
)
# Keys we force to a fixed value handheld (not user-configurable): clear the pad slow-mo BUTTON
# (we use the trigger axis), disable the Start+Select menu combo (menu is L3+Select now), and
# clear any keyboard hotkey binds.
_FIXED = {
    "input_toggle_slowmotion_btn": "nul",
    "input_menu_toggle_gamepad_combo": "0",
    "input_rewind": "nul",
    "input_hold_fast_forward": "nul",
    "input_toggle_slowmotion": "nul",
}
# The Deck virtual pad's correct GAMEPLAY binds. RetroArch's sdl2 joypad driver keys this pad by
# SDL GameController SEMANTIC indices (a=0 b=1 x=2 y=3 back=4 start=6 L3=7 R3=8 L1=9 R1=10 dpad
# 11-14; axes leftx=0 lefty=1 rightx=2 righty=3 trigL=4 trigR=5). RetroArch uses the stale GLOBAL
# input_player1_* binds (leftover udev-driver values -> d-pad rotated, A/B + X/Y swapped, right
# stick on the wrong axes) INSTEAD of any autoconfig (manual binds win, per the RetroArch docs), so
# we set the right ones directly -- transient, docked X-Arcade binds restored on exit. These exact
# values are RetroArch's OWN capture (Set All Controls -> config/fbneo_libretro.cfg), not a guess.
_GAMEPAD = {
    "input_player1_a_btn": "0", "input_player1_b_btn": "1",
    "input_player1_x_btn": "2", "input_player1_y_btn": "3",
    "input_player1_select_btn": "4", "input_player1_start_btn": "6",
    "input_player1_l3_btn": "7", "input_player1_r3_btn": "8",
    "input_player1_l_btn": "9", "input_player1_r_btn": "10",
    "input_player1_up_btn": "11", "input_player1_down_btn": "12",
    "input_player1_left_btn": "13", "input_player1_right_btn": "14",
    "input_player1_l2_axis": "+4", "input_player1_r2_axis": "+5",
    "input_player1_l_x_plus_axis": "+0", "input_player1_l_x_minus_axis": "-0",
    "input_player1_l_y_plus_axis": "+1", "input_player1_l_y_minus_axis": "-1",
    "input_player1_r_x_plus_axis": "+2", "input_player1_r_x_minus_axis": "-2",
    "input_player1_r_y_plus_axis": "+3", "input_player1_r_y_minus_axis": "-3",
}
# Safe DOCKED-resting fallback for any touched key that was ABSENT at rest, so restore() still
# reverts what apply() appends. Buttons/axes/keys -> "nul" (unbound); the menu combo -> "0"
# (none). Used two ways: as the per-key default when a key is missing at snapshot time, and (for
# a corrupt sidecar) as the KEY SET to recover -- but recovery reads the real resting values from
# retroarch.cfg.mad-bak first and only falls back to these safe defaults for a key the backup
# lacks, so a corrupt sidecar never nul's the user's real gameplay binds.
# Optional P1 "settings" globals the Pad-mapping page can drive (device type + analog-to-D-pad).
# Unlike the binds above, these are WRITTEN only when the user opts in (see _handheld_settings) --
# their entries here are RetroArch's OWN defaults, used ONLY so restore() reverts a key that was
# ABSENT at rest (device=RetroPad, analog-dpad=off). Absent + not-in-new = never touched.
_SETTING_DEFAULTS = {
    "input_libretro_device_p1": "1",           # RETRO_DEVICE_JOYPAD (RetroArch default)
    "input_player1_analog_dpad_mode": "0",     # ANALOG_DPAD_NONE (off)
}
_SAFE_RESTING = {k: "nul" for _f, k, _d in _SCHEME}
_SAFE_RESTING.update({k: ("0" if k.endswith("_gamepad_combo") else "nul") for k in _FIXED})
_SAFE_RESTING.update({k: "nul" for k in _GAMEPAD})
_SAFE_RESTING.update(_SETTING_DEFAULTS)


# --- editable gameplay-pad overrides (WS-C) ---
# _GAMEPAD is the shipped Deck-pad gameplay map (RetroArch's own capture). MAD's "RetroArch
# (handheld) -> Pad mapping" page lets the user re-value these binds; edits go to a JSON sidecar
# that _handheld_values layers over _GAMEPAD at launch. INVARIANT: an override may only RE-VALUE an
# existing _GAMEPAD key, never add one -- apply() indexes _SAFE_RESTING[k] directly (outside the
# try), so an unknown key would KeyError and break the handheld launch. load/save filter to PAD_KEYS.
GAMEPAD_DEFAULTS = dict(_GAMEPAD)
PAD_KEYS = frozenset(_GAMEPAD)
# The only VALUES an override may hold: a real Deck-control token that appears somewhere in the
# shipped map (every valid sdl2 button index + axis token). This drops a hand-edited garbage value
# (e.g. "99", true) so it reverts to the shipped default instead of silently binding an out-of-range
# index (= that button UNBOUND handheld) while the page shows the default.
_VALID_PAD_VALUES = frozenset(_GAMEPAD.values())
PAD_OVERRIDES = SIDECAR.parent / ".mad-ra-handheld-pad-overrides.json"


def load_pad_overrides() -> dict:
    """User gameplay-pad overrides {retroarch_cfg_key: sdl2_value_str}, filtered to _GAMEPAD keys
    AND valid Deck-control values. {} on absent/corrupt. Read at launch by _handheld_values AND by
    the editor page. str(v) folds an int/bool to text; only a real token survives the value gate."""
    try:
        data = json.loads(PAD_OVERRIDES.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(v) for k, v in data.items()
            if k in PAD_KEYS and str(v) in _VALID_PAD_VALUES}


def save_pad_overrides(overrides: dict) -> None:
    """Atomic write of the pad-override sidecar (keys filtered to _GAMEPAD). An empty map DELETES
    the file == reset to shipped defaults. Best-effort (never raises into a caller)."""
    clean = {k: str(v) for k, v in (overrides or {}).items() if k in PAD_KEYS}
    try:
        PAD_OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
        if not clean:
            PAD_OVERRIDES.unlink(missing_ok=True)
            return
        tmp = PAD_OVERRIDES.with_suffix(PAD_OVERRIDES.suffix + ".tmp")
        tmp.write_text(json.dumps(clean, sort_keys=True))
        tmp.replace(PAD_OVERRIDES)
    except Exception:
        pass


# ── policy ───────────────────────────────────────────────────────────────────
def _load_policy() -> dict:
    try:
        from . import policy                     # package context (hooks use `from lib import`)
        return policy.load_merged()
    except Exception:
        return {}


def _dget(d, key, default=None):
    """dict.get that tolerates a non-dict (a malformed hand-edited TOML scalar)."""
    return d.get(key, default) if isinstance(d, dict) else default


def _ra_cfg() -> dict:
    hh = _dget(_load_policy(), "handheld", {})
    ra = hh.get("retroarch") if isinstance(hh, dict) else None
    return ra if isinstance(ra, dict) else {}


def _handheld() -> bool:
    hh = _dget(_load_policy(), "handheld", {})
    if not _dget(hh, "enabled", False):
        return False
    try:
        from . import deck_state
    except Exception:                            # pragma: no cover
        return False
    return deck_state.is_handheld(deck_state.resolve_force(hh if isinstance(hh, dict) else {}))


def _handheld_settings(ra: dict) -> dict:
    """The optional P1 device-type / analog-to-D-pad globals, from [handheld.retroarch]
    (device_p1 / analog_dpad_p1). Absent/'' -> INHERIT: the key is OMITTED so the resting global
    carries through untouched. A value is applied only if it is in the valid set (a garbage
    hand-edit is dropped -> inherit), so the transient write can never bind an out-of-range id."""
    try:
        from . import retroarch_rmp as _rmp
        # Pad-relevant device types only: the Deck's built-in controls are a gamepad, so a GLOBAL
        # handheld Light gun / Mouse is nonsense (and the editor no longer offers them). Dropping
        # them here means a stale stored id can never be applied at launch either.
        valid_dev = {str(v) for _l, v in _rmp.DEVICE_OPTIONS
                     if v not in (_rmp.DEVICE_LIGHTGUN, _rmp.DEVICE_MOUSE)}
        valid_adp = {str(i) for i in range(len(_rmp.ANALOG_DPAD_LABELS))}
    except Exception:                            # pragma: no cover
        return {}
    out = {}
    dev = str(_dget(ra, "device_p1", "") or "").strip()
    if dev in valid_dev:
        out["input_libretro_device_p1"] = dev
    adp = str(_dget(ra, "analog_dpad_p1", "") or "").strip()
    if adp in valid_adp:
        out["input_player1_analog_dpad_mode"] = adp
    return out


def _handheld_values(ra: dict) -> dict:
    new = {k: str(_dget(ra, field, dflt) or dflt) for field, k, dflt in _SCHEME}
    new.update(_FIXED)
    new.update(_GAMEPAD)                 # shipped gameplay defaults
    new.update(load_pad_overrides())     # user gameplay edits win (filtered to _GAMEPAD keys)
    new.update(_handheld_settings(ra))   # optional P1 device-type / analog-to-D-pad (only when set)
    return new


def _atomic_write_sidecar(text: str) -> None:
    SIDECAR.parent.mkdir(parents=True, exist_ok=True)
    tmp = SIDECAR.with_suffix(SIDECAR.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(SIDECAR)


def _write_baseline(snap: dict) -> None:
    """Record the docked resting values for corrupt-sidecar recovery. Best-effort:
    a failure here must never stop a launch -- recovery just falls back a level."""
    try:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        tmp = BASELINE.with_suffix(BASELINE.suffix + ".tmp")
        tmp.write_text(json.dumps(snap))
        tmp.replace(BASELINE)
    except Exception:
        pass


def _baseline_values(keys) -> dict:
    """{key: value|None} from the refreshed baseline; {} on absent/corrupt/garbage.
    Only str values survive, so a hand-edited file cannot feed set_global_option a
    non-str and abort the sweep."""
    try:
        data = json.loads(BASELINE.read_text())
        assert isinstance(data, dict)
    except Exception:
        return {}
    return {k: data[k] for k in keys if isinstance(data.get(k), str)}


# ── restore ──────────────────────────────────────────────────────────────────
def restore(logger=None) -> bool:
    """Restore each snapshotted RetroArch hotkey key from the sidecar and drop it. No-op (False)
    when no sidecar exists (docked play never created one). Keeps the sidecar if a write is
    rejected, so the next sweep retries."""
    if not SIDECAR.exists():
        return False
    try:
        from . import retroarch_cfg
    except Exception:                            # pragma: no cover
        return False
    try:
        snap = json.loads(SIDECAR.read_text())
        assert isinstance(snap, dict)
    except Exception:
        # A corrupt sidecar can't be trusted, but nul'ing the gameplay binds would kill the docked
        # pad AND a later apply() would re-baseline that nul'd state -> permanent dataloss of the
        # user's real input_player1_* binds. So restore each touched key from the best DOCKED
        # resting values we have, newest first:
        #   1. BASELINE  -- refreshed by apply() every handheld launch: tracks the user's own
        #      rebinds and any kernel change that moves what a bind MEANS.
        #   2. retroarch.cfg.mad-bak -- the pre-MAD freeze. A LAST resort, and only because it
        #      beats nul'ing: it is a snapshot of one moment that may be years old (on 2026-07-17
        #      it still held June's pre-6.16 d-pad, which would have restored a ROTATED stick).
        #      Reached only when the user has never launched RA handheld since this shipped.
        #   3. the safe default -- hotkeys/combos only; a gameplay bind is SKIPPED, never nul'd.
        # Then drop the sidecar so the next handheld launch re-applies cleanly. Extremely rare given
        # the atomic tmp+replace writer -- this path is external tamper / filesystem damage only.
        base = _baseline_values(list(_SAFE_RESTING))
        bak = retroarch_cfg.read_global_bak_options(list(_SAFE_RESTING))
        if logger:
            src = "the refreshed resting baseline" if base else "retroarch.cfg.mad-bak (pre-MAD freeze; may be stale)"
            logger.warning(f"ra_handheld_input: corrupt sidecar; restoring touched keys from {src}")
        for k, dflt in _SAFE_RESTING.items():
            v = base.get(k)
            if not isinstance(v, str):
                v = bak.get(k)
            if not isinstance(v, str):
                if k in _GAMEPAD:
                    continue                     # no resting value -> never nul a real gameplay bind
                v = dflt                         # a hotkey/combo key is safe to reset to its default
            try:
                retroarch_cfg.set_global_option(k, v)
            except Exception:
                pass
        SIDECAR.unlink(missing_ok=True)
        return False
    ok = True
    for k, v in snap.items():
        if not isinstance(v, str):
            continue
        try:
            retroarch_cfg.set_global_option(k, v)
        except Exception as ex:
            if logger:
                logger.warning(f"ra_handheld_input: restore {k} failed ({ex!r}); keeping sidecar")
            ok = False
    if not ok:
        return False                             # keep sidecar for the next sweep
    SIDECAR.unlink(missing_ok=True)
    if logger:
        logger.info("ra_handheld_input: restored resting RetroArch hotkeys")
    return True


# ── apply ────────────────────────────────────────────────────────────────────
def apply(logger=None) -> str:
    """At a handheld RetroArch game-start: sweep any crash-orphaned profile back to resting, then
    (handheld) snapshot the resting hotkey keys and apply the Deck-pad combo scheme. Docked /
    feature-off -> sweep only. Returns a status string for the launch log."""
    restore(logger)                              # sweep any crash orphan back to resting first
    if SIDECAR.exists():                         # restore() couldn't consume it -> don't clobber
        msg = "leftover sidecar survived; leaving RetroArch hotkeys untouched"
        if logger:
            logger.warning(f"ra_handheld_input: {msg}")
        return msg
    if not _handheld():
        return "docked -> no RA hotkey combos"
    try:
        from . import retroarch_cfg
    except Exception:                            # pragma: no cover
        return "retroarch_cfg unavailable"

    new = _handheld_values(_ra_cfg())
    # Snapshot the resting value of every key we will write; a key ABSENT at rest (None) is
    # recorded as its safe docked default so restore() still reverts what apply() appends.
    snap = {k: (v if isinstance(v, str) else _SAFE_RESTING[k])
            for k, v in retroarch_cfg.get_global_options(list(new)).items()}
    if all(snap.get(k) == v for k, v in new.items()):
        return "RA hotkey combos already applied"
    try:
        _atomic_write_sidecar(json.dumps(snap))
        # Refresh the corrupt-sidecar baseline HERE and nowhere else: restore() has just swept any
        # orphan and no sidecar survived, so `snap` IS the docked resting truth. This is the whole
        # fix for the stale-baseline landmine -- the values now track the user instead of being
        # frozen at MAD's first edit. Before the sets below, which make the config non-resting.
        _write_baseline(snap)
        for k, v in new.items():
            retroarch_cfg.set_global_option(k, v)
    except Exception as ex:
        if logger:
            logger.warning(f"ra_handheld_input: apply failed ({ex!r})")
        return f"apply failed: {ex!r}"
    if logger:
        logger.info("ra_handheld_input: handheld RA hotkey combos applied "
                    f"(modifier={new.get('input_enable_hotkey_btn')} rewind={new.get('input_rewind_btn')} "
                    f"ffwd={new.get('input_hold_fast_forward_btn')} menu={new.get('input_menu_toggle_btn')} "
                    f"slowmo={new.get('input_toggle_slowmotion_axis')})")
    return "handheld RA hotkey combos applied"
