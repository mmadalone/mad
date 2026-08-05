"""Handheld graphic-pack overrides for Cemu (Wii U): apply at game-start, revert at game-end.

Cemu has no handheld/docked axis of its own, so MAD gives it one. Two On-the-go pages feed this
rail, and it is the only thing that ever writes their choices into Cemu's settings.xml:

  * RESOLUTION. Cemu's internal resolution is not a scalar, it is a graphic-pack PRESET (e.g.
    "1920x1080" / "1280x720") under `<GraphicPack><Entry filename=...><Preset>`. The per-title
    handheld preset lives in policy at [systems.wiiu.handheld.res_presets].<titleid>; unset means
    the 720p CAP, applied only when it LOWERS the game's resting render.
  * GRAPHIC PACKS. Force a pack on or off handheld, and override any of its option presets. Stored
    per (title, pack) at [systems.wiiu.handheld.packs] -- see lib/cemu_hhpacks.

Own independent atomic marker rail (own dir), swept at game-start AND game-end so a crash orphan can
never leave a later DOCKED game stuck on the handheld state. ONE marker covers settings.xml and
carries a LIST of records, one per pack, each recording what was there before and what we applied;
a revert is skipped per record if that value was changed underneath (revert-if-unchanged). The v1
single-record marker a pre-upgrade launch may have left behind is still understood and swept.

Timing: Cemu rewrites settings.xml ON EXIT, so apply runs at game-start (Cemu closed) and restore at
game-end (Cemu closed), the same timing the input rail (cemu_input_dock) uses. Both entry points
refuse outright while Cemu is running, because a Cemu that exits later rewrites the file from its own
memory and would re-bake whatever we had applied, with no marker left to heal it.

Reuses the byte-preserving <GraphicPack> read/write + the resolution-pack detector in
lib/madsrv/cemu_packs_cmds.py. Called by hooks/game-start/08-cemu-res.sh (sweep + apply) and
hooks/game-end/10-cemu-res-restore.sh (sweep).
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from . import cemu_hhpacks, deck_state, mad_paths, proc_guard
from .madsrv import cemu_games, cemu_packs_cmds as cp, cfgutil
from .policy import load_merged

_RES_DIR = mad_paths.storage("controller-router", "cemu-res")
_MARKER_VERSION = 2


def _log(msg: str) -> None:
    """One line per applied record and per sweep verdict, into the shared router log. Game Mode has
    no console and both hooks send stderr to /dev/null, so without this a partial revert leaves no
    evidence at all beyond the marker's presence."""
    try:
        import datetime
        log = mad_paths.storage("controller-router", "router.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log.open("a", encoding="utf-8") as f:
            f.write(f"cemu-res [{ts}]: {msg}\n")
    except Exception:
        pass


def _marker(path: Path) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", str(path))
    return _RES_DIR / (slug + ".json")


def _set_group_preset(entry: dict, group: str, value) -> None:
    """Set `entry`'s <Preset> for `group` to `value` (a name), or REMOVE it when value is None.
    Direct + explicit: we never lean on Cemu's default-clearing, because real packs often mark the
    default INSIDE the preset name ("... (Default)") with no `default=` flag, so a detected default is
    unreliable. Storing the chosen preset explicitly and restoring the exact resting <Preset> (name,
    or absent) is always correct -- Cemu honours an explicit preset and falls back to its own default
    when the <Preset> is absent.

    Deliberately NOT cp._set_option: that one clears a value equal to the pack default and drops a
    preset-less disabled entry. Those are Cemu-editor parity rules, correct for the editor pages and
    wrong here, where the job is exact restoration of whatever was there."""
    entry["presets"] = [(c, p) for (c, p) in entry["presets"] if c != group]
    if value is not None:
        entry["presets"].append((group, value))


def _tid_for_rom(rom: str) -> str | None:
    """Delegates to cemu_games.titleid_for_rom -- the single rom -> title-id boundary, which also
    strips ES-DE's backslash escapes. Kept as a name because it is the historical entry point."""
    return cemu_games.titleid_for_rom(rom)


def _configured_preset(pol: dict, tid: str) -> str | None:
    """The user's handheld resolution preset for this title from
    [systems.wiiu.handheld.res_presets].<tid> in policy, or None (not configured -> leave alone)."""
    systems = pol.get("systems") if isinstance(pol, dict) else None
    wiiu = systems.get("wiiu") if isinstance(systems, dict) else None
    hh = wiiu.get("handheld") if isinstance(wiiu, dict) else None
    presets = hh.get("res_presets") if isinstance(hh, dict) else None
    v = presets.get(tid.lower()) if isinstance(presets, dict) else None
    return v.strip() if isinstance(v, str) and v.strip() else None


# ── marker records ────────────────────────────────────────────────────────────
def _records(d: dict) -> list:
    """The marker's records, normalised to v2 shape. A v1 marker is the pre-upgrade single-record
    form {filename, group, prev, applied}; it can still be sitting on disk from a launch that
    happened before this module grew a list, and it must still heal."""
    if isinstance(d.get("recs"), list):
        return [r for r in d["recs"] if isinstance(r, dict)]
    if d.get("filename"):
        return [{"filename": d["filename"], "prev_present": True, "prev_disabled": False,
                 "applied_disabled": None, "created": False,
                 "groups": [{"group": d.get("group", ""), "prev": d.get("prev"),
                             "applied": d.get("applied")}]}]
    return []


def _revert_record(entries: list, snapshot: list, rec: dict) -> str:
    """Undo one pack's record against `entries`, judging "is this still what I applied?" against
    `snapshot` -- the state before ANY record in this sweep was undone. Judging against the live
    model instead would let two records on the same pack alias each other: they resolve to the same
    entry dict, so undoing the first changes what the second compares against and the second would
    decline, stranding a handheld value permanently. Returns a short verdict for the log."""
    filename = rec.get("filename") or ""
    created = bool(rec.get("created"))
    e = cp._find(entries, filename)
    was = cp._find(snapshot, filename)
    groups = [g for g in rec.get("groups", []) if isinstance(g, dict)]

    if e is None:
        # The entry is gone. If WE created it, that is already the right end state. If we switched
        # the pack OFF and Cemu then dropped the whole entry on exit (its documented "presence means
        # enabled" model makes this possible), the user's docked pack would be silently lost, so
        # rebuild what we recorded. If we only changed presets, the disappearance is the user
        # removing the pack in Cemu, and resurrecting it would fight them.
        if created or rec.get("applied_disabled") is None:
            return "entry gone, left alone"
        presets = [(p[0], p[1]) for p in rec.get("prev_presets", [])
                   if isinstance(p, (list, tuple)) and len(p) == 2]
        entries.append({"filename": filename, "disabled": bool(rec.get("prev_disabled")),
                        "presets": presets})
        return "entry gone, rebuilt from marker"

    done = []
    for g in groups:
        group = g.get("group", "")
        cur = cp._entry_preset(was, group) if was is not None else None
        if cur != g.get("applied"):
            done.append(f"{group or '(unnamed)'}: changed underneath, kept")
            continue
        _set_group_preset(e, group, g.get("prev"))
        done.append(f"{group or '(unnamed)'}: restored")

    applied_dis = rec.get("applied_disabled")
    if applied_dis is not None:
        if was is not None and bool(was.get("disabled")) == bool(applied_dis):
            e["disabled"] = bool(rec.get("prev_disabled"))
            done.append("enabled: restored")
        else:
            done.append("enabled: changed underneath, kept")

    if created and not e["presets"]:
        # We invented this entry; with the presets we wrote now gone it means nothing. Drop it only
        # when nothing survives that we did not write ourselves.
        try:
            entries.remove(e)
            done.append("created entry removed")
        except ValueError:
            pass
    return f"{filename}: " + ", ".join(done or ["nothing to do"])


def docked_baseline(path: Path | None = None) -> dict:
    """What settings.xml held BEFORE the outstanding marker's changes, as
    {norm filename: {"disabled": bool, "presets": {group: preset}}}. Empty when no marker.

    A page that wants to show "same as docked (X)" cannot trust the live file while a marker is
    outstanding: after a crash orphan the file holds the HANDHELD values, and echoing those back as
    the docked baseline would invite the user to bake them in. Only the groups the rail actually
    touched are reported, since those are the only ones it changed."""
    try:
        mk = _marker(path or cp.settings_path())
        if not mk.is_file():
            return {}
        d = json.loads(mk.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict = {}
    for rec in _records(d):
        key = cp._norm(rec.get("filename") or "")
        if not key:
            continue
        slot = out.setdefault(key, {"disabled": None, "presets": {}})
        if rec.get("applied_disabled") is not None:
            # prev_present distinguishes "was there and switched off" from "was not there at all",
            # and both mean the pack was NOT enabled docked. Reading prev_disabled alone reported a
            # pack the rail itself created as enabled docked, so a page would label it "same as
            # docked (on)" and clearing the override would do the opposite of what the row promised.
            slot["disabled"] = (True if not rec.get("prev_present", True)
                                else bool(rec.get("prev_disabled")))
        for g in rec.get("groups", []):
            if isinstance(g, dict):
                slot["presets"][g.get("group", "")] = g.get("prev")
    return out


def sweep_all() -> None:
    """Revert every recorded handheld pack change and drop the marker. Reverts a value ONLY if it is
    still the one we applied (else the user or Cemu changed it -- leave it). Idempotent +
    self-healing; run at game-start and game-end, with Cemu CLOSED."""
    if proc_guard.emulator_running("cemu"):
        _log("sweep skipped: Cemu is running (marker kept for the next sweep)")
        return
    try:
        markers = sorted(_RES_DIR.glob("*.json"))
    except OSError:
        return
    for mk in markers:
        keep = False
        try:
            d = json.loads(mk.read_text(encoding="utf-8"))
            path = Path(d["path"])
            recs = _records(d)
            text = cfgutil.read_text(path)
            if text is not None and recs:
                entries = cp._parse_graphicpack(text)
                snapshot = copy.deepcopy(entries)
                for rec in recs:
                    _log("revert " + _revert_record(entries, snapshot, rec))
                new = cp._write_block(text, entries)
                if new != text:
                    try:
                        # No ensure_bak here: with a marker on disk the file may already hold the
                        # handheld state, and capturing THAT as the one-time pristine .bak would
                        # freeze a transient mid-override snapshot as the user's backup. apply()
                        # takes the .bak, from the resting file, before it changes anything.
                        cfgutil.atomic_write(path, new)
                    except Exception:
                        keep = True     # revert WRITE failed (I/O) -> keep the marker to retry,
                        #                 so a later sweep still restores the docked state
        except Exception:
            pass                        # unreadable/malformed marker -> drop it (nothing to heal)
        if not keep:
            try:
                mk.unlink()
            except OSError:
                pass


# ── apply ─────────────────────────────────────────────────────────────────────
def _gates(pol: dict) -> bool:
    """on-the-go on, physically handheld, and wiiu opted in."""
    hh = pol.get("handheld") if isinstance(pol, dict) else None
    if not (isinstance(hh, dict) and hh.get("enabled", False)):
        return False
    try:
        if not deck_state.is_handheld(deck_state.resolve_force(hh)):
            return False
    except Exception:
        return False
    systems = pol.get("systems")
    sysd = systems.get("wiiu") if isinstance(systems, dict) else None
    sys_hh = sysd.get("handheld") if isinstance(sysd, dict) else None
    return isinstance(sys_hh, dict) and bool(sys_hh.get("enabled", False))


def _res_change(pol: dict, tid: str, entries: list, packs: list, effective: set):
    """(filename, group, preset) for the handheld RESOLUTION change, or None.

    Ownership is resolved against the HANDHELD-EFFECTIVE enabled set, not the docked one: if the
    packs pages switched the docked owner off for handheld, it is not the owner this launch, and if
    they switched a different resolution pack on, it is. That keeps this in step with what the
    Resolution page shows and stops the two stores writing contradictory things into one pack."""
    info = cp.resolution_titleids(entries=entries, packs=packs, enabled=effective).get(tid.lower())
    if not info:
        return None
    stored = _configured_preset(pol, tid)             # a preset name, cp.KEEP, or None (unset)
    if stored == cp.KEEP:
        return None                                   # explicit Keep -> leave as-is
    if stored and stored in info["presets"]:
        low = stored                                  # explicit preset override (respected as-is)
    else:
        # Unset or a stale name -> the automatic 720p cap. Only ever for a pack that is enabled
        # DOCKED: for one the override is switching on, the "resting" render this compares against
        # would be the pack's own default, a resolution the game never actually renders at.
        if cp._norm(info["filename"]) not in cp.enabled_filenames(entries):
            return None
        low = cp.downshift_target(info, entries)
        if not low:
            return None                               # already <=720p, or no <=720p preset -> leave
    return info["filename"], info["group"], low


def _plan(pol: dict, tid: str, entries: list) -> list:
    """The records to apply for this title: every handheld pack override, plus the resolution
    change. One record per PACK (never per option group) -- two records naming the same pack would
    resolve to the same entry dict and alias each other on the way back out."""
    packs = cp._scan_packs()
    overrides = cemu_hhpacks.for_title(tid, pol)
    docked = cp.enabled_filenames(entries)
    effective = cemu_hhpacks.effective_enabled(entries, overrides, docked)
    by_file: dict = {}

    def _rec(filename: str) -> dict:
        key = cp._norm(filename)
        if key not in by_file:
            e = cp._find(entries, filename)
            by_file[key] = {"filename": filename, "prev_present": e is not None,
                            "prev_disabled": bool(e.get("disabled")) if e else False,
                            # The entry's WHOLE resting preset list, not just the groups we touch.
                            # Only the rebuild path reads it, and that path has nothing else to go
                            # on: an enable-only record knows no groups, so without this it would
                            # restore an empty entry and drop every option choice the user had.
                            "prev_presets": [list(p) for p in e["presets"]] if e else [],
                            "applied_disabled": None, "created": False, "groups": []}
        return by_file[key]

    by_name = {cp._norm(p["filename"]): p for p in packs}
    res = _res_change(pol, tid, entries, packs, effective)
    # The (pack, group) the Resolution page owns THIS launch. The packs pages hide that row, but a
    # value can still be sitting in their store from a moment when the row was visible (force the
    # pack off, the row reappears, pick something, set it back to "same as docked"), and nothing
    # prunes it. Resolving the owner FIRST and skipping it below means the Resolution page always
    # wins; the old order let the stale packs-page value take the slot and silently drop the
    # resolution change instead.
    owned = (cp._norm(res[0]), res[1]) if res is not None else None

    for filename, spec in overrides.items():
        want = spec.get("enabled")
        e = cp._find(entries, filename)
        if want is True and (e is None or e.get("disabled")):
            rec = _rec(filename)
            rec["applied_disabled"] = False
            rec["created"] = e is None
        elif want is False and e is not None and not e.get("disabled"):
            rec = _rec(filename)
            rec["applied_disabled"] = True
        # Option presets are pointless on a pack that will be off this launch.
        if cp._norm(filename) not in effective:
            continue
        pk = by_name.get(cp._norm(filename))
        for group, preset in (spec.get("options") or {}).items():
            if owned is not None and (cp._norm(filename), group) == owned:
                continue                              # the Resolution page owns this group
            # A pack update can rename a group or a preset out from under a stored override. The
            # row then shows "same as docked" (or vanishes entirely), so the user cannot see it, let
            # alone clear it -- but applying it verbatim would still write a value the pack does not
            # offer. Decline it, matching what the resolution store already does with a stale name.
            if pk is not None and preset not in pk["options"].get(group, []):
                continue
            prev = cp._entry_preset(e, group) if e is not None else None
            if prev == preset:
                continue                              # already there -> nothing to undo later
            _rec(filename)["groups"].append({"group": group, "prev": prev, "applied": preset})

    if res is not None:
        filename, group, low = res
        e = cp._find(entries, filename)
        prev = cp._entry_preset(e, group) if e is not None else None
        if prev != low:
            _rec(filename)["groups"].append({"group": group, "prev": prev, "applied": low})

    return [r for r in by_file.values() if r["groups"] or r["applied_disabled"] is not None]


def _mutate(entries: list, recs: list) -> None:
    """Write the planned records into the entries model."""
    for rec in recs:
        filename = rec["filename"]
        e = cp._find(entries, filename)
        if rec["applied_disabled"] is False and e is None:
            e = {"filename": filename, "disabled": False, "presets": []}
            entries.append(e)
        if e is None:
            continue
        if rec["applied_disabled"] is not None:
            e["disabled"] = bool(rec["applied_disabled"])
        for g in rec["groups"]:
            _set_group_preset(e, g["group"], g["applied"])


def apply(rom: str) -> None:
    """Apply this Wii U game's handheld pack state when handheld. No-op unless: on-the-go enabled,
    HANDHELD, wiiu participating, Cemu closed, no marker outstanding, and something to change.
    Writes an atomic marker recording every previous value BEFORE mutating anything."""
    if proc_guard.emulator_running("cemu"):
        _log("apply skipped: Cemu is running")
        return
    try:
        pol = load_merged()
    except Exception:
        return
    if not _gates(pol):
        return
    tid = _tid_for_rom(rom)
    if not tid:
        return
    path = cp.settings_path()
    mk = _marker(path)
    if mk.exists():
        # The sweep that runs immediately before us kept its marker, which only happens when the
        # revert write failed. Applying now would overwrite the record of the DOCKED state and lose
        # it for good. Leave everything alone; the next sweep still has what it needs.
        _log("apply skipped: an unreverted marker is outstanding")
        return
    text = cfgutil.read_text(path)
    if text is None:
        return
    entries = cp._parse_graphicpack(text)
    try:
        recs = _plan(pol, tid, entries)
    except Exception:
        return
    if not recs:
        return
    try:
        _RES_DIR.mkdir(parents=True, exist_ok=True)
        tmp = mk.with_name(mk.name + ".tmp")
        tmp.write_text(json.dumps({"v": _MARKER_VERSION, "path": str(path), "tid": tid,
                                   "recs": recs}), encoding="utf-8")
        tmp.replace(mk)                               # atomic: complete-or-absent
        _mutate(entries, recs)
        new = cp._write_block(text, entries)
        if new != text:
            cfgutil.ensure_bak(path)
            cfgutil.atomic_write(path, new)
        for rec in recs:
            bits = [f"{g['group'] or '(unnamed)'}={g['applied']}" for g in rec["groups"]]
            if rec["applied_disabled"] is not None:
                bits.append("off" if rec["applied_disabled"] else "on")
            _log(f"applied {rec['filename']}: " + ", ".join(bits))
    except Exception:
        pass
