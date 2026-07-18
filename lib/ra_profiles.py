"""RetroArch input PROFILES: named, assigned to controller families, resolved per launch.

THE PROBLEM. RetroArch hotkeys are six raw numbers in the global retroarch.cfg, and RetroArch polls
hotkeys on ONE port (hotkey_port, 0 by default). Nothing re-points them at whoever is actually on
player 1, so a set tuned for the X-Arcade (modifier=6=Select) puts the modifier on L2 when a
DualSense takes P1 -- and rewind/fast-forward on 13/14, which the DualSense does not even have
(it reaches index 12). Measured on this rig 2026-07-17, both pads connected.

THE FIX. A profile stores SEMANTIC names ("l3", "left", "select"), never numbers. At launch the
seated pad's own base map turns them into that pad's numbers. One "Gamepad" profile is then correct
for a DualSense, a DualShock 4, an 8BitDo and an Xbox pad at once, and it cannot rot when a kernel
renumbers buttons.

    profile:  modifier = "select"        X-Arcade base:  select_btn = "6"    -> input_enable_hotkey_btn = "6"
              rewind   = "l2"            DualSense base: l2_axis    = "+2"   -> input_rewind_axis       = "+2"

WHY THE AUTOCONFIG IS THE DICTIONARY. RetroArch's per-device autoconfig already maps RetroPad names
to that device's tokens, and device_binds.binds_for() already reads it (the router copies those
binds onto the reserved port every launch). It is the semantic layer we needed, already present and
already correct. Do not build a second one.

THE DRIVER DECIDES WHAT A NUMBER MEANS, so the base map is keyed on it, never inferred from dock
state and never hardcoded (see retroarch_cfg.planned_joypad_driver, the ONE decision the router
acts on). Under udev a DualSense's L3 is button 11 and its d-pad is a hat; under sdl2 the same pad
reports SDL GameController semantic indices where 11 is d-pad UP. Same token, same pad, two
different numbers. An unrecognised driver writes NOTHING rather than guess a number space.

VERIFIED IN LIBRETRO SOURCE at v1.22.2 (our tag), not inferred from docs:
  * hotkeys DO work in a per-game override: config_load_override appends it into the same
    config_file_t and config_read_keybinds_conf parses the merged result over the FULL bind map,
    reading _btn, _axis and _mbtn. The only ident blocklist is on the SAVE path.
  * autoconfig is a per-bind FALLBACK, never a clobber:
    joykey = (binds[i].joykey != NO_BTN) ? binds[i].joykey : auto_binds[i].joykey
  * meta binds exist for user 0 ONLY, so hotkey keys carry no player prefix.
"""
from __future__ import annotations

import re
from typing import Optional

from . import device_binds
from .ra_deck_pad import _GAMEPAD

# --- the hotkey set (Miquel's call 2026-07-17: the five he named, plus quit) ---
# field -> RetroArch bind BASE name. Every one already exists in retroarch.cfg, which matters:
# on override UNLOAD RetroArch re-parses the base file, and a key absent there would KEEP its
# override value. Adding a hotkey here means adding it to retroarch.cfg too.
HOTKEYS: tuple[tuple[str, str], ...] = (
    ("modifier",     "input_enable_hotkey"),
    ("rewind",       "input_rewind"),
    ("fast_forward", "input_hold_fast_forward"),
    ("slowmotion",   "input_toggle_slowmotion"),
    ("menu",         "input_menu_toggle"),
    ("quit",         "input_exit_emulator"),
)
_HOTKEY_FIELDS = frozenset(f for f, _ in HOTKEYS)

# The sdl2 base map, DERIVED from ra_deck_pad._GAMEPAD so there is one source. Those values
# are RetroArch's OWN "Set All Controls" capture on this rig, not a guess, and they are the
# SDL_GameControllerButton / SDL_GameControllerAxis enum ordinals (a=0 b=1 x=2 y=3 back=4 guide=5
# start=6 L3=7 R3=8 L1=9 R1=10, dpad 11-14; axes leftx=0 lefty=1 rightx=2 righty=3 l2=+4 r2=+5).
# Re-keyed from input_player1_<suffix> to the bare <suffix> that binds_for() returns, so both
# drivers hand the resolver the same shape.
#
# NORMALIZED ACROSS PADS, CONDITIONALLY. Verified in sdl_joypad.c at v1.22.2: a pad SDL recognises
# is opened with SDL_GameControllerOpen and read through the enum above; one it does NOT recognise
# falls back to RAW SDL_JoystickOpen indices, for which this table is simply WRONG. Probed the
# flatpak's real SDL (2.32.70) with the full fleet on 2026-07-17: every gamepad (DualSense, DS4,
# X-Arcade, Wii U Pro, 8BitDo, Steam Deck) is recognised; only the Sinden guns fall back to raw,
# and they are not gamepads. Re-probe before trusting this for an unrecognised pad.
#
# HATS ARE DEAD UNDER sdl2. sdl_pad_connect sets num_hats = 0 in the controller branch and
# sdl_joypad_button_state returns 0 for any hat query, so the d-pad is reachable ONLY as buttons
# 11-14. That is why this table gives left_btn = "13" while the udev map gives "h0left": the two
# number spaces are NOT interchangeable and must never be "simplified" into one shared table.
_P1 = "input_player1_"
SDL_SEMANTIC_TABLE: dict[str, str] = {
    k[len(_P1):]: v for k, v in _GAMEPAD.items() if k.startswith(_P1)
}

# driver -> how to get that driver's base map for a device. Adding dinput/xinput/hid is one entry
# here and nothing else; that is what lets this outlive udev.
BASE_MAPS = {
    "udev": lambda d: device_binds.binds_for(d),   # the pad's OWN autoconfig, per device
    "sdl2": lambda d: dict(SDL_SEMANTIC_TABLE),    # one fixed table, same for any recognised pad
}

_AXIS_RE = re.compile(r"^[+-]\d+$")
_BTN_RE = re.compile(r"^(?:\d+|h\d+(?:up|down|left|right))$")
_MBTN_RE = re.compile(r"^\d+$")
_NUL = "nul"


def base_map(device, driver: str) -> Optional[dict]:
    """The seated pad's semantic dictionary for `driver`, or None when we cannot know it.

    None means WRITE NOTHING: an unrecognised driver has an unknown number space, and guessing one
    silently mis-binds every control. A udev pad with no autoconfig is None for the same reason."""
    fn = BASE_MAPS.get((driver or "").strip().lower())
    if fn is None:
        return None
    try:
        return fn(device) or None
    except Exception:
        return None


def resolve_token(token: str, base: dict) -> Optional[dict]:
    """A profile token -> the three RetroArch bind variants for it.

    Returns {"btn": v, "axis": v, "mbtn": v} with "nul" for the ones that do not apply, so a write
    CLEARS the variants it is not using (a stale _btn beside a new _axis would fire both). None
    means the token is unresolvable: the caller writes nothing and logs, rather than binding a
    number it made up.

    Order, and each step is load-bearing:
      ""            -> everything nul (deliberately unbound)
      mbtn:N        -> a mouse button. The X-Arcade's trackball red button is the only real user
                       (input_exit_emulator_mbtn = "3"), and RetroArch polls mbtn hotkeys against
                       input_player1_mouse_index only.
      btn:N / axis:+N -> raw escapes, for a control the RetroPad vocabulary cannot name.
      <name>        -> base["<name>_btn"] first: this is the HAT path, and it is why the X-Arcade's
                       d-pad survives a kernel renumbering ("h0left" is direction-explicit, while
                       the raw 13 it replaced is a rank that the 6.16 xpad change already moved
                       once). Then base["<name>_axis"], which is how a DualSense's L2 (an analog
                       trigger, absent from its autoconfig as a button) becomes a hotkey.
    """
    tok = (token or "").strip()
    out = {"btn": _NUL, "axis": _NUL, "mbtn": _NUL}
    if not tok:
        return out
    for prefix, kind, rx in (("mbtn:", "mbtn", _MBTN_RE), ("btn:", "btn", _BTN_RE),
                             ("axis:", "axis", _AXIS_RE)):
        if tok.lower().startswith(prefix):
            val = tok[len(prefix):].strip()
            if not rx.match(val):
                return None                    # garbage escape: refuse, never coerce
            out[kind] = val
            return out
    btn = base.get(f"{tok}_btn")
    if btn and btn != _NUL:
        if not _BTN_RE.match(str(btn)):
            return None
        out["btn"] = str(btn)
        return out
    axis = base.get(f"{tok}_axis")
    if axis and axis != _NUL:
        # STRICT, because RetroArch's parser is not. input_config_parse_joy_axis validates only
        # "length >= 2 and leads with + or -", then strtol(base 0) whose failure is
        # indistinguishable from success: "+abc" silently binds axis 0 and sets valid = true.
        # Garbage is NOT fail-safe there, so it has to be caught here.
        if not _AXIS_RE.match(str(axis)):
            return None
        out["axis"] = str(axis)
        return out
    return None                                # this family cannot express that control


def hotkey_lines(hotkeys: dict, base: dict, logger=None) -> dict[str, str]:
    """{retroarch_cfg_key: value} for a profile's hotkeys, resolved against `base`.

    P1 ONLY, and there is no player variant to add: meta binds exist for user 0 alone
    (input_config_get_prefix returns "input" for meta and only for user 0), so
    input_player2_menu_toggle_btn is not a thing RetroArch would ever read.

    THE MODIFIER MUST NOT BE AN AXIS. RetroArch's "menu toggle bypasses enable_hotkey" escape hatch
    is joykey-ONLY and ignores joyaxis (confirmed in v1.22.2 and master), so an axis-only modifier
    lets menu-toggle fire unmodified -- the pad would open the menu mid-game on its own. Refuse it
    here rather than let a picker ship it.

    A MODIFIER THAT CANNOT RESOLVE VOIDS THE WHOLE SET. Verified in v1.22.2 input_driver.c: the
    block that raises INP_FLAG_BLOCK_HOTKEY is gated on CHECK_INPUT_DRIVER_BLOCK_HOTKEY, which is
    true only when the enable-hotkey bind is SET (key/mbutton/joykey/joyaxis, config or autoconf).
    Leave the modifier unbound while any other hotkey IS bound and that gate is false, the flag is
    never raised, and the hotkeys fire UNGATED -- menu-toggle on Start would open the menu every
    time you press Start, mid-game. Found by resolving the seeded Gamepad profile against the live
    8BitDo FC30 II (2026-07-17): it has no sticks and no triggers, so l3/l2/r2 do not resolve, yet
    slowmotion and menu did. A partial set is worse than none.
    """
    out: dict[str, str] = {}
    resolved: dict[str, dict] = {}
    for field, key in HOTKEYS:
        tok = str(hotkeys.get(field, "") or "")
        got = resolve_token(tok, base)
        if got is None:
            if logger:
                logger.warning(f"ra_profiles: {field}={tok!r} does not resolve on this pad; "
                               f"leaving {key} unbound")
            got = {"btn": _NUL, "axis": _NUL, "mbtn": _NUL}
        if field == "modifier" and got["axis"] != _NUL:
            if logger:
                logger.warning(f"ra_profiles: modifier={tok!r} resolves to an axis; refusing "
                               "(RetroArch's menu-toggle bypass ignores joyaxis, so an axis-only "
                               "modifier lets menu-toggle fire unmodified)")
            got = {"btn": _NUL, "axis": _NUL, "mbtn": _NUL}
        resolved[field] = got
    mod_tok = str(hotkeys.get("modifier", "") or "")
    mod_bound = any(v != _NUL for v in resolved.get("modifier", {}).values())
    others_bound = any(v != _NUL for f, g in resolved.items() if f != "modifier"
                       for v in g.values())
    if mod_tok and not mod_bound and others_bound:
        # An EMPTY modifier token is a deliberate "no modifier, hotkeys always live" and is left
        # alone. This is the other case: a modifier was ASKED for and this pad cannot give it.
        if logger:
            logger.warning(f"ra_profiles: modifier={mod_tok!r} does not resolve on this pad, so "
                           "every hotkey would fire UNGATED; refusing the whole set")
        resolved = {f: {"btn": _NUL, "axis": _NUL, "mbtn": _NUL} for f in resolved}
    for field, key in HOTKEYS:
        for kind, val in resolved[field].items():
            out[f"{key}_{kind}"] = val
    return out


def profile_name_for(policy: dict, family: str, sys_entry: Optional[dict] = None) -> Optional[str]:
    """The profile assigned to `family`, most-specific-wins, or None (-> write nothing).

    `sys_entry` is resolve_policy()'s answer, which already cascades
    game > collection > system. The GLOBAL [ra_profile_map] is applied HERE, per family, and that
    is not a detail: resolve_policy does NOT merge top-level tables. This exact bug already shipped
    once -- the global X-Arcade warn toggles were inert at launch because _xarcade_warn read only
    sys_entry -- and it was fixed the same way, by cascading the global tier explicitly.

    Per-family, so a system overriding only "DualSense" keeps the global "X-Arcade" mapping
    instead of silently dropping it.
    """
    if not family:
        return None
    ent_map = (sys_entry or {}).get("ra_profile_map")
    if isinstance(ent_map, dict):
        name = ent_map.get(family)
        if name:
            return str(name)
    glob = policy.get("ra_profile_map")
    if isinstance(glob, dict):
        name = glob.get(family)
        if name:
            return str(name)
    return None


def get_profile(policy: dict, name: str) -> Optional[dict]:
    """The [ra_profiles.<name>] table, or None. Tolerates a hand-edited husk."""
    profs = policy.get("ra_profiles")
    if not isinstance(profs, dict):
        return None
    p = profs.get(name)
    return p if isinstance(p, dict) else None


def resolve_for(device, driver: str, profile: dict, port: int = 1,
                logger=None) -> dict[str, str]:
    """Every retroarch.cfg key this profile contributes for `device` on `port`.

    {} when nothing can be written (unknown driver, no base map) -- the caller then leaves the
    launch exactly as it would have been without us, which is always better than a guess.

    Composition: the base map is the pad's physical truth (its own autoconfig under udev), the
    profile's `gameplay` RE-VALUES individual binds on top, `settings` are opt-in, and the hotkeys
    ride along for P1 only. Gameplay binds are per-port; hotkeys are not.
    """
    base = base_map(device, driver)
    if not base:
        if logger:
            logger.warning(f"ra_profiles: no base map for driver {driver!r}; writing nothing")
        return {}
    eff = dict(base)
    gameplay = profile.get("gameplay")
    if isinstance(gameplay, dict):
        # RE-VALUE only: an override may not invent a bind the base map does not have, or it would
        # write a key for a control the pad does not expose.
        for k, v in gameplay.items():
            if k in eff:
                eff[k] = str(v)
            elif logger:
                logger.warning(f"ra_profiles: gameplay override {k!r} is not a bind this pad has; "
                               "ignoring")
    out = {f"input_player{port}_{suffix}": val for suffix, val in eff.items()}
    settings = profile.get("settings")
    if isinstance(settings, dict):
        adp = settings.get("analog_dpad_mode")
        if adp is not None:
            out[f"input_player{port}_analog_dpad_mode"] = str(adp)
        dev = settings.get("libretro_device")
        if dev is not None:
            out[f"input_libretro_device_p{port}"] = str(dev)
    if port == 1:
        hk = profile.get("hotkeys")
        out.update(hotkey_lines(hk if isinstance(hk, dict) else {}, eff, logger))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Editor store layer (P3): PURE dict transforms over the local-override policy.
#
# These NEVER touch the filesystem -- the ra_profiles_cmds backend does the
# localpolicy.load(LOCAL) -> mutate -> localpolicy.dump(LOCAL) round-trip, exactly
# like policy_cmds. Two hard rules, both forced by routing.deep_merge, which can
# OVERRIDE a key but can never REMOVE one:
#   * a profile or [ra_profile_map] row seeded in the base controller-policy.toml
#     can only be SHADOWED in local, never deleted. So delete_profile is for
#     USER-made profiles (local-only); a shipped profile is "reset" by dropping
#     its local shadow (reset_profile).
#   * unassign_family writes "" rather than removing the row, because a base row
#     cannot be removed -- and profile_name_for treats "" (falsy) as "no profile".
# ─────────────────────────────────────────────────────────────────────────────

_NAME_MAX = 40
# C0 controls AND DEL (0x7f). The localpolicy TOML emitter escapes only \\ " \n \r \t, so any other
# unescaped control char in a name -- emitted BOTH as a table key and (once a family is assigned) a
# string value -- makes the WHOLE controller-policy.local.toml unparseable, and load() then returns
# {} and silently wipes EVERY local override. 0x7f is the one C1-adjacent case that slips a bare
# [\x00-\x1f]; 0x80-0x9f round-trip fine in tomllib.
_BAD_NAME_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_EDITABLE_SETTINGS = ("analog_dpad_mode",)     # libretro_device is NOT editable: it is absent from
                                               # the base retroarch.cfg and would STICK after unload.


def valid_profile_name(name: str) -> bool:
    n = (name or "").strip()
    return bool(n) and len(n) <= _NAME_MAX and not _BAD_NAME_CHARS.search(n)


def list_profiles(merged: dict) -> list[str]:
    """Every [ra_profiles.<name>] in the merged policy, case-insensitively sorted."""
    profs = merged.get("ra_profiles")
    if not isinstance(profs, dict):
        return []
    return sorted((k for k, v in profs.items() if isinstance(v, dict)), key=str.lower)


def is_shipped(base: dict, name: str) -> bool:
    """True if `name` is seeded in the BASE policy: edit-only (reset, never delete)."""
    profs = base.get("ra_profiles")
    return isinstance(profs, dict) and isinstance(profs.get(name), dict)


def _profiles(local: dict) -> dict:
    return local.setdefault("ra_profiles", {})


def _profile_map(local: dict) -> dict:
    return local.setdefault("ra_profile_map", {})


def create_profile(local: dict, name: str, merged: dict) -> str:
    """Add an empty [ra_profiles.<name>] to `local` (mutates). Refused if the name is invalid or
    already exists anywhere in the merged policy. Returns the stored name."""
    n = (name or "").strip()
    if not valid_profile_name(n):
        raise ValueError(f"invalid profile name {name!r}")
    if n in set(list_profiles(merged)):
        raise ValueError(f"a profile named {n!r} already exists")
    _profiles(local)[n] = {"hotkeys": {f: "" for f, _ in HOTKEYS}}
    return n


def delete_profile(local: dict, name: str) -> None:
    """Remove a user-made profile from local AND every local map row pointing at it. A shipped
    profile lives in base and cannot be removed this way -- use reset_profile."""
    _profiles(local).pop(name, None)
    m = _profile_map(local)
    for fam in [f for f, p in list(m.items()) if p == name]:
        m.pop(fam, None)


def reset_profile(local: dict, name: str) -> None:
    """Drop a shipped profile's LOCAL shadow so the merged view reverts to the base seed."""
    _profiles(local).pop(name, None)


def set_hotkeys(local: dict, name: str, hotkeys: dict) -> None:
    """Set the profile's hotkey tokens in local (mutates). Fields are validated; tokens are stored
    verbatim (an unresolvable token simply no-ops on a pad at resolve time, never crashes)."""
    hk = _profiles(local).setdefault(name, {}).setdefault("hotkeys", {})
    for field, tok in hotkeys.items():
        if field not in _HOTKEY_FIELDS:
            raise ValueError(f"unknown hotkey field {field!r}")
        hk[field] = str(tok or "")


def set_setting(local: dict, name: str, key: str, value) -> None:
    """Set (or clear, on None/"") one editable profile setting in local."""
    if key not in _EDITABLE_SETTINGS:
        raise ValueError(f"unsupported profile setting {key!r}")
    s = _profiles(local).setdefault(name, {}).setdefault("settings", {})
    if value is None or value == "":
        s.pop(key, None)
    else:
        s[key] = str(value)


def assign_family(local: dict, family: str, profile: str) -> None:
    """Point `family` at `profile` in the global [ra_profile_map] (mutates local)."""
    if not family:
        raise ValueError("empty family")
    _profile_map(local)[family] = str(profile or "")


def unassign_family(local: dict, family: str) -> None:
    """Set `family` to "" (unassigned). NOT a pop: a base-seeded row cannot be removed via local,
    only shadowed to "no profile"."""
    if not family:
        raise ValueError("empty family")
    _profile_map(local)[family] = ""
