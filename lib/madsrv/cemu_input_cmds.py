"""cemu_input.* - the Cemu (Wii U) family x context input-assignment page.

FAMILY-FIRST (simpler than the RetroArch profile-first raprof editor, because Cemu profiles are
device-agnostic): one page per launch CONTEXT ("docked" | "handheld"), each a "settings" page
rendered by the generic GuiMadPageEmuSettings under its OWN namespace -- "cemu_input_docked" and
"cemu_input_handheld" (EmuSettings does not thread a context param, so the door bakes the context
into the namespace it opens), buffered X=Save / Y=Cancel. Groups:

  "Family input"  - one bool switch: seating_enabled (the master opt-in for lib/cemu_seat).
  "<Docked|Handheld> map" - one ENUM row per controller family: which native
                            controllerProfiles/<stem>.xml is that family's map in this context.
                            Index 0 = "(leave resting)" = unset (that slot is left untouched).

The DOOR fixes the context: the docked Cemu tile Input row opens context="docked"; the on-the-go
Wii U Input door opens context="handheld". There is no in-page docked/handheld toggle.

EVERY write touches ONLY controller-policy.local.toml (the base profile_map ships empty). Editing a
family writes/removes ONE key under [backends.cemu.profile_map.<context>]; the maps are not shared
across profiles (unlike RA's [ra_profile_map]), so a net-changed write is self-contained.

Phase 2 (deferred) adds live per-button rebinding of a profile's <mappings> on a separate
GuiMadPageEmuInputMap leaf; this page is assignment only and reuses your existing profiles.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

from .. import cemu_profiles, localpolicy, mad_config
from ..policy import LOCAL, load_merged
from . import policy_settings_cmds
from .input_buffer import InputBuffer
from .rpc import RpcError, method

_CONTEXTS = ("docked", "handheld")
_UNSET_LABEL = "(leave resting)"
_GAMEPAD_FAMILY = "Steam Deck"
_WIIU_SYS = "wiiu"
_WARN_FLAG = "warn_when_only_xarcade"   # X-Arcade "plug in a gamepad" system flag, relocated onto Input


def _families() -> list[str]:
    # Exclude X-Arcade: routing.family_of (which cemu_seat uses at launch) classifies the cab as
    # "Xbox" and NEVER "X-Arcade" (only family_token_of splits it out), so an "X-Arcade" row would be a
    # dead assignment the binder can never apply. Assign the cab via the "Xbox" row instead.
    return [f for f in mad_config.KNOWN_FAMILIES if f != "X-Arcade"]


_SEATS = (1, 2, 3, 4)          # EXTERNAL players, the same numbering as [systems.wiiu].ports
#                                and the Device pins page, so "Player 2" means one pad everywhere.


def _seat_keys() -> list[str]:
    return [cemu_profiles.seat_key(s) for s in _SEATS]


def _cemu_cfg(merged: dict) -> dict:
    be = merged.get("backends") if isinstance(merged.get("backends"), dict) else {}
    cfg = be.get("cemu") if isinstance(be, dict) else None
    return cfg if isinstance(cfg, dict) else {}


def _config_dir(cfg: dict) -> Path:
    return Path(str(cfg.get("config_dir", "~/.config/Cemu/controllerProfiles"))).expanduser()


def _profile_stems(cfg: dict) -> list[str]:
    """Named profiles the user can assign: every controllerProfiles/*.xml EXCEPT the active slot
    files controller0..7.xml (those are the router-managed targets, not templates)."""
    cfg_dir = _config_dir(cfg)
    if not cfg_dir.is_dir():
        return []
    out = []
    for p in sorted(cfg_dir.glob("*.xml")):
        if re.fullmatch(r"controller[0-7]", p.stem):
            continue
        out.append(p.stem)
    return out


_GAMEPAD_TYPE = "Wii U GamePad"
_PRO_TYPE = "Wii U Pro Controller"
_PROFILE_TYPE_RE = re.compile(r"<type>(.*?)</type>", re.DOTALL)


def _profile_type(path: Path) -> str:
    """The emulated <type> of a controllerProfiles/<name>.xml ("" if unreadable/absent)."""
    try:
        m = _PROFILE_TYPE_RE.search(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    return m.group(1).strip() if m else ""


def _profile_stems_by_type(cfg: dict) -> dict[str, str]:
    """{stem: emulated <type>} for every assignable profile (same set as _profile_stems)."""
    cfg_dir = _config_dir(cfg)
    if not cfg_dir.is_dir():
        return {}
    out: dict[str, str] = {}
    for p in sorted(cfg_dir.glob("*.xml")):
        if re.fullmatch(r"controller[0-7]", p.stem):
            continue
        out[p.stem] = _profile_type(p)
    return out


# ── buffered working copy (a nested dict; InputBuffer deep-compares it) ────────────────
def _load_working(ctx):
    (context,) = ctx
    merged = load_merged()
    cfg = _cemu_cfg(merged)
    pm = cfg.get("profile_map") if isinstance(cfg.get("profile_map"), dict) else {}
    slice_ = pm.get(context) if isinstance(pm.get(context), dict) else {}
    # Player rows live in the SAME profile_map slice as the family rows (family_profiles.lookup is
    # key-agnostic, exactly as Ryujinx already does), so they ride through _apply_edit and _flush
    # with no second store and no second write path.
    assign = {k: str(slice_.get(k, "") or "") for k in _families() + _seat_keys()}
    # Type-aware options PER FAMILY: the "Steam Deck" family IS Controller 1 (the Wii U GamePad), so
    # it may only take a "Wii U GamePad" profile; every external family fills a Pro-controller player
    # slot, so it takes "Wii U Pro Controller" profiles (a GamePad profile there would be an invalid
    # 2nd GamePad). A family's CURRENT assignment is always kept visible even when it no longer matches
    # (stale / cross-type / file removed), so nothing silently reads as unset.
    types = _profile_stems_by_type(cfg)

    def _opts(fam):
        want = _GAMEPAD_TYPE if fam == _GAMEPAD_FAMILY else _PRO_TYPE
        valid = [s for s, t in types.items() if t == want]
        cur = assign.get(fam, "")
        if cur and cur not in valid:
            valid.append(cur)
        return [_UNSET_LABEL] + sorted(set(valid))

    # A player row always takes Pro-type profiles. In takeover mode the first seated external
    # becomes Cemu's Controller 1, but the seater retypes it at write time (repin_profile's
    # gamepad_type), so the page never has to know which slot a player will land in.
    options_by_family = {fam: _opts(fam) for fam in _families()}
    options_by_family.update({k: _opts(k) for k in _seat_keys()})

    # cemu-global "Profiles folder" (config_dir): AppImage vs Flatpak controllerProfiles. Rendered on the
    # DOCKED page only (one editor for a context-independent value); existence-marked like backends:255.
    cur_dir = str(cfg.get("config_dir", "~/.config/Cemu/controllerProfiles"))
    dir_paths = list(mad_config.CONFIG_PRESETS.get(("cemu", "config_dir"), []))
    if cur_dir and cur_dir not in dir_paths:
        dir_paths = [cur_dir] + dir_paths
    dir_opts = [("✓ " if Path(p).expanduser().exists() else "· ") + p for p in dir_paths]
    # X-Arcade "plug in a gamepad" warn ([systems.wiiu] flag), rendered on the DOCKED page only.
    wd = policy_settings_cmds.warn_descriptor(_WIIU_SYS)
    # Part 2: the docked slice (for the handheld "(from docked)" hint) + the mirror flag.
    docked = pm.get("docked") if isinstance(pm.get("docked"), dict) else {}
    docked_assign = {k: str(docked.get(k, "") or "") for k in _families() + _seat_keys()}
    # The handheld slice + the stem->type map, for the docked page's "copy my handheld map" action.
    handheld = pm.get("handheld") if isinstance(pm.get("handheld"), dict) else {}
    handheld_assign = {fam: str(handheld.get(fam, "") or "") for fam in _families()
                       if str(handheld.get(fam, "") or "")}

    return {"seating": bool(cfg.get("seating_enabled", False)), "assign": assign,
            "options_by_family": options_by_family,
            "config_dir": cur_dir, "config_dir_paths": dir_paths, "config_dir_opts": dir_opts,
            "warn": (bool(wd["value"]) if wd else None), "warn_label": (wd["label"] if wd else ""),
            "mirror": bool(cfg.get("handheld_mirrors_docked", False)),
            "docked_assign": docked_assign,
            "handheld_assign": handheld_assign, "stems_by_type": types}


def _apply_edit(working, edit):
    key = edit["key"]
    val = edit.get("value", "")
    if key == "seating_enabled":
        working["seating"] = (str(val) == "1")
    elif key.startswith("family:") or key.startswith("player:"):
        fam = key.split(":", 1)[1]
        if fam not in working["assign"]:
            raise RpcError("EINVAL", f"unknown family {fam!r}")
        options = working["options_by_family"].get(fam, [_UNSET_LABEL])
        try:
            idx = int(float(val))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad option index {val!r}")
        working["assign"][fam] = "" if idx <= 0 or idx >= len(options) else options[idx]
    elif key == "config_dir":
        paths = working.get("config_dir_paths", [])
        try:
            idx = int(float(val))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad config_dir index {val!r}")
        if 0 <= idx < len(paths):
            working["config_dir"] = paths[idx]
    elif key == "warn_xarcade":
        working["warn"] = (str(val) == "1")
    elif key == "handheld_mirrors_docked":
        working["mirror"] = (str(val) == "1")
    elif key == "fill_from_handheld":
        # DOCKED page only. Copies each handheld family pick into the docked map, but ONLY where
        # docked is unset and only when the stem still exists AND its emulated <type> is EXACTLY the
        # one that row accepts. Never overwrites an existing docked pick, never removes anything.
        # It STAGES like every other edit on this buffered page, so X saves it and Y reverts it --
        # an unbuffered write underneath a page with pending edits would be a nasty surprise.
        # Index 0 ("Leave docked as it is") is a deliberate no-op, so the stepper can be moved back.
        try:
            do_fill = int(float(val)) != 0
        except (TypeError, ValueError):
            do_fill = False
        by_type = working.get("stems_by_type") or {}
        for fam, stem in (working.get("handheld_assign") or {}).items() if do_fill else ():
            if fam not in working["assign"] or working["assign"].get(fam):
                continue                                  # unknown family, or docked already set
            if stem not in by_type:
                continue                                  # the profile is gone from disk
            # The SAME predicate _opts uses. A looser test (merely "not GamePad") could stage a
            # Wiimote/Classic-typed profile into a row whose own option list excludes it: the row
            # would still render as unset, yet _flush's value diff would write it, and cemu_seat
            # force-rewrites <type> to Pro WITHOUT retranslating the button ids -> a dead d-pad.
            want = _GAMEPAD_TYPE if fam == _GAMEPAD_FAMILY else _PRO_TYPE
            if by_type[stem] != want:
                continue
            working["assign"][fam] = stem
        # No status field is stashed in `working`: InputBuffer deep-compares this dict to decide
        # "dirty", so a note would make a no-op copy look like a pending change. The stepper does
        # not rebuild the page, so the filled rows repaint when it is reopened (the note says so).
    else:
        raise RpcError("EINVAL", f"unknown key {key!r}")
    return working, edit


def _flush(ctx, disk, edits):
    (context,) = ctx
    final = copy.deepcopy(disk)
    for e in edits:
        final, _ = _apply_edit(final, e)
    keys = {e["key"] for e in edits}
    data = localpolicy.load(LOCAL)
    be = data.setdefault("backends", {}).setdefault("cemu", {})

    if "seating_enabled" in keys and final["seating"] != disk["seating"]:
        be["seating_enabled"] = bool(final["seating"])
    if "config_dir" in keys and final["config_dir"] != disk["config_dir"]:
        be["config_dir"] = final["config_dir"]                    # relocated from the old Controllers page
    if "handheld_mirrors_docked" in keys and final["mirror"] != disk["mirror"]:
        if final["mirror"]:
            be["handheld_mirrors_docked"] = True
        else:
            be.pop("handheld_mirrors_docked", None)               # default absent = off

    # Write ONLY families that NET-CHANGED vs the load-time snapshot (a toggle-there-and-back is a
    # no-op). "" clears the key (base profile_map is empty, so a cleared local key = unset).
    # Derived from the VALUE diff, not from which keys were edited: "Copy my handheld map here"
    # stages several families under one non-family key, and a key-derived set silently dropped them.
    changed = {fam for fam in final.get("assign", {})
               if final["assign"].get(fam, "") != disk["assign"].get(fam, "")}
    if changed:
        pm = be.setdefault("profile_map", {}).setdefault(context, {})
        for fam in changed:
            if final["assign"].get(fam, "") == disk["assign"].get(fam, ""):
                continue
            stem = final["assign"].get(fam, "")
            if stem:
                pm[fam] = stem
            else:
                pm.pop(fam, None)
    localpolicy.dump(LOCAL, data)      # atomic write + staterev.bump("config")

    # The X-Arcade warn is a SYSTEM flag ([systems.wiiu]); write it via policy_settings_cmds (keeps the
    # base-default-revert clamp in one place) AFTER the backend dump, as its OWN load/dump so neither
    # write clobbers the other.
    if "warn_xarcade" in keys and final.get("warn") is not None and final.get("warn") != disk.get("warn"):
        policy_settings_cmds._sysflags_set(_WIIU_SYS, {"key": _WARN_FLAG, "value": final["warn"]})

    return _load_working(ctx)


_buf = InputBuffer(load=_load_working, apply_edit=_apply_edit, flush=_flush)


# ── render ────────────────────────────────────────────────────────────────────────────
def _render(context: str, working, dirty: bool) -> dict:
    obf = working["options_by_family"]
    mirror = bool(working.get("mirror")) and context == "handheld"
    docked_assign = working.get("docked_assign", {})

    def _idx(fam, stem):
        opts = obf.get(fam, [_UNSET_LABEL])
        try:
            return opts.index(stem) if stem else 0
        except ValueError:
            return 0

    def _fam_row(f):
        opts = list(obf.get(f, [_UNSET_LABEL]))
        # "same as docked" ON: an UNSET handheld family with a docked value shows the fallback target in
        # slot 0 (DISPLAY-only -- index 0 still means "unset", and _apply_edit reads options_by_family, so
        # the indices are unchanged; picking a real profile still overrides).
        if mirror and not working["assign"].get(f, "") and docked_assign.get(f):
            opts = [f"(from docked: {docked_assign[f]})"] + opts[1:]
        return {"key": f"family:{f}", "label": f, "type": "enum",
                "options": opts, "value": _idx(f, working["assign"].get(f, ""))}

    fam_rows = [_fam_row(f) for f in _families()]

    def _player_row(seat: int):
        """One external player. Slot 0 falls through to that pad's TYPE row rather than meaning
        "nothing", which is what makes players adoptable one at a time: an untouched Deck resolves
        exactly as it did before players existed."""
        k = cemu_profiles.seat_key(seat)
        opts = list(obf.get(k, [_UNSET_LABEL]))
        opts[0] = "Same as pad type"
        if mirror and not working["assign"].get(k, "") and docked_assign.get(k):
            opts[0] = f"(from docked: {docked_assign[k]})"
        return {"key": f"player:{k}", "label": f"Player {seat}", "type": "enum",
                "options": opts, "value": _idx(k, working["assign"].get(k, "")), "picker": True}

    player_rows = [_player_row(s) for s in _SEATS]
    seat_row = {"key": "seating_enabled", "label": "Let MAD set input by controller",
                "type": "bool", "value": bool(working["seating"])}
    family_settings = [seat_row]
    fam_note = ""
    if context == "handheld":
        family_settings.append({"key": "handheld_mirrors_docked",
                                "label": "Use my docked map when a handheld family is unset",
                                "type": "bool", "value": bool(working.get("mirror"))})
        # Quote the row's REAL label, not a paraphrase: the note used to say "'Same as docked'
        # applies only while...", which names no row on this page. And point at the per-game tier:
        # flattening the old Input group left this page as the only signpost to it, unlike the PS2,
        # PS3 and Lindbergh tiles which still show Per-game as a sibling row.
        fam_note = ("'Use my docked map when a handheld family is unset' applies only while 'Let "
                    "MAD set input by controller' is on. One game only: Per-game, Input.")

    ctx_label = "Docked" if context == "docked" else "Handheld"
    groups = [
        {"title": "Family input", "note": fam_note, "settings": family_settings},
        {"title": f"{ctx_label} players", "settings": player_rows,
         "note": ("Which pad is which player is set in Device pins, Wii U. A player left on 'Same "
                  "as pad type' uses the map below, which is how this worked before.")},
        {"title": f"{ctx_label} map", "note": "", "settings": fam_rows},
    ]
    if context == "docked":
        # The docked map ships EMPTY, and an empty map means "leave Cemu's own saved Controller 1-8
        # settings exactly as they are" -- correct, but easy to read as broken. Offer a one-press
        # copy from the handheld map (which most setups fill first), and say what empty means.
        if working.get("handheld_assign"):
            # An ENUM, not an action row. The panel's addActionButton returns early unless the row
            # carries an "rpc" (so an action row here would draw nothing at all), and its callback
            # only flashes a message: it never sets mDirty, so a staged fill could not be saved with
            # X on this buffered page. Routing through the enum stepper reuses the normal
            # set -> dirty -> X-to-save path. Reopen the page to see the rows repaint: the stepper
            # does not rebuild.
            groups.append({"title": "Start from handheld",
                           "note": ("Copies your handheld picks into the docked families that are "
                                    "still unset. Nothing already set is changed. Press X to save, "
                                    "then reopen this page to see the filled rows."),
                           "settings": [{"key": "fill_from_handheld", "type": "enum",
                                         "label": "Copy my handheld map here",
                                         "options": ["Leave docked as it is",
                                                     "Copy my handheld map here"],
                                         "value": 0}]})
        try:
            dir_idx = working.get("config_dir_paths", []).index(working.get("config_dir", ""))
        except ValueError:
            dir_idx = 0
        groups.append({"title": "Profiles folder", "note": "", "settings": [
            {"key": "config_dir", "label": "controllerProfiles folder", "type": "enum",
             "options": list(working.get("config_dir_opts", [])), "value": dir_idx}]})
        if working.get("warn") is not None:
            groups.append({"title": "Startup warnings", "note": "", "settings": [
                {"key": "warn_xarcade",
                 "label": working.get("warn_label") or "Warn when only the X-Arcade is present",
                 "type": "bool", "value": bool(working["warn"])}]})

    return {
        "exists": True, "buffered": True, "dirty": dirty,
        "note": (f"Assign each controller family its {ctx_label.lower()} input profile. The layout "
                 f"follows the controller, not the player slot. '{_GAMEPAD_FAMILY}' is Controller 1 "
                 "(the Wii U GamePad). Leave a family on '(leave resting)' to not touch its slot - "
                 "with nothing assigned at all, MAD leaves Cemu's own saved Controller 1-8 settings "
                 "exactly as they are. A 2nd "
                 "same-family pad auto-uses the next-numbered profile (e.g. 'DualSense 2') if you have "
                 "made one, else it reuses this one. Changes are staged - press X to save."),
        "groups": groups,
    }


# ── buffered RPCs, ONE namespace per context. Getters registered WITHOUT cache=("config",):
#    the buffer is the source of truth mid-edit (per the InputBuffer caller contract). ──────
def _register(ns: str, context: str):
    @method(f"{ns}.get", slow=True)
    def _get(params, _c=context):
        return _render(_c, _buf.get((_c,)), _buf.dirty)

    @method(f"{ns}.set", slow=True)
    def _set(params, _c=context):
        key = params.get("key")
        if not key:
            raise RpcError("EINVAL", "key required")
        _buf.set((_c,), {"key": str(key), "value": str(params.get("value", ""))})
        return {"dirty": _buf.dirty}

    @method(f"{ns}.save", slow=True)
    def _save(params, _c=context):
        saved = _buf.save((_c,))
        return {"saved": saved, "message": "Saved." if saved else "Nothing to save."}

    @method(f"{ns}.cancel", slow=True)
    def _cancel(params, _c=context):
        _buf.cancel((_c,))
        return {"message": "Reverted to saved."}


_register("cemu_input_docked", "docked")
_register("cemu_input_handheld", "handheld")
