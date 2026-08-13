"""Wii Remote SOURCE decider + Classic-Controller launch rail -- the SOLE writer of WiimoteNew.ini.

Called at Wii game-start by hooks/game-start/dolphin-wii-mode.sh
    python3 -m lib.dolphin_wii_source apply "<rom>"
and reverted at game-end by hooks/game-end/dolphin-wii-cc-restore.sh
    python3 -m lib.dolphin_wii_source restore

One decision per launch, made AFTER sweeping any crashed-CC leftover:

    DolphinBar present            -> real / real2 by connected-remote count (lightgun AND non-lightgun)
    no bar, lightgun collection   -> Sinden (Source flip; the sweep + a contamination guard heal the body)
    no bar, CC-capable or forced  -> Classic Controller (docked: pads->players | handheld: seat pickers)
    no bar, style profile seats   -> per-seat ladder: players 1-4 each resolve pergame pick ->
                                     style default (sideways/nunchuk; handheld Classic likewise)
    no bar, otherwise             -> real  (today's behavior; the router shows the "no remote" warning)

"Forced" = a per-game override, `[backends.dolphin_wii.pergame.<GameID>].force_cc = true`, for a
data-gap game GameTDB has no CC record of (e.g. WiiWare like Retro City Rampage). It is consulted only
in the no-bar branch, so it applies to BOTH docked-no-bar and handheld. (It replaces the old global
`[backends.dolphin].cc_overrides` allowlist, which is retired.)

Only the CC branch is TRANSIENT: it snapshots WiimoteNew.ini.cc-backup and the game-end hook reverts
it. Because the snapshot is written BEFORE the CC bodies, a CC body in the gun slots ALWAYS implies a
consumable backup -- so the crash-orphan sweep (run first, every launch) restores the FULL resting gun
body (the on-disk Sinden profiles are only partial subsets, so we never rebuild from them except as a
last resort when contamination is detected without a backup).

Byte-safe: only targeted [WiimoteN] bodies are touched (block copy, lib.dolphin_wii_profiles); the
snapshot is a whole-file copy; atomic writes; Dolphin is closed at game-start. Everything degrades to
"leave the resting config" on any error -- the launch always continues.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from lib import deck_state, devices, dolphin_profiles, dolphin_wii_profiles, dolphin_wii_tdb
from lib.policy import load_merged

_DIR = Path.home() / ".var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu"
_FILE = _DIR / "WiimoteNew.ini"
_BACKUP = _DIR / "WiimoteNew.ini.cc-backup"       # transient snapshot (CC branch only)
_TOOL = Path(__file__).resolve().parent.parent / "dolphin-wii-mode.sh"   # the Source-only writer
_HANDHELD_DEFAULT = dolphin_wii_profiles.HANDHELD_DEFAULT
_SINDEN_P1 = "Sinden Lightgun P1"
_SINDEN_P2 = "Sinden Lightgun P2"
# Any EMULATED-profile body in the gun slots (the _apply_sinden rebuild check). Extended
# beyond Classic when non-Classic profiles became pickable (2026-08-04). IMPORTANT: this can
# match a LEGITIMATE resting body too (profiles are authored in Dolphin's own UI and may be
# left active), not just crash debris — which is why the rebuild snapshots first and is
# TRANSIENT (see _apply_sinden). A bare-Wiimote body (plain Buttons/ lines) stays undetected.
_CLASSIC_MARK = re.compile(r'(?mi)^(Extension[ \t]*=[ \t]*(Classic|Nunchuk)\b|Classic/|Nunchuk/)')

# Per-STYLE global default keys in [backends.dolphin_wii] (docked_key, handheld_key), the
# Player 1 seat; players 2-4 derive `<key>_p<n>` (see _seat_key). The CC style has no entry
# here: docked CC is the pads-to-players rail, handheld CC reads undocked_profile[_pN]
# directly (seat 1 keeps the built-in Deck default). The former "other" style has no keys
# any more (2026-08-04): pointer/unknown games seat only via a per-game pick.
_STYLE_KEYS = {"sideways": ("docked_profile_sideways", "undocked_profile_sideways"),
               "nunchuk": ("docked_profile_nunchuk", "undocked_profile_nunchuk")}
_SEATS = (1, 2, 3, 4)


def _seat_key(base: str, seat: int) -> str:
    """Player 1 keeps the legacy key name (live user data stays valid); 2-4 suffix _p<n>."""
    return base if seat == 1 else f"{base}_p{seat}"


def _be() -> dict:
    """[backends.dolphin] -- real2 threshold + the DolphinBar tool config (shared with route())."""
    be = (load_merged().get("backends") or {}).get("dolphin")
    return be if isinstance(be, dict) else {}


def _be_wii() -> dict:
    """[backends.dolphin_wii] -- the Classic Controller prefs (handheld undocked_profile, pads
    priority). SAME table the editor page (dolphin_wii_pads_cmds) reads/writes, so the handheld
    profile the decider loads matches what the user set."""
    be = (load_merged().get("backends") or {}).get("dolphin_wii")
    return be if isinstance(be, dict) else {}


def _read() -> str | None:
    try:
        return _FILE.read_text(encoding="utf-8", errors="replace") if _FILE.is_file() else None
    except OSError:
        return None


def _atomic_write(text: str) -> None:
    tmp = _FILE.with_suffix(_FILE.suffix + ".cc-tmp")
    tmp.write_text(text, encoding="utf-8", newline="")     # verbatim (preserve line endings)
    tmp.replace(_FILE)


def _is_docked() -> bool:
    """Physical dock/display state (deck_state), honoring the [handheld] force override. Fail-safe:
    docked on any error (-> the pads->players branch, which no-ops unless a priority is set)."""
    try:
        hh = load_merged().get("handheld")
        return deck_state.is_docked(deck_state.resolve_force(hh if isinstance(hh, dict) else None))
    except Exception:
        return True


def _is_lightgun(rom: str) -> bool:
    """True iff the ROM belongs to a require_sinden (lightgun) collection -- the same check the
    router's `lightgun-rom` mode uses. Fail-safe False (a gun game is never GameTDB-CC-capable, so it
    then lands on `real`, matching today's lightgun-rom failure fallback)."""
    try:
        from lib import es_collections as colls
        name = colls.collection_for_rom(str(rom))
        if not name:
            return False
        ent = (load_merged().get("collections") or {}).get(name) or {}
        return bool(ent.get("require_sinden"))
    except Exception:
        return False


# --------------------------------------------------------------------------- transient restore (CC)
def restore(logger=None) -> bool:
    """Revert a transient CC swap: copy the snapshot back over WiimoteNew.ini and drop it. No-op
    (False) when no snapshot exists. Idempotent -- safe at game-end AND as the crash-orphan sweep."""
    if not _BACKUP.is_file():
        return False
    try:
        tmp = _FILE.with_suffix(_FILE.suffix + ".cc-tmp")
        tmp.write_bytes(_BACKUP.read_bytes())
        tmp.replace(_FILE)
        _BACKUP.unlink()
        if logger:
            logger.info("dolphin_wii: restored resting WiimoteNew.ini after Classic Controller game")
        return True
    except OSError as ex:
        if logger:
            logger.warning(f"dolphin_wii: CC restore failed: {ex!r}")
        return False


def _snap_write(new_text: str, logger, msg: str) -> None:
    """Snapshot the resting WiimoteNew.ini (only when no backup survives -- apply() guarantees that)
    then write the transient CC swap. Never truncates (whole-file snapshot; temp+replace)."""
    try:
        if not _BACKUP.is_file():
            _BACKUP.write_bytes(_FILE.read_bytes())
        _atomic_write(new_text)
    except OSError as ex:
        if logger:
            logger.warning(f"dolphin_wii: could not apply Classic Controller: {ex!r}")
        return
    if logger:
        logger.info(f"dolphin_wii: {msg}")


# --------------------------------------------------------------------------- source modes
def _run_tool(mode: str, logger) -> None:
    """Delegate a real/real2/sinden Source flip to the existing single-writer tool. Source-only is
    correct for real (Source=2 makes Dolphin use the physical remote and ignore the emulated body)
    and for sinden here (the crash sweep + contamination guard keep the gun BODY correct).

    CRITICAL: the tool prints status lines to STDOUT, so we CAPTURE its output (never let it inherit
    our stdout). The game-start hook reads this process's stdout as the chosen mode -- a leaked tool
    banner would corrupt `$mode` and stop the real-Wiimote quit-watcher from starting. The captured
    output is forwarded to the logger (stderr -> the hook's log)."""
    if not _TOOL.is_file():
        if logger:
            logger.warning(f"dolphin_wii: {_TOOL} not found; leaving WiimoteNew.ini untouched")
        return
    try:
        r = subprocess.run([str(_TOOL), mode], check=False,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if logger:
            out = (r.stdout or "").strip().replace("\n", " | ")
            logger.info(f"dolphin_wii: source mode {mode!r}" + (f" ({out})" if out else ""))
    except OSError as ex:
        if logger:
            logger.warning(f"dolphin_wii: failed to run {_TOOL} {mode}: {ex!r}")


def _cc_contaminated(text: str) -> bool:
    """True iff [Wiimote1] or [Wiimote2] still holds Classic-Controller lines -- i.e. a CC body
    survived into a gun launch WITHOUT a consumable backup (the invariant was broken)."""
    for slot in ("Wiimote1", "Wiimote2"):
        body = dolphin_profiles._section_body(text, slot) or ""
        if _CLASSIC_MARK.search(body):
            return True
    return False


def _apply_sinden(logger) -> None:
    """Lightgun launch: normally just the Source flip -- the crash-orphan sweep (run first) has
    already reverted any CC leftover to the FULL resting gun body, so a Source-only flip keeps the
    rich live mapping intact. Last resort: if the gun slots STILL look like an emulated-profile
    body (Classic OR Nunchuk), rebuild [Wiimote1/2] from the on-disk gun profiles -- incomplete,
    but far better than dead guns. The rebuild SNAPSHOTS the pre-rebuild file first (2026-08-04
    review fix): now that profiles are authored in Dolphin's own UI, an emulated resting body can
    be a LEGITIMATE hand-authored setup, not just crash debris -- the snapshot makes the rebuild
    transient (game-end restore puts the resting body back) instead of destroying user data."""
    text = _read()
    if text is not None and _cc_contaminated(text):
        changed = False
        for slot, prof in ((1, _SINDEN_P1), (2, _SINDEN_P2)):
            body = dolphin_wii_profiles.profile_body(prof)
            if body is None:
                continue
            nt = dolphin_wii_profiles.apply_cc_body(text, f"Wiimote{slot}", body)
            if nt is not None:
                text, changed = nt, True
        if changed and _snap_backup(logger):           # never overwrite without a restorable copy
            try:
                _atomic_write(text)
                if logger:
                    logger.warning("dolphin_wii: gun slots held emulated-profile bindings; "
                                   "rebuilt from gun profiles (transient)")
            except OSError as ex:
                if logger:
                    logger.warning(f"dolphin_wii: gun-slot rebuild failed: {ex!r}")
    _run_tool("sinden", logger)


def _apply_cc(logger) -> None:
    """No-bar Classic Controller (transient). Docked -> the pads->players profile priority across
    [Wiimote1..4]; handheld -> the single Deck profile on [Wiimote1] (2..4 off)."""
    from lib import dolphin_wii_pads
    text = _read()
    if text is None:
        if logger:
            logger.warning("dolphin_wii: WiimoteNew.ini missing; skipping Classic Controller")
        return
    if _is_docked():
        new_text, applied = dolphin_wii_pads.assign_text(text)
        if not applied:
            if logger:
                logger.info("dolphin_wii: docked, no Classic Controller pad matched; leaving resting")
            return
        _snap_write(new_text, logger,
                    "docked Classic Controller -> "
                    + ", ".join(f"P{s}={n!r}" for s, n in applied) + " (transient)")
        return
    profile = str(_be_wii().get("undocked_profile", _HANDHELD_DEFAULT) or _HANDHELD_DEFAULT)
    body = dolphin_wii_profiles.profile_body(profile)
    if body is None:
        if logger:
            logger.warning(f"dolphin_wii: handheld CC profile {profile!r} not found; leaving resting")
        return
    nt = dolphin_wii_profiles.apply_cc_body(text, "Wiimote1", body)
    if nt is None:
        if logger:
            logger.warning("dolphin_wii: [Wiimote1] absent; skipping Classic Controller")
        return
    for slot in (2, 3, 4):
        nt = dolphin_wii_profiles.disable_slot(nt, f"Wiimote{slot}")
    _snap_write(nt, logger, f"handheld Classic Controller -> {profile!r} on [Wiimote1] (transient)")


# --------------------------------------------------------------------------- profile ladder
def _clean(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _explicit_pergame(be: dict, gid: str | None, docked: bool, seat: int = 1) -> str | None:
    """The per-game profile pick for this context+seat ([backends.dolphin_wii.pergame.<GameID>]
    docked_profile[_pN] / handheld_profile[_pN]), or None. Beats style detection per seat.
    `be` is the caller's single _be_wii() snapshot (the ladder loads policy once)."""
    if not gid:
        return None
    pg = (be.get("pergame") or {}).get(gid)
    if not isinstance(pg, dict):
        return None
    return _clean(pg.get(_seat_key("docked_profile" if docked else "handheld_profile", seat)))


def _force_cc_gid(be: dict, gid: str | None) -> bool:
    """force_cc by pre-resolved gid + pre-loaded policy snapshot (the ladder-internal variant
    of the public force_cc)."""
    if not gid:
        return False
    pg = (be.get("pergame") or {}).get(gid)
    return bool(pg.get("force_cc")) if isinstance(pg, dict) else False


def _style(be: dict, rom: str, gid: str | None) -> str:
    """The game's input style: cc | sideways | nunchuk | other. CC first (a pad-drivable game
    keeps the existing rails); the CURATED sideways list beats the derived nunchuk fact
    (GameTDB lists an optional nunchuk on many sideways-primary games, e.g. NSMB Wii)."""
    if _cc_capable(rom) or _force_cc_gid(be, gid):
        return "cc"
    try:
        if gid and dolphin_wii_tdb.is_sideways(gid):
            return "sideways"
        if gid and dolphin_wii_tdb.is_nunchuk(gid):
            return "nunchuk"
    except Exception:
        pass
    return "other"


def style(rom_or_id) -> str:
    """PUBLIC: the game's input-style token (cc|sideways|nunchuk|other) — the ONE ladder the
    launch decider AND the UI labels read (dolphin_profile_cmds.wii_style delegates here, so
    the two can never drift). Fail-safe "other"."""
    try:
        s = str(rom_or_id)
        try:
            gid = dolphin_wii_tdb._resolve(s) or None
        except Exception:
            gid = None
        return _style(_be_wii(), s, gid)
    except Exception:
        return "other"


def _style_global(be: dict, style_token: str, docked: bool, seat: int = 1) -> str | None:
    """The [backends.dolphin_wii] per-style default for this context+seat, or None."""
    keys = _STYLE_KEYS.get(style_token)
    if not keys:
        return None
    return _clean(be.get(_seat_key(keys[0] if docked else keys[1], seat)))


def _snap_backup(logger) -> bool:
    """Take the transient WiimoteNew.ini snapshot WITHOUT writing anything else — used before
    a Sinden PICK's Source flip (and _apply_sinden's rebuild) so the game-end restore reverts
    it. False (with a warning) if the snapshot could not be taken."""
    try:
        if not _BACKUP.is_file():
            _BACKUP.write_bytes(_FILE.read_bytes())
        return True
    except OSError as ex:
        if logger:
            logger.warning(f"dolphin_wii: could not snapshot before Sinden apply: {ex!r}")
        return False


def _seat_candidates(be: dict, gid: str | None, style_token: str, docked: bool,
                     seat: int) -> list[tuple[str, str]]:
    """ONE seat's ladder rungs, unvalidated, in order: [(profile, rung), ...]. The per-game
    pick beats the style default per seat. Handheld Classic reads undocked_profile[_pN]
    directly (no _STYLE_KEYS entry); its seat 1 keeps the built-in Deck default. Docked
    Classic has NO style rung (the pads-to-players rail owns non-explicit docked CC)."""
    out: list[tuple[str, str]] = []
    pg = _explicit_pergame(be, gid, docked, seat)
    if pg is not None:
        out.append((pg, "explicit"))
    if style_token in _STYLE_KEYS:
        sg = _style_global(be, style_token, docked, seat)
        if sg is not None:
            out.append((sg, style_token))
    elif style_token == "cc" and not docked:
        v = _clean(be.get(_seat_key("undocked_profile", seat)))
        if v is None and seat == 1:
            v = _HANDHELD_DEFAULT
        if v is not None:
            out.append((v, "cc"))
    return out


def _seat_pick(be: dict, gid: str | None, style_token: str, docked: bool, seat: int,
               logger=None) -> tuple[str | None, str | None]:
    """ONE seat's validated pick: (profile, rung) or (None, None). Mirrors the shipped
    two-rung fall-through PER SEAT: a stale pick (profile file missing) warns and falls to
    the next rung — the pickers keep stale picks visible for clearing, so this state is
    expected. A Sinden* stem is only meaningful on seat 1 (a whole-launch gun mode, not a
    body copy — the caller routes it through _apply_sinden); on seats 2-4 it is skipped
    like a stale pick so a lower rung can still seat that player."""
    for prof, rung in _seat_candidates(be, gid, style_token, docked, seat):
        if dolphin_wii_profiles.is_sinden(prof):
            if seat == 1:
                return prof, rung          # gun mode; never body-validated here
            if logger:
                logger.warning("dolphin_wii: a Sinden profile is a whole-launch gun mode; "
                               f"ignoring {prof!r} on player {seat}")
            continue
        if dolphin_wii_profiles.profile_body(prof) is None:
            if logger:
                logger.warning(f"dolphin_wii: player {seat} profile {prof!r} not found; "
                               "falling through")
            continue
        return prof, rung
    return None, None


def _resolved_seats(rom: str, logger=None,
                    docked: bool | None = None) -> tuple[dict[int, str], str]:
    """The no-bar ladder for EVERY seat, VALIDATED: ({seat: profile}, rung). rung keeps the
    old _resolved_pick contract for seat 1 ("explicit" | "cc" | <style>); with no seat
    resolved it is the detected style. ({}, "cc") on a DOCKED Classic game with no explicit
    Player 1 pick: the multi-pad pads-to-players rail owns that launch and seat rows are
    deliberately not consulted (they would fight the pad assignment). A seat-1 Sinden pick
    short-circuits (gun mode; other seats are meaningless). A duplicate-stem post-pass
    drops later seats repeating an earlier seat's profile — one authored profile drives one
    pad; copied twice it would mirror, not split."""
    if docked is None:
        docked = _is_docked()
    be = _be_wii()
    try:
        gid = dolphin_wii_tdb._resolve(rom) or None
    except Exception:
        gid = None
    style_token = _style(be, rom, gid)
    p1, rung1 = _seat_pick(be, gid, style_token, docked, 1, logger)
    if style_token == "cc" and docked and p1 is None:
        return {}, "cc"
    seats: dict[int, str] = {}
    rungs: dict[int, str] = {}
    if p1 is not None:
        seats[1] = p1
        rungs[1] = rung1 or style_token
        if dolphin_wii_profiles.is_sinden(p1):
            return seats, rung1 or style_token
    for n in _SEATS[1:]:
        pn, rn = _seat_pick(be, gid, style_token, docked, n, logger)
        if pn is not None:
            seats[n] = pn
            rungs[n] = rn or style_token
    seen: dict[str, int] = {}
    for n in sorted(seats):
        prof = seats[n]
        m = seen.get(prof)
        if m is None:
            seen[prof] = n
            continue
        # Duplicate stem: an EXPLICIT per-game pick beats an inherited global seat even on
        # a higher player number ("a per-game pick wins" must hold per stem, not per slot);
        # otherwise the lower seat wins.
        if rungs[n] == "explicit" and rungs[m] != "explicit":
            if logger:
                logger.warning(f"dolphin_wii: profile {prof!r} is picked for players {m} "
                               f"and {n}; keeping the explicit per-game pick on player {n} "
                               "(a profile is bound to the one pad it was authored on)")
            del seats[m]
            seen[prof] = n
        else:
            if logger:
                logger.warning(f"dolphin_wii: profile {prof!r} is picked for players {m} "
                               f"and {n}; ignoring player {n} (a profile is bound to the "
                               "one pad it was authored on)")
            del seats[n]
    return seats, (rung1 or style_token)


def sinden_pick(rom: str) -> bool:
    """True iff a no-bar launch of this WII rom resolves to a Sinden profile PICK — PUBLIC:
    controller-router's `lightgun-rom` mode consults it so the game-start sinden.sh hook
    starts the gun DRIVER for a picked (non-collection) lightgun game; without this the pick
    would flip the config but the gun would never track. Fail-safe False; only fires for a
    rom under a wii ROM dir with no DolphinBar (a bar launch never reaches the ladder)."""
    try:
        path = str(rom).replace("\\", "/").lower()
        if "/wii/" not in path:
            return False
        if devices.dolphinbar_present():
            return False
        seats, _rung = _resolved_seats(rom)
        p1 = seats.get(1)
        return bool(p1 and dolphin_wii_profiles.is_sinden(p1))
    except Exception:
        return False


def _apply_seats(seats: dict[int, str], docked: bool, logger) -> bool:
    """Load each seat's authored profile body into its [Wiimote<n>] (Source=1) and turn OFF
    every unfilled slot 1-4, in ONE transient _snap_write — the multi-seat successor of the
    old _apply_single, mirroring dolphin_wii_pads.assign_text's shape (fill, disable the
    rest, single snapshot write). A seat that fails body-load or header-injection is skipped
    WITH a warning; the rest still seat. False on a total miss (file missing or NO seat
    applied) — the caller falls through to the legacy behavior, so a stale state can never
    brick a launch. Sinden picks are routed by the CALLER through _apply_sinden, never here."""
    text = _read()
    if text is None:
        if logger:
            logger.warning("dolphin_wii: WiimoteNew.ini missing; skipping profiles")
        return False
    applied: list[tuple[int, str]] = []
    filled: set[int] = set()
    for n in sorted(seats):
        body = dolphin_wii_profiles.profile_body(seats[n])
        if body is None:
            if logger:
                logger.warning(f"dolphin_wii: player {n} profile {seats[n]!r} not found; "
                               "seat left empty")
            continue
        nt = dolphin_wii_profiles.apply_cc_body(text, f"Wiimote{n}", body)
        if nt is None:
            if logger:
                logger.warning(f"dolphin_wii: [Wiimote{n}] absent; player {n} left unseated")
            continue
        text = nt
        filled.add(n)
        applied.append((n, seats[n]))
    if not applied:
        return False
    for n in _SEATS:
        if n not in filled:
            text = dolphin_wii_profiles.disable_slot(text, f"Wiimote{n}")   # no-op if absent
    _snap_write(text, logger, f"{'docked' if docked else 'handheld'} profiles -> "
                + ", ".join(f"P{n}={m!r}" for n, m in applied) + " (transient)")
    return True


def profile_override(rom: str) -> bool:
    """True iff a no-bar launch of this ROM will receive at least one emulated profile seat
    (VALIDATED per-game picks or style defaults — stale picks do not count, so the warning
    still fires exactly when the launch would actually fall through to `real`) — PUBLIC:
    dolphin_cfg.route consults it so a covered game shows no spurious "no DolphinBar"
    warning (the force_cc invariant). The CC rung returns False here (route's own CC check
    already covers it). Fail-safe False."""
    try:
        seats, rung = _resolved_seats(rom)
        return bool(seats) and rung != "cc"
    except Exception:
        return False


def plan() -> dict:
    """Rom-less preview of the no-bar profile ladder for the CURRENT context (mirrors
    dolphin_gc_dock.plan's contract: Preview renders what the launch decider acts on, never a
    re-derivation). Per-game picks and the Sinden collection can differ per title — the
    pickers' notes carry that caveat.

    {"mode": "docked"|"handheld",
     "styles": {"sideways"|"nunchuk": {seat: profile}},     # only SET seats appear
     "cc": {seat: profile}}                                 # empty = nothing seats

    The docked "cc" used to be the literal string "pads-to-players order" -- the NAME of the
    mechanism instead of its answer, which is exactly the re-derivation this contract exists to
    forbid, and it read on screen as "Classic -> pads-to-players order", 622px into a 614px
    column, so it also ran off the right edge. It now resolves through
    dolphin_wii_pads.assign_text, the same call _apply_cc makes, so both contexts return the
    same shape and the page can name real profiles.

    Not free of side conditions: this enumerates input devices and reads WiimoteNew.ini, where
    the old docked branch was pure config. Its one caller is the Preview page (measured 1.46s
    cold, 0.026s warm, and _preview_all pumps SDL before its route loop, so it is always the
    warm path). Nothing on a launch path calls plan()."""
    docked = _is_docked()
    be = _be_wii()
    styles: dict[str, dict[int, str]] = {}
    for s, keys in _STYLE_KEYS.items():
        base = keys[0] if docked else keys[1]
        styles[s] = {n: v for n in _SEATS
                     if (v := _clean(be.get(_seat_key(base, n)))) is not None}
    if docked:
        # assign_text, NOT plan_assignment. The plan says which profiles WANT a slot;
        # assign_text is what _apply_cc actually calls, and it drops any seat whose profile
        # body will not load or whose [WiimoteN] section is missing from WiimoteNew.ini, then
        # refuses the whole rewrite if nothing landed. On a file trimmed to [Wiimote2..4] the
        # plan promises P1 and P2 while the launch seats one -- a phantom row. Pure text: it
        # is handed the file's CONTENTS and returns a new string we throw away, so nothing is
        # written. Same shape the cemu branch of the Preview page already uses, and for the
        # same stated reason.
        try:
            from lib import dolphin_wii_pads
            cc = dict(dolphin_wii_pads.assign_text(_read() or "")[1])
        except Exception:
            cc = {}
    else:
        cc = {n: v for n in _SEATS
              if (v := _clean(be.get(_seat_key("undocked_profile", n)))) is not None}
        cc.setdefault(1, _HANDHELD_DEFAULT)
    return {"mode": "docked" if docked else "handheld", "styles": styles, "cc": cc}


# --------------------------------------------------------------------------- the decision
def _wiimote_count() -> int:
    try:
        return int(devices.dolphinbar_wiimotes())
    except Exception:
        return 0


def _cc_capable(rom: str) -> bool:
    try:
        return dolphin_wii_tdb.is_cc_capable(rom)
    except Exception:
        return False                                   # fail-closed


def force_cc(rom: str) -> bool:
    """A per-game override: `[backends.dolphin_wii.pergame.<GameID>].force_cc = true` forces the
    Classic Controller for a data-gap game GameTDB has no CC record of (e.g. WiiWare). Consulted only
    in the no-bar branch, so it covers docked-no-bar AND handheld. Fail-safe False. PUBLIC: the
    router's warning (dolphin_cfg.route) also consults it so a forced game shows no spurious "no
    DolphinBar" dialog. Resolves the id exactly as is_cc_capable does, so the stored GameID matches."""
    try:
        gid = dolphin_wii_tdb._resolve(rom)
        return _force_cc_gid(_be_wii(), gid or None)
    except Exception:
        return False


def apply(rom: str, logger=None) -> str:
    """Guarded entry point. The game-start hook launches the game regardless, so ANY unexpected error
    degrades to "skip" (leave the resting config) rather than aborting the launch."""
    try:
        return _run_decision(rom, logger)
    except Exception as ex:                            # never let a probe/IO error break a launch
        if logger:
            logger.warning(f"dolphin_wii: apply aborted ({ex!r}); leaving resting config")
        return "skip"


def _run_decision(rom: str, logger=None) -> str:
    """Decide + apply the Wii Remote source for this launch; return the chosen mode
    (real|real2|sinden|classic|skip). Sweeps any crashed-CC leftover FIRST."""
    restore(logger)                                    # crash-orphan sweep (no-op without a leftover)
    if _BACKUP.is_file():                              # restore() failed to consume a surviving backup
        if logger:
            logger.warning("dolphin_wii: leftover CC backup survived; leaving config untouched")
        return "skip"
    if devices.dolphinbar_present():                   # USB-level presence (NOT the awake-remote count)
        real2_min = int(_be().get("real2_min_wiimotes", 2))
        mode = "real2" if _wiimote_count() >= real2_min else "real"
        _run_tool(mode, logger)
        return mode
    if _is_lightgun(rom):
        _apply_sinden(logger)
        return "sinden"
    # No bar, not a Sinden-collection game: the PER-SEAT PROFILE LADDER (multi-seat
    # 2026-08-04) — for each player 1-4: validated per-game pick -> that style's validated
    # global default. Docked Classic with no explicit Player 1 pick keeps the unchanged
    # multi-pad pads-to-players rail (seat rows deliberately not consulted); handheld
    # Classic seats via the pickers (seat 1 keeps the built-in Deck default). Stale picks
    # warn + fall through PER SEAT, duplicates are dropped (a profile drives the one pad it
    # was authored on), so nothing here can brick a launch OR mask a still-valid lower
    # rung. Byte-identical to the old behavior when nothing new is configured. A seat-1
    # Sinden pick reroutes through the FULL sinden mode (both gun slots + scanning flag;
    # the DRIVER start is covered by sinden_pick() via the lightgun-rom gate) and is made
    # TRANSIENT by snapshotting first — the game-end restore then reverts the Source flips.
    # (The Dolphin.ini WiimoteContinuousScanning=False the sinden tool writes is not
    # snapshotted — same residue the collection path has always had.)
    docked = _is_docked()
    seats, rung = _resolved_seats(rom, logger, docked)
    p1 = seats.get(1)
    # The Sinden check runs BEFORE the cc branch: a Sinden stem can reach seat 1 through
    # the handheld-Classic rung too (hand-edited undocked_profile), and it must ALWAYS take
    # the full gun mode — sinden_pick() gates the gun driver on exactly this resolution, so
    # a body-copy here would start the driver against a non-gun config.
    if p1 is not None and dolphin_wii_profiles.is_sinden(p1):
        _snap_backup(logger)                           # transient: game-end reverts the flip
        _apply_sinden(logger)
        return "sinden"
    if rung == "cc":
        if docked:                                     # GameTDB CC-capable / force_cc: old rail
            _apply_cc(logger)
        else:                                          # handheld Classic: the seat pickers
            _apply_seats(seats, docked, logger)
        return "classic"
    if seats and _apply_seats(seats, docked, logger):
        return "classic"
    if _cc_capable(rom) or force_cc(rom):              # legacy fallback
        _apply_cc(logger)
        return "classic"
    _run_tool("real", logger)                          # no bar, nothing configured -> today's behavior
    return "real"


# --------------------------------------------------------------------------- CLI (the hooks)
def _main(argv: list[str]) -> int:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("dolphin_wii")
    mode = argv[1] if len(argv) > 1 else ""
    rom = argv[2] if len(argv) > 2 else ""
    if mode == "apply":
        print(apply(rom, log))                         # the hook reads this to start the quit-watcher on real*
        return 0
    if mode == "restore":
        restore(log)
        return 0
    print("usage: python3 -m lib.dolphin_wii_source {apply <rom>|restore}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
