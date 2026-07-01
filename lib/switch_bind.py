"""Launch-time controller binding for ES-DE standalone emulators (Switch:
Ryujinx + Eden; plus PCSX2 — the Standalones migration adds one emulator at a
time via `_write`/`_target`/`_snapshot` branches + `pads_cmds._EMUS`).

Flow (Steam Input OFF for ES-DE, so the emulator sees raw pads):
  • `mad-switch-launch.py <emu> <rom> -- <cmd>` calls bind(), then execs the
    emulator (becoming it — so nothing matching the quit-combo's `pkill -f
    'Ryujinx|Eden|…'` lingers as a separate wrapper process).
  • bind() rewrites ONLY the input portion of the emulator's config (Ryujinx
    per-game by titleid, else global; Eden global) to the connected pads in the
    user's stored priority order, and writes a sidecar `<config>.mad-restore`
    recording {emu, snapshot-of-the-input}.
  • An ES-DE game-end hook calls restore_all(), which finds every sidecar and
    re-applies its snapshot — reverting the input to the on-the-go (Steam-direct)
    default while KEEPING every SETTING (60 FPS mod, graphics, res scale) the
    emulator wrote. The hook fires whether the game exited normally or was
    quit-combo-killed, so the restore is robust.

The SDL slot index in the Ryujinx id is computed in the launch session, so it
matches what the emulator enumerates moments later. Everything is best-effort: a
failure here must never block the game launch.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from . import eden_cfg, fsutil, inifile, mad_paths, pcsx2_cfg, rpcs3_cfg, xemu_cfg
from .madsrv import pads_cmds, ryujinx_cfg, ryujinx_json

_RYUJINX_GLOBAL = Path.home() / ".config/Ryujinx/Config.json"
_RYUJINX_GAMES = Path.home() / ".config/Ryujinx/games"
_EDEN_INI = Path.home() / ".config/eden/qt-config.ini"
_PCSX2_INI = Path.home() / ".config/PCSX2/inis/PCSX2.ini"
# pcsx2x6 (Namco 246/256 fork) runs -portable, so its ini lives beside the AppImage.
_PCSX2X6_INI = Path.home() / "Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini"
# Retail GunCon2 split: the same pcsx2x6 AppImage launched with
# `-datapath ~/Applications/pcsx2x6-retail` (data root = <datapath>/PCSX2x6, per the
# fork's EmuFolders::SetDataDirectory). A SEPARATE ini so the binder + cursor-freeze
# strip never touch the Namco arcade portable config.
_PS2GUNCON_INI = Path.home() / "Applications/pcsx2x6-retail/PCSX2x6/inis/PCSX2.ini"
_XEMU_TOML = Path.home() / ".var/app/app.xemu.xemu/data/xemu/xemu/xemu.toml"
_RPCS3_YML = Path.home() / ".config/rpcs3/input_configs/global/Default.yml"
_PLAYER_RE = re.compile(r"Player \d+ Input$")
_TITLEID_RE = re.compile(r"\[([0-9A-Fa-f]{16})\]")
_PLAYERS = {"ryujinx": 8, "eden": 8, "pcsx2": 8, "pcsx2x6": 2, "ps2guncon": 2, "xemu": 4, "rpcs3": 7}   # HARDWARE-MAX slots: sizes the snapshot/restore + the writer's null-out range below. The per-launch BIND CAP is pads_cmds.managed_players(emu) (policy-driven; pcsx2 default 2 = no multitap, opt in to 4). Keep pcsx2=8 here so an opt-in 4-player launch still nulls Pad1..8 and can't leak phantom pads.
# TRANSIENT emulators snapshot their input before binding and restore it on exit.
# CRITERION (the default for EVERY writer-backed standalone): the emulator is ALSO
# launched via the Steam UI on the go — Steam Input ON, so it sees the virtual Deck
# pad (28de:11ff), different from the RAW pads ES-DE sees (Steam Input OFF) — while
# sharing ONE config file. So an ES-DE bind must revert on exit, leaving the
# Steam-UI-compatible resting config. The user runs Switch AND PS2 (and others) this
# way. (RetroArch does the same via per-game reservations stripped by the game-end
# cleanup hook; OpenBOR self-reads a whitelist so has no config to revert.)
_TRANSIENT = {"ryujinx", "eden", "pcsx2", "xemu", "rpcs3"}
# pcsx2x6 (Namco 246/256) is deliberately NON-transient: it is launched ONLY from
# ES-DE (never the Steam UI on the go), so there is no second context to revert for.
# Persisting the bind means its [Pad1] keeps a real DualShock2 SDL block after the
# first pad launch, which is what makes the Input-mapping page editable (the portable
# ini ships with a keyboard [Pad1] that has no SDL button keys to remap). Hands-off
# still skips the bind entirely for anyone who wants the keyboard config left alone.
# PCSX2's "input" = its [PadN] slot sections PLUS the [Pad] control section (which holds
# MultitapPort1/2 — the writer toggles those for 3+ players). All revert on exit. The per-game
# input feature's transient [USB1]/[USB2] overrides are NOT here: they are snapshotted LAZILY, only
# when a game actually sets a USB override (see _apply_pcsx2_pergame_ports), so a normal launch never
# touches USB config and a stale pre-feature sidecar can't leak an unreverted USB write.
_PCSX2_SECTIONS = ("Pad",) + tuple(f"Pad{k}" for k in range(1, _PLAYERS["pcsx2"] + 1))
_SIDECAR_SUFFIX = ".mad-restore"
_LOG_FILE = mad_paths.storage("controller-router", "router.log")


# MAD_DEBUG=1 raises launch-binder verbosity (deeper _resolve_pads detail) without
# editing code; default off = zero added per-launch spam. Also flips the router logger
# to DEBUG (see controller-router.py _setup_logging).
_DEBUG = os.environ.get("MAD_DEBUG") == "1"


def _log(msg: str) -> None:
    line = f"mad-switch: {msg}"
    print(line, file=sys.stderr, flush=True)
    try:                                  # persist (the wrapper's stderr is lost in Game Mode)
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _dbg(msg: str) -> None:
    """Verbose line — emitted only when MAD_DEBUG=1."""
    if _DEBUG:
        _log(msg)


def _log_sdl_view() -> None:
    """Log the full SDL enumeration as Ryujinx-format ids, so a launch-time index
    mismatch (the wrapper's view vs the emulator's) is visible in the log."""
    try:
        from .madsrv import ryujinx_cfg as _rc
        sdl = pads_cmds.sdl_devices()
        _log("sdl view: " + " | ".join(
            f"idx{d.index} pidx{d.player_index} {d.vidpid} '{d.name}' -> {_rc.ryujinx_id(d.index, d.guid)}"
            for d in sdl))
    except Exception as e:
        _log(f"sdl view unavailable ({e!r})")


def _titleid(rom: str) -> str | None:
    m = _TITLEID_RE.search(Path(rom).name)
    return m.group(1).lower() if m else None


def _target(emu: str, rom: str) -> Path:
    """The config file the launched game actually reads."""
    if emu == "pcsx2":
        return _PCSX2_INI
    if emu == "pcsx2x6":
        return _PCSX2X6_INI
    if emu == "ps2guncon":
        return _PS2GUNCON_INI
    if emu == "xemu":
        return _XEMU_TOML
    if emu == "rpcs3":
        return _RPCS3_YML
    if emu == "eden":
        return _EDEN_INI
    tid = _titleid(rom)
    if tid:
        per = _RYUJINX_GAMES / tid / "Config.json"
        if per.is_file():
            return per
    return _RYUJINX_GLOBAL


def _sidecar(target: Path) -> Path:
    return target.with_name(target.name + _SIDECAR_SUFFIX)


def _resolve_pads(emu: str, order=None):
    """Top-N supported connected pads by the stored priority. Reuses pads_cmds;
    runs in the launch session so SDL indices match the emulator's.

    `order` (optional, PCSX2 per-game): a type-priority list that overrides the stored
    global order for THIS launch. Applied BEFORE the managed_players truncation below, so
    a per-game order can promote a normally-excluded pad into the top-N players.

    HANDHELD FALLBACK: the Deck's built-in pad (the emulator's `handheld_class`) is
    bound ONLY when no external pad is present — so docked play uses the external
    pad(s), and ES-DE on the go falls back to the Deck for Player 1."""
    real = pads_cmds._real_pads()
    pads = pads_cmds._supported(emu, real)
    ordered = pads_cmds._ordered(emu, pads, real, order=order)
    hh = pads_cmds._handheld_class(emu)
    external = [d for d in ordered if d.vidpid != hh] if hh else ordered
    chosen = external if external else ordered      # Deck only when nothing else
    _dbg(f"{emu}: supported={[d.vidpid for d in pads]} ordered={[d.vidpid for d in ordered]} "
         f"handheld_class={hh!r}")
    if hh and not external:
        _log(f"{emu}: no external pad -> handheld fallback to Deck ({hh})")
    elif hh:
        _log(f"{emu}: external pad(s) present -> using them (Deck fallback skipped)")
    return chosen[: pads_cmds.managed_players(emu)]


# In Game Mode, Steam owns the SDL player numbers PCSX2 binds by (it can't be predicted or
# forced from here — all verified on-device). So we READ PCSX2's own numbering from its emulog
# and reuse it. It is stable for a fixed controller set, and self-heals: every PCSX2 run
# rewrites the emulog for the next launch, so a controller change costs one "learning" launch.
_PCSX2_EMULOG = Path.home() / ".config/PCSX2/logs/emulog.txt"
_PCSX2_OPENED_RE = re.compile(
    r"Opened (?:gamepad|joystick) \d+ \(instance id \d+, player id (-?\d+)\): (.+)")


def _norm_pad_name(name: str) -> str:
    """Normalise an SDL controller name for matching across SDL2 (our launcher) and SDL3
    (PCSX2), which print slightly different names for the same pad (e.g. 'Xbox 360 Wireless
    Controller' vs 'Xbox 360 Controller')."""
    s = (name or "").lower()
    for drop in ("wireless", "controller", "gamepad"):
        s = s.replace(drop, "")
    return re.sub(r"\s+", " ", s).strip()


def _pcsx2_emulog_slots() -> dict:
    """PCSX2's OWN controller numbering from its last run: {normalised name -> [SDL-N, ...]}
    (a list per name so two same-model pads each get a slot). Empty if the emulog is absent."""
    out: dict = {}
    try:
        text = _PCSX2_EMULOG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for pid, name in _PCSX2_OPENED_RE.findall(text):
        n = int(pid)
        if n >= 0:
            out.setdefault(_norm_pad_name(name), []).append(n)
    return out


# Per-setup calibration cache: PCSX2's numbering depends on the WHOLE connected set, so a
# learned mapping is keyed by that set's vid:pid signature. A run's emulog belongs to the set
# recorded as "pending" at the previous bind, so we associate it correctly and cache it. Result:
# a setup is "learned" once (a single learning launch ever); switching back to a known setup is
# correct on the FIRST launch (no relearn). Stored in storage/, not the policy (machine state).
_PCSX2_CALIB_CACHE = mad_paths.storage("controller-router", "pcsx2-calibration.json")


def _pad_signature() -> str:
    """Stable key for the current connected controller set (sorted vid:pid multiset)."""
    return ",".join(sorted(d.vidpid for d in pads_cmds.sdl_devices()))


def _load_calib_cache() -> dict:
    try:
        d = json.loads(_PCSX2_CALIB_CACHE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_calib_cache(data: dict) -> None:
    try:
        _PCSX2_CALIB_CACHE.parent.mkdir(parents=True, exist_ok=True)
        fsutil.atomic_write_text(_PCSX2_CALIB_CACHE, json.dumps(data))
    except OSError:
        pass


def _covers(emu_map: dict, names: list) -> bool:
    """True if `emu_map` has a slot for every (duplicate-counted) name in `names` — i.e. the
    emulog plausibly came from a launch that actually had these pads. Rejects a foreign emulog
    (a PCSX2 run started OUTSIDE the MAD wrapper, e.g. Desktop/EmuDeck with a different controller
    set) so it can neither be filed under the wrong signature nor used as a best-effort source."""
    need: dict = {}
    for n in names:
        need[n] = need.get(n, 0) + 1
    return all(len(emu_map.get(n, [])) >= c for n, c in need.items())


def _calibrate_pcsx2(chosen):
    """Rewrite each chosen pad's index to the SDL-N PCSX2 gives that controller, read from
    PCSX2's own emulog and cached per controller set. The last run's emulog belongs to the set
    recorded as `pending` at the previous bind, so we file it under that signature; then we look
    up the CURRENT set's cached mapping (a known setup is right on the first launch). A pad with
    no cached slot keeps its raw index as a best-effort fallback; this run refreshes the cache so
    the next launch is correct. Returns (pads_with_calibrated_index, log_detail)."""
    cur_sig = _pad_signature()
    cur_pads = sorted(_norm_pad_name(d.name) for d in chosen)
    cache = _load_calib_cache()
    maps = cache.get("maps") if isinstance(cache.get("maps"), dict) else {}
    emu_map = _pcsx2_emulog_slots()
    last_sig = cache.get("pending")
    last_pads = cache.get("pending_pads") or []
    # File the emulog under the previously-launched set ONLY if it actually covers the pads MAD
    # bound then; otherwise a PCSX2 run started outside the wrapper (different controllers) would
    # mis-file its emulog under last_sig and poison a good learned mapping.
    if emu_map and last_sig and _covers(emu_map, last_pads):
        maps[last_sig] = emu_map
    cache["maps"] = maps
    cache["pending"] = cur_sig               # this launch produces the emulog for cur_sig
    cache["pending_pads"] = cur_pads
    _save_calib_cache(cache)
    src = maps.get(cur_sig)                   # learned mapping for the CURRENT set (no relearn if known)
    if not src and _covers(emu_map, cur_pads):  # never-seen set: trust the last emulog only if it has our pads
        src = emu_map
    pool = {k: list(v) for k, v in (src or {}).items()}
    out, detail = [], []
    for d in chosen:
        ids = pool.get(_norm_pad_name(d.name))
        if ids:
            slot = ids.pop(0)
            out.append(d._replace(index=slot))
            detail.append((d.vidpid, f"SDL-{slot}"))
        else:
            out.append(d)
            detail.append((d.vidpid, f"raw SDL-{d.index}"))
    return out, detail


def _snapshot(emu: str, target: Path):
    """The input portion to restore later (input only — never settings), for the
    TRANSIENT emulators."""
    if emu == "ryujinx":
        return ryujinx_json.load(target).get("input_config", [])
    if emu == "rpcs3":   # RPCS3 owns the `Player N Input` blocks (YAML doc).
        data = rpcs3_cfg.yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        return {k: v for k, v in data.items() if _PLAYER_RE.match(k)}
    text = target.read_text(encoding="utf-8", errors="replace")
    if emu == "pcsx2":   # PCSX2 owns [Pad] (multitap) + the [PadN] sections.
        # Record absent sections as None so restore can DELETE the ones the bind adds
        # (the writer always creates [Pad] + [Pad1..8]); else multitap/phantom pads
        # would drift into a later Steam-UI launch. None round-trips via the sidecar JSON.
        return {n: inifile.section_body(text, n) for n in _PCSX2_SECTIONS}
    # Record an absent section as None (like PCSX2 above) so restore DELETES the section
    # the bind added, instead of leaving a phantom empty [input.bindings]/[Controls] that
    # would drift into a later Steam-UI launch.
    if emu == "xemu":    # xemu owns the [input.bindings] section.
        return inifile.section_body(text, "input.bindings")
    return inifile.section_body(text, "Controls")


def _write(emu: str, target: Path, pads, overrides=None):
    """Write the resolved pads to the emulator's INPUT config (input only — button
    maps + settings untouched) and RETURN the writer's summary dict (what was actually
    written — slots/GUIDs/device strings/multitap flags) so bind() can log it. One
    branch per emulator; add an entry here plus `pads_cmds._EMUS` to onboard a new one.
    `overrides` (pcsx2 only) lets bind() pass per-game bind overrides merged over the global."""
    if emu == "ryujinx":
        return ryujinx_cfg.assign_devices(pads, config_path=target)
    if emu == "pcsx2":
        ov = overrides if overrides is not None else pcsx2_cfg.load_input_overrides(target)
        return pcsx2_cfg.assign_devices(pads, ini_path=str(target), manage=_PLAYERS["pcsx2"],
                                        overrides=ov)
    if emu == "pcsx2x6":   # same PCSX2 writer, pointed at the portable ini
        return pcsx2_cfg.assign_devices(pads, ini_path=str(target), manage=_PLAYERS["pcsx2x6"],
                                        overrides=pcsx2_cfg.load_input_overrides(target))
    if emu == "ps2guncon":   # retail GunCon2 split: same PCSX2 writer, pointed at the retail ini
        return pcsx2_cfg.assign_devices(pads, ini_path=str(target), manage=_PLAYERS["ps2guncon"],
                                        overrides=pcsx2_cfg.load_input_overrides(target))
    if emu == "xemu":
        return xemu_cfg.assign_devices(pads, config_path=str(target), manage=_PLAYERS["xemu"])
    if emu == "rpcs3":
        return rpcs3_cfg.assign_devices(pads, config_path=str(target), manage=_PLAYERS["rpcs3"])
    return eden_cfg.assign_devices(pads, ini_path=str(target), manage=_PLAYERS["eden"])


def _pcsx2_pergame(emu: str, rom: str):
    """The per-game input override for the launching PS2 disc, or None. Resolves the ROM to
    its <SERIAL>_<CRC> via PCSX2's game list and reads the MAD per-game input store. Skips the
    (cache-parsing) lookup entirely when the store file is absent — the common no-overrides case."""
    if emu != "pcsx2":
        return None
    try:
        from .madsrv import pcsx2_games, pcsx2_pergame_input_cmds as pgin
        if not pgin._STORE.exists():
            return None
        key = pcsx2_games.path_to_key(rom)
        return pgin.load_entry(key) if key else None
    except Exception as e:
        _log(f"pcsx2: per-game input lookup failed ({e!r})")
        return None


def _merge_overrides(base: dict, pergame_binds: dict) -> dict:
    """Global per-player bind overrides with the per-game binds layered on top (per-game wins).
    Non-dict cruft (from a hand-corrupted store) is skipped rather than raising."""
    out = {int(k): dict(v) for k, v in base.items()}
    for pstr, binds in (pergame_binds or {}).items():
        if not isinstance(binds, dict):
            continue
        try:
            pi = int(pstr)
        except (TypeError, ValueError):
            continue
        out.setdefault(pi, {}).update(binds)
    return out


def _pcsx2_p2_section(npads: int) -> str:
    """Player 2's [PadN] section for the current bound-pad count. Under 4-player multitap the
    slot plan is [1,3,4,5], so Player 2 lives in Pad3, not Pad2."""
    try:
        pad_nums, _mt1, _mt2 = pcsx2_cfg._slot_plan(max(npads, 2))
        return f"Pad{pad_nums[1]}"
    except Exception:
        return "Pad2"


def _sidecar_record_sections(side: Path, target: Path, sections) -> None:
    """Ensure the sidecar snapshot records each section's CURRENT body, so restore reverts a
    per-game override even for a section not in the base _snapshot (USB1/USB2) or missing from a
    stale pre-feature sidecar. Only ADDS a section not already recorded — never overwrites the
    authoritative pre-write body. No-op if the sidecar is absent / not a section-dict snapshot.
    Safe because the sections we lazily record (USB*) are untouched by assign_devices, so their
    current body still equals the pre-launch body at apply time."""
    try:
        if not side.exists():
            return
        meta = json.loads(side.read_text(encoding="utf-8"))
        snap = meta.get("input")
        if not isinstance(snap, dict):
            return
        text = target.read_text(encoding="utf-8", errors="replace")
        changed = False
        for sec in sections:
            if sec not in snap:
                snap[sec] = inifile.section_body(text, sec)
                changed = True
        if changed:
            meta["input"] = snap
            fsutil.atomic_write_text(side, json.dumps(meta))
    except Exception as e:
        _log(f"pcsx2: sidecar record failed ({e!r})")


def _apply_pcsx2_pergame_ports(target: Path, entry: dict, side: Path, npads: int) -> None:
    """Apply the per-game USB-port / Player-2 overrides to the GLOBAL ini (transient; reverted on
    exit). USB value = 'None' (port off); absent/'' = inherit (untouched). pad2 False = force
    Player 2 off, on its ACTUAL [PadN] for the current pad count. Each written section's pre-write
    body is recorded into the sidecar FIRST so restore reverts it (never a persistent global write);
    [Pad*] slots are already in the base snapshot, so only [USB*] is newly recorded."""
    writes = []
    for port, key in (("USB1", "usb1"), ("USB2", "usb2")):
        if entry.get(key):
            writes.append((port, entry[key]))
    if entry.get("pad2") is False:
        writes.append((_pcsx2_p2_section(npads), "None"))
    if not writes:
        return
    _sidecar_record_sections(side, target, [sec for sec, _ in writes])
    for sec, val in writes:
        pcsx2_cfg.set_section_type(target, sec, val)


def bind(emu: str, rom: str) -> None:
    """Snapshot the input portion (once), then write the connected pads to the
    target config (input only — button maps + settings untouched)."""
    try:
        _log(f"--- bind: emu={emu} rom={Path(rom).name!r} ---")
        if emu in ("pcsx2x6", "ps2guncon"):
            # The lightgun crosshair freezes if ANY guncon2*_Relative* key exists (it flips
            # the GunCon2 cursor to the unfed relative path). Strip them every launch so no
            # source (PCSX2 "Automatic Mapping", a stale config) can keep it frozen — must run
            # even when there are no pads / the emu is hands-off, hence before those returns.
            # pcsx2x6 = arcade guncon2 (portable ini); ps2guncon = retail guncon2-retail (datapath ini).
            gun_ini = _PCSX2X6_INI if emu == "pcsx2x6" else _PS2GUNCON_INI
            try:
                if pcsx2_cfg.strip_guncon2_relative_binds(gun_ini):
                    _log(f"{emu}: stripped guncon2 relative binds (lightgun cursor-freeze fix)")
            except Exception as e:
                _log(f"{emu}: relative-bind strip failed ({e!r})")
        if pads_cmds._hands_off(emu):
            _log(f"{emu}: hands-off is set — leaving its own controller config untouched")
            return
        _log_sdl_view()
        target = _target(emu, rom)
        if not target.is_file():
            _log(f"{emu}: no config at {target}; leaving input untouched")
            return
        pergame = _pcsx2_pergame(emu, rom)   # per-game override (USB/Pad2/binds/pad order), or None
        # Per-game pad ORDER (which type is which player) overrides the global order for this
        # launch, resolved BEFORE the managed_players truncation so it can promote a pad into
        # the top-N. None for every non-pcsx2 emu (pergame is None), so no collateral.
        pads = _resolve_pads(emu, order=(pergame or {}).get("pads"))
        _log(f"{emu}: stored order={pads_cmds._stored_order(emu)} "
             f"resolved={[(d.index, d.vidpid) for d in pads]} -> {target}")
        # Per-game PORT overrides (USB off / Player 2 off) apply even with NO pads — e.g. a lightgun
        # PS2 game launched with only the gun connected. Pad binds still need pads.
        has_ports = bool(pergame and (pergame.get("usb1") or pergame.get("usb2")
                                      or pergame.get("pad2") is not None))
        if not pads and not has_ports:
            _log(f"{emu}: no connected pads; leaving input untouched")
            return
        if pads and emu == "pcsx2":   # bind to PCSX2's OWN numbering (Steam owns it; read, don't predict)
            pads, cal = _calibrate_pcsx2(pads)
            _log(f"pcsx2: calibrated {cal}")
        if emu in _TRANSIENT:    # snapshot once for the on-exit restore (Switch dual-context)
            side = _sidecar(target)
            if not side.exists():
                fsutil.atomic_write_text(
                    side, json.dumps({"emu": emu, "input": _snapshot(emu, target)}))
        if pads:
            # Per-game button remaps layer over the global overrides that assign_devices applies.
            # Best-effort: a corrupt per-game bind must never skip the pad bind itself.
            overrides = None
            try:
                if pergame and pergame.get("binds"):
                    overrides = _merge_overrides(pcsx2_cfg.load_input_overrides(target), pergame["binds"])
            except Exception as e:
                _log(f"pcsx2: per-game bind merge failed ({e!r}); using global binds")
            res = _write(emu, target, pads, overrides=overrides)
            _log(f"{emu}: bound {len(pads)} pad(s) -> {target.name} :: {res}")
        if pergame:              # per-game USB-port / Player-2 overrides (transient, reverted on exit)
            try:
                _apply_pcsx2_pergame_ports(target, pergame, _sidecar(target), len(pads))
                _bk = pergame.get("binds")
                _log(f"pcsx2: per-game ports usb1={pergame.get('usb1')} usb2={pergame.get('usb2')} "
                     f"pad2={pergame.get('pad2')} "
                     f"binds={sorted(_bk.keys()) if isinstance(_bk, dict) else []}")
            except Exception as e:
                _log(f"pcsx2: per-game port apply failed ({e!r})")
    except Exception as e:               # never block the launch
        _log(f"{emu}: bind failed ({e!r}); launching unchanged")


def restore_target(target: Path) -> None:
    """Re-apply the sidecar's input snapshot to `target` (the emulator-rewritten
    config), then drop the sidecar. SETTINGS the emulator wrote are kept."""
    side = _sidecar(target)
    try:
        if not (target.is_file() and side.exists()):
            return
        meta = json.loads(side.read_text(encoding="utf-8"))
        emu, snap = meta.get("emu"), meta.get("input")
        if emu == "ryujinx":
            data = ryujinx_json.load(target)        # has the emulator's settings
            data["input_config"] = snap
            ryujinx_json.write(data, target)
        elif emu == "eden":
            text = target.read_text(encoding="utf-8", errors="replace")
            text = (inifile.remove_section(text, "Controls") if snap is None
                    else inifile.set_section(text, "Controls", snap))
            fsutil.atomic_write(target, text)
        elif emu == "pcsx2":
            text = target.read_text(encoding="utf-8", errors="replace")
            for name, body in (snap or {}).items():
                # body is None ⇒ the section didn't exist pre-bind ⇒ remove the one the
                # bind added (multitap [Pad], extra [PadN]); else re-apply the original.
                text = (inifile.remove_section(text, name) if body is None
                        else inifile.set_section(text, name, body))
            fsutil.atomic_write(target, text)
        elif emu == "xemu":
            text = target.read_text(encoding="utf-8", errors="replace")
            text = (inifile.remove_section(text, "input.bindings") if snap is None
                    else inifile.set_section(text, "input.bindings", snap))
            fsutil.atomic_write(target, text)
        elif emu == "rpcs3":
            data = rpcs3_cfg.yaml.safe_load(target.read_text(encoding="utf-8", errors="replace")) or {}
            snap = snap or {}
            for k in [k for k in data if _PLAYER_RE.match(k) and k not in snap]:
                del data[k]                       # drop a Player block the bind added
            for k, v in snap.items():
                data[k] = v                       # restore the original blocks
            fsutil.atomic_write_text(target, rpcs3_cfg.yaml.safe_dump(
                data, sort_keys=False, default_flow_style=False, allow_unicode=True))
        side.unlink()
        _log(f"{emu}: restored input on {target.name}")
    except json.JSONDecodeError as e:
        # Corrupt/truncated sidecar (SIGKILL / power-loss mid-write): drop it so it can't
        # wedge restore AND re-snapshot forever (bind re-snaps only when the sidecar is absent).
        _log(f"restore: dropping corrupt sidecar {side.name} ({e!r})")
        try:
            side.unlink()
        except OSError:
            pass
    except Exception as e:
        _log(f"restore failed on {target} ({e!r})")


def _known_configs():
    # The TRANSIENT emulators' configs — the ones restore_all may need to revert.
    yield _RYUJINX_GLOBAL
    yield _EDEN_INI
    yield _PCSX2_INI
    yield _XEMU_TOML
    yield _RPCS3_YML
    try:
        yield from _RYUJINX_GAMES.glob("*/Config.json")
    except OSError:
        pass


def restore_all() -> None:
    """Restore every pending sidecar (called by the ES-DE game-end hook). Idempotent
    — a no-op when nothing is pending (normal: only one switch game ran)."""
    for cfg in _known_configs():
        if _sidecar(cfg).exists():
            restore_target(cfg)
