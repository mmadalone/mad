"""
PCSX2 (PS2) controller-assignment backend for the controller-router.

PCSX2 binds each emulated pad to an SDL device by **index**, not GUID/name:
`PCSX2.ini` has `[Pad1]` with every button bound to `SDL-0/...`, `[Pad2]` to
`SDL-1/...`, etc. The button names are SDL-standard (`FaceSouth`,
`+LeftTrigger`, …), so the bind block is device-agnostic — only the `SDL-N/`
prefix selects which physical pad is that player.

So routing PS2 = find the PlayStation pads' real SDL indices (via
`devices.sdl_devices()`, the same SDL order PCSX2 walks moments later) and write
`[Pad1]`/`[Pad2]` to those `SDL-N`. This is robust even when Sinden guns / the
Steam Deck / other pads also occupy SDL slots (e.g. the Deck is usually SDL-0,
so the DualShock 4s land on SDL-1/2 — PCSX2's default `Pad1=SDL-0` would wrongly
be the Deck; the router fixes that).

Behaviour (all from `[backends.pcsx2]` in controller-policy.toml):
  * PlayStation pads (DualSense + DualShock 4, by vid:pid in `pad_classes`,
    priority order) → Pad1, Pad2 … up to `manage_pads`; extra slots -> None.
  * No PlayStation pad -> bind Pad1 to `handheld_class` (the Steam Deck) so the
    game is playable handheld; if that class isn't present (or it's ""), leave
    PCSX2.ini untouched.

Edits are section-targeted text replacements (the rest of PCSX2.ini is preserved
verbatim) with a one-time backup. PCSX2 is closed at ES-DE game-start (it
rewrites the ini on exit, so edits must happen while it's closed).
"""
from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path

from .devices import sdl_devices
from . import fsutil, inifile, mad_paths, pad_assign


def _warn(msg: str) -> None:
    """Append a diagnostic to router.log (Game Mode has no console)."""
    line = f"pcsx2_cfg: {msg}"
    print(line, file=sys.stderr)
    try:
        log = mad_paths.storage("controller-router", "router.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

_IDX = "@@IDX@@"   # placeholder for the SDL index in a bind template

# Canonical PCSX2 DualShock2 bind block (captured from a live EmuDeck PCSX2.ini),
# used when the existing [Pad1] has no usable bindings to clone. `@@IDX@@` is the
# SDL device index. The tuning header (AxisScale, deadzones) matches EmuDeck's.
_BAKED_DS2 = """Type = DualShock2
InvertL = 0
InvertR = 0
Deadzone = 0
AxisScale = 1.33
LargeMotorScale = 1
SmallMotorScale = 1
ButtonDeadzone = 0
PressureModifier = 0.5
Up = SDL-@@IDX@@/DPadUp
Right = SDL-@@IDX@@/DPadRight
Down = SDL-@@IDX@@/DPadDown
Left = SDL-@@IDX@@/DPadLeft
Triangle = SDL-@@IDX@@/FaceNorth
Circle = SDL-@@IDX@@/FaceEast
Cross = SDL-@@IDX@@/FaceSouth
Square = SDL-@@IDX@@/FaceWest
Select = SDL-@@IDX@@/Back
Start = SDL-@@IDX@@/Start
L1 = SDL-@@IDX@@/LeftShoulder
L2 = SDL-@@IDX@@/+LeftTrigger
R1 = SDL-@@IDX@@/RightShoulder
R2 = SDL-@@IDX@@/+RightTrigger
L3 = SDL-@@IDX@@/LeftStick
R3 = SDL-@@IDX@@/RightStick
LUp = SDL-@@IDX@@/-LeftY
LRight = SDL-@@IDX@@/+LeftX
LDown = SDL-@@IDX@@/+LeftY
LLeft = SDL-@@IDX@@/-LeftX
RUp = SDL-@@IDX@@/-RightY
RRight = SDL-@@IDX@@/+RightX
RDown = SDL-@@IDX@@/+RightY
RLeft = SDL-@@IDX@@/-RightX
LargeMotor = SDL-@@IDX@@/LargeMotor
SmallMotor = SDL-@@IDX@@/SmallMotor"""


def _expand(p: str) -> Path:
    return Path(p).expanduser()


# Relative-aim keys whose mere PRESENCE flips the GunCon2 cursor to the (unfed)
# relative path, freezing the lightgun crosshair while JVS aim still works. Never
# used for S246/256; written by PCSX2 "Automatic Mapping" or a stale config.
_GUNCON2_RELATIVE_KEYS = ("guncon2_RelativeUp", "guncon2_RelativeDown",
                          "guncon2_RelativeLeft", "guncon2_RelativeRight",
                          # retail GunCon2 device (Type = guncon2-retail) has the same freeze bug
                          "guncon2-retail_RelativeUp", "guncon2-retail_RelativeDown",
                          "guncon2-retail_RelativeLeft", "guncon2-retail_RelativeRight")


def strip_guncon2_relative_binds(ini_path) -> bool:
    """Remove guncon2_Relative{Up,Down,Left,Right} from [USB1]/[USB2] in `ini_path`.
    Byte-stable except the removed lines; returns True if anything changed, no-op when
    the file or the keys are absent. Run at launch so the lightgun cursor tracks the
    absolute pointer no matter how the relative keys got written."""
    p = Path(ini_path).expanduser()
    if not p.is_file():
        return False
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as ex:
        _warn(f"strip_guncon2: cannot read {p} ({ex}); leaving it unchanged")
        return False
    out: list[str] = []
    section = ""
    changed = False
    for ln in text.split("\n"):
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
        if section in ("USB1", "USB2") and ln.split("=", 1)[0].strip() in _GUNCON2_RELATIVE_KEYS:
            changed = True
            continue
        out.append(ln)
    if not changed:
        return False
    fsutil.atomic_write(p, "\n".join(out))
    return True


def _bind_template(text: str) -> str:
    """A DualShock2 bind block with the SDL index replaced by @@IDX@@. Clones the
    live [Pad1] (preserving any user tuning) if it's a usable DualShock2 block;
    otherwise the baked canonical block."""
    body = inifile.section_body(text, "Pad1")
    if body and "Type = DualShock2" in body and "SDL-" in body:
        return re.sub(r"SDL-\d+/", f"SDL-{_IDX}/", body)
    return _BAKED_DS2


def _slot_template(text: str, pad_num: int, fallback: str) -> str:
    """Bind template for ``[Pad{pad_num}]``, PRESERVING that slot's OWN button sources
    (a per-player remap from MAD's input-map page) when it already holds a usable
    DualShock2 block — only the SDL index is re-templated to ``@@IDX@@``. Falls back to
    ``fallback`` (the shared [Pad1] clone / baked block) for an empty / fresh / Type=None
    slot. Without this, cloning [Pad1] to every slot would overwrite Player 2+ remaps so
    they never take effect in-game (the whole point of the per-player picker)."""
    body = inifile.section_body(text, f"Pad{pad_num}")
    if body and "Type = DualShock2" in body and "SDL-" in body:
        return re.sub(r"SDL-\d+/", f"SDL-{_IDX}/", body)
    return fallback


def _pad_body(template: str, sdl_index: int) -> str:
    return template.replace(_IDX, str(sdl_index))


def _slot_plan(n: int):
    """Map ``n`` connected pads (priority order) to PCSX2 ``[PadN]`` slot NUMBERS plus
    the two multitap-enable flags ``(pads, mt1, mt2)``.

    Verified pad→port/slot mapping (PCSX2 source — see deck-docs/pcsx2-ini-encodings.md):
    port 1 = Pad1,Pad3,Pad4,Pad5 ; port 2 = Pad2,Pad6,Pad7,Pad8 (Pad3-5 need
    MultitapPort1, Pad6-8 need MultitapPort2; Pad1/Pad2 always active). The order is
    PORT-1-FIRST so a single-multitap 4-player game works, while 1-2 players stay on
    ports 1&2 with NO multitap (the standard 2-controller layout, unchanged behaviour).
    ``players[i]`` (priority i) → in-game player i+1."""
    if n <= 2:
        return [1, 2][:n], False, False           # ports 1 & 2, no multitap
    if n <= 4:
        return [1, 3, 4, 5][:n], True, False       # one multitap on port 1
    return [1, 3, 4, 5, 2, 6, 7, 8][:n], True, True   # both multitaps


def _set_key(body: str, key: str, value: str) -> str:
    """Rewrite ``key``'s value in an INI section ``body`` in place (preserving its
    formatting + every other key); append ``key = value`` if absent."""
    pat = re.compile(rf"(?m)^([ \t]*{re.escape(key)}[ \t]*=[ \t]*).*$")
    if pat.search(body):
        return pat.sub(lambda m: m.group(1) + value, body)
    return (body + ("\n" if body and not body.endswith("\n") else "")) + f"{key} = {value}"


def set_section_type(ini_path, section: str, type_value: str) -> bool:
    """Set ``[section] Type = type_value`` in the ini, byte-preserving (every other key kept),
    at launch. Used to apply per-game USB-port / Player-2 overrides to the GLOBAL config; the
    router snapshots [USB1]/[USB2]/[Pad*] before binding and reverts them on exit, so this write
    is transient. Returns True if the file changed. Never raises into the launch path."""
    try:
        ini = _expand(str(ini_path))
        if not ini.is_file():
            return False
        text = ini.read_text(encoding="utf-8", errors="replace")
        body = inifile.section_body(text, section)
        if body is None:                              # section absent -> nothing to override safely
            return False
        new_body = _set_key(body, "Type", type_value)
        if new_body == body:
            return False
        new_text = inifile.set_section(text, section, new_body)
        if not new_text or new_text == text:
            return False
        fsutil.atomic_write_text(ini, new_text)
        return True
    except Exception:
        return False


# ── per-player input-override store ───────────────────────────────────────────
# A remap is stored keyed by PLAYER (not by physical [PadN] slot), in a JSON sidecar
# next to the ini, and re-applied at launch to whatever slot that player lands in (the
# rpcs3_cfg.load_overrides/_player_block pattern). This makes a Player-2 remap follow
# the player across any pad count, and survive a non-transient pcsx2x6 single-pad launch.
def _overrides_path(ini_path) -> Path:
    return _expand(str(ini_path)).with_name(".mad-input-overrides.json")


def load_input_overrides(ini_path) -> dict:
    """``{player(int): {ps2_button: sdl_source}}`` from the sidecar, or ``{}``."""
    p = _overrides_path(ini_path)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError) as ex:
        _warn(f"corrupt override sidecar {p} ({ex}); dropping ALL remaps this launch")
        return {}
    return {int(k): dict(v) for k, v in data.items()
            if str(k).isdigit() and isinstance(v, dict)}


def save_input_overrides(ini_path, overrides: dict) -> None:
    p = _overrides_path(ini_path)
    data = {str(int(k)): dict(v) for k, v in sorted(overrides.items()) if v}
    p.parent.mkdir(parents=True, exist_ok=True)
    fsutil.atomic_write_text(p, json.dumps(data, indent=2, sort_keys=True))


_OVERRIDES_LOCK = threading.Lock()


def update_input_override(ini_path, player: int, key: str, source) -> None:
    """Atomic read-modify-write of ONE override entry, serialized across the 4-worker RPC
    pool so two near-simultaneous remaps can't lost-update each other."""
    with _OVERRIDES_LOCK:
        ovr = load_input_overrides(ini_path)
        ovr.setdefault(player, {})[key] = source
        save_input_overrides(ini_path, ovr)


def clear_input_override(ini_path, player: int, key: str) -> None:
    """Reset one button to the baked DualShock2 default (the page's "focus a row, press Start"
    clear). We WRITE the baked default as the override rather than DELETE the entry: at launch the
    slot keeps its OWN source when no override is present (_slot_template), so a user who bound the
    button in PCSX2's own GUI to a non-baked source would otherwise never actually get the default.
    Forcing the baked default makes the reset take effect in-game (idempotent when already baked).
    Falls back to delete only for a key with no baked default. Same lock as update_input_override."""
    baked = baked_default_sources().get(key)
    with _OVERRIDES_LOCK:
        ovr = load_input_overrides(ini_path)
        if baked is not None:
            ovr.setdefault(player, {})[key] = baked
            save_input_overrides(ini_path, ovr)
        elif player in ovr and key in ovr[player]:
            del ovr[player][key]
            if not ovr[player]:
                del ovr[player]
            save_input_overrides(ini_path, ovr)


def baked_default_sources() -> dict:
    """``{ps2_button: sdl_source}`` for the canonical DualShock2 block (Cross->FaceSouth,
    Up->DPadUp, LUp->-LeftY, …). The display base for the input-map page and the source
    base the per-player overrides layer on top of at launch."""
    return {m.group(1): m.group(2)
            for m in re.finditer(rf"(?m)^(\w+) = SDL-{re.escape(_IDX)}/(.+)$", _BAKED_DS2)}


def migrate_overrides_from_ini(ini_path, slot_sections) -> dict:
    """ONE-TIME: if the store is empty, seed it from existing [PadN] SDL sources that
    DIFFER from the baked default (an existing PCSX2 user's button remaps), keyed by
    player position (``slot_sections[i]`` = player i+1's slot). No-op when the store is
    non-empty or the slots hold no SDL block (e.g. pcsx2x6's keyboard [Pad1]). Returns
    the resulting store."""
    ov = load_input_overrides(ini_path)
    if ov:
        return ov
    ini = _expand(str(ini_path))
    if not ini.is_file():
        return ov
    text = ini.read_text(encoding="utf-8", errors="replace")
    defaults = baked_default_sources()
    migrated: dict = {}
    for i, section in enumerate(slot_sections):
        body = inifile.section_body(text, section) or ""
        if "Type = DualShock2" not in body or "SDL-" not in body:
            continue
        per = {}
        for key, default_src in defaults.items():
            m = re.search(rf"(?m)^{re.escape(key)} = SDL-\d+/(.+)$", body)
            if m and m.group(1).strip() != default_src:
                per[key] = m.group(1).strip()
        if per:
            migrated[i + 1] = per
    if migrated:
        save_input_overrides(ini_path, migrated)
    return load_input_overrides(ini_path)


def _override_block(base_block: str, overrides_for_player: dict) -> str:
    """Layer a player's per-button overrides onto an existing DualShock2 bind block
    (SDL index = @@IDX@@) — typically the live target slot's own sources + tuning.

    Empty overrides ⇒ the base block is returned UNCHANGED, byte-identical to the
    slot-keyed path, so a remap made in PCSX2's own GUI (and not yet captured by the
    MAD page's one-time migration) survives a launch. A real override re-sources only
    its own buttons, so it follows the player to whatever slot they land in while the
    rest of that slot's bindings are preserved."""
    block = base_block
    for button, source in (overrides_for_player or {}).items():
        block = _set_key(block, button, f"SDL-{_IDX}/{source}")
    return block


def assign(cfg: dict, logger, devs=None, pins=None) -> int:
    """Apply the PS2 pad assignment. Returns 0 (launch always continues).

    `pins` ({player: evdev Device}) + `devs` (the evdev device list) let a GLOBAL
    device pin override the default in-SDL-order selection: a pinned pad takes its
    player's [PadN] via its live SDL index (re-resolved each launch)."""
    ini = _expand(cfg.get("config_file", "~/.config/PCSX2/inis/PCSX2.ini"))
    manage = int(cfg.get("manage_pads", 2))
    pad_classes: list[str] = list(cfg.get("pad_classes", []))
    handheld_class = cfg.get("handheld_class", "")

    if not ini.is_file():
        logger.warning(f"pcsx2: config file {ini} not found; skipping")
        return 0

    sdl = sdl_devices()
    if not sdl:
        logger.warning("pcsx2: SDL enumerated no joysticks; leaving PCSX2.ini")
        return 0

    logger.info("pcsx2: SDL order = "
                + ", ".join(f"SDL-{d.index}:{d.vidpid}" for d in sdl))

    text = ini.read_text(encoding="utf-8")
    orig = text                       # resting config — read each slot's own remap from here
    template = _bind_template(text)

    # Slot -> SDL index via the shared pipeline. pcsx2's value IS the SDL index,
    # so collisions are plain value-membership (unit_count=1). Two historical
    # quirks are preserved by flags: an over-manage pin still suppresses the
    # handheld fallback (filter_pins_at_resolve=False), and two players pinned to
    # one pad keep only the higher slot (dedup_pins=True, the original loop).
    from .devices import sdl_index_of
    assigned = pad_assign.assign_slots(
        sdl, manage, pins, devs,
        pad_classes=pad_classes, handheld=handheld_class,
        encode_auto=lambda d, rank: d.index,
        encode_pin=lambda pdev, sdl_devs, evdevs: sdl_index_of(pdev, evdevs, sdl_devs),
        base_index=1, filter_pins_at_resolve=False, dedup_pins=True,
    )
    if assigned is None:
        logger.info("pcsx2: no PlayStation pad and no handheld device; "
                    "leaving PCSX2.ini untouched")
        return 0
    logger.info("pcsx2: pads -> "
                + (", ".join(f"Pad{k}=SDL-{i}" for k, i in sorted(assigned.items()))
                   or "(all disabled)"))

    # Back up once, then write Pad1..manage (assigned -> DualShock2, else None).
    if fsutil.ensure_pristine_backup(ini):
        logger.info(f"pcsx2: one-time backup -> {ini.name}.router-backup")

    for k in range(1, manage + 1):
        if k in assigned:
            slot_tmpl = _slot_template(orig, k, template)   # keep this slot's own remap
            text = inifile.set_section(text, f"Pad{k}", _pad_body(slot_tmpl, assigned[k]))
        else:
            text = inifile.set_section(text, f"Pad{k}", "Type = None")

    fsutil.atomic_write(ini, text)
    logger.info(f"pcsx2: wrote {ini}")
    return 0


def assign_devices(players, ini_path: str | None = None, manage: int = 8,
                   overrides: dict | None = None) -> dict:
    """Configure-once device pick (MAD Standalones 'pads → players'): bind the
    ordered ``players`` (a list of ``devices.SdlDevice`` in priority order) to the
    PCSX2 ``[PadN]`` slots their player number maps to (``_slot_plan``), set the
    matching ``[Pad] MultitapPort1/2`` flags, and ``Type = None`` every other slot.
    The Standalones launch wrapper calls this at game-start (and restores the prior
    ``[Pad]`` + ``[Pad*]`` on exit).

    1-2 pads → ports 1&2, multitap OFF (the standard layout — unchanged). 3-4 → one
    multitap on port 1; 5-8 → both multitaps (see ``_slot_plan`` for the verified
    pad→port mapping). Unlike ``assign()`` there is no policy ``pad_classes``/``pins``
    /handheld — the caller already chose the order. The DualShock2 bind block is
    cloned from the live ``[Pad1]`` (preserving user tuning) or the baked canonical
    block. Raises FileNotFoundError if PCSX2.ini is missing (launch a PS2 game once)."""
    ini = _expand(ini_path or "~/.config/PCSX2/inis/PCSX2.ini")
    if not ini.is_file():
        raise FileNotFoundError("PCSX2.ini not found — launch a PS2 game once")
    text = ini.read_text(encoding="utf-8")
    orig = text                       # resting config — read each slot's own remap from here
    template = _bind_template(text)
    fsutil.ensure_pristine_backup(ini)

    pad_nums, mt1, mt2 = _slot_plan(len(players))
    by_pad = {pad_nums[i]: players[i] for i in range(len(pad_nums))}
    slot_player = {pad_nums[i]: i + 1 for i in range(len(pad_nums))}  # slot -> in-game player
    slots = max(int(manage), 8)            # PCSX2 has Pad1..Pad8
    for k in range(1, slots + 1):
        if k in by_pad and overrides is not None:
            # player-keyed: the live slot's OWN sources (so a direct-in-PCSX2 remap is
            # preserved) overlaid by this PLAYER's overrides, so a MAD remap follows the
            # player to whatever slot they land in this launch.
            slot_tmpl = _slot_template(orig, k, template)
            block = _override_block(slot_tmpl, overrides.get(slot_player[k]) or {})
            text = inifile.set_section(text, f"Pad{k}", _pad_body(block, by_pad[k].index))
        elif k in by_pad:
            slot_tmpl = _slot_template(orig, k, template)   # slot-keyed (legacy path)
            text = inifile.set_section(text, f"Pad{k}", _pad_body(slot_tmpl, by_pad[k].index))
        else:
            text = inifile.set_section(text, f"Pad{k}", "Type = None")

    body = inifile.section_body(text, "Pad") or ""
    body = _set_key(body, "MultitapPort1", "true" if mt1 else "false")
    body = _set_key(body, "MultitapPort2", "true" if mt2 else "false")
    text = inifile.set_section(text, "Pad", body)

    fsutil.atomic_write(ini, text)
    return {"assigned": [(f"Pad{pad_nums[i]}", players[i].index) for i in range(len(pad_nums))],
            "multitap": (mt1, mt2)}
