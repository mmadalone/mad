"""
Cemu (Wii U) controller-assignment backend for the controller-router.

Cemu's active per-port configs live as `<config_dir>/controllerN.xml`, where
`controller0.xml` == UI "Controller 1" (P1), `controller1.xml` == P2, … Each
file is a full emulated-controller profile: a `<type>`, an `<api>`/`<uuid>`/
`<display_name>` device binding and a `<mappings>` block.

This backend OWNS the managed port files (default 0,1) on every Wii U launch:

  * For each port that the router resolved to a physical pad, clone that pad's
    *named template* profile (chosen by vid:pid) into `controllerN.xml`, only
    rewriting the SDL `<uuid>` prefix `"<index>_"` to the pad's position within
    its own class (0 = first connected pad of that vid:pid, 1 = second). The
    template's GUID, mappings and display_name are device-correct already — and
    SDL gamepad button indices are identical across pads, so one template per
    class suffices.
  * Ports with no resolved pad have their managed file removed.
  * If NO external pad is connected at all, restore the configured handheld
    profile to P1 (and clear the rest) so the Steam Deck plays handheld — or,
    if no handheld profile is configured, leave every file untouched.

A one-time backup of the managed files is taken before the first write, so the
user's original manual setup is recoverable. Cemu is closed at ES-DE
game-start, which is when this runs (Cemu rewrites its config on exit).

All paths/templates/handheld choices come from `[backends.cemu]` in
controller-policy.toml — nothing here is hardcoded.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from .devices import Device, class_index, sdl_devices, vidpid
from . import fsutil, staterev

_UUID_RE = re.compile(r"(<uuid>)\s*([^_<\s]+)_([0-9a-fA-F]+)\s*(</uuid>)")
_DISPLAY_RE = re.compile(r"(<display_name>)(.*?)(</display_name>)", re.DOTALL)
_BACKUP_DIRNAME = ".router-backup"


def _expand(p: str) -> Path:
    return Path(p).expanduser()


def _template_path(cfg_dir: Path, name: str) -> Path:
    return cfg_dir / f"{name}.xml"


def _port_path(cfg_dir: Path, port0: int) -> Path:
    return cfg_dir / f"controller{port0}.xml"


def _template_guid(text: str) -> str | None:
    """The GUID portion (after the 'index_' prefix) of a profile's <uuid>."""
    m = _UUID_RE.search(text)
    return m.group(3) if m else None


def _backup_once(cfg_dir: Path, managed0: list[int], logger) -> None:
    """One-time PRISTINE snapshot of each managed controllerN.xml into
    <cfg_dir>/.router-backup before the router first overwrites/clears it.

    Per file: a file is snapshotted only if it has NO backup yet — so the user's
    ORIGINAL is preserved and never re-snapshotted over with MAD's own generated
    output (which IS newer every launch and would silently destroy the backup).
    A managed file that first appears in a later launch still gets its one
    pristine snapshot here (the old all-or-nothing dir guard missed those)."""
    backup = cfg_dir / _BACKUP_DIRNAME
    created: list[str] = []
    for port0 in managed0:
        src = _port_path(cfg_dir, port0)
        if not src.is_file():
            continue
        dst = backup / src.name
        if dst.exists():
            continue
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        created.append(src.name)
    if created:
        logger.info(f"cemu: one-time backup of managed ports -> {backup} "
                    f"({', '.join(created)})")


def _sdl_match(dev: Device, devs: list[Device],
               sdl_devs: list) -> tuple[int, str | None]:
    """Resolve a router-resolved (evdev) Device to the live SDL joystick
    *index* and GUID that Cemu expects inside "<index>_<guid>".

    Cemu binds each port to the SDL device whose enumeration index AND GUID
    match the saved uuid. Two identical pads share a GUID, so the index is the
    only discriminator — writing 0/1 (class position) instead of the real SDL
    index made Cemu fall back to GUID-only and bind BOTH ports to the first pad
    (the "P1 & P2 on the same controller" bug).

    Map by class order: the k-th pad of a vid:pid class (k from the evdev
    enumeration) -> the k-th SDL device of that same class (SDL is returned in
    index order). Both subsystems enumerate by connection order so the k-th
    aligns; if they ever don't, the pads are still DISTINCT indices (worst case
    a P1/P2 swap, fixable by power-on order — never the same pad twice). Falls
    back to class index + class GUID if SDL doesn't see the class."""
    cls = vidpid(dev)
    same = [d for d in sdl_devs if d.vidpid == cls]   # already SDL-index order
    ci = class_index(devs, dev)
    if ci < len(same):
        return same[ci].index, same[ci].guid
    # ci >= len(same): SDL enumerates FEWER pads of this class than evdev (a transient SDL-vs-evdev
    # hotplug / undercount). Return an index BEYOND every real SDL index so it can never collide with a
    # present pad's same[k].index -- two undercounted twins still get DISTINCT indices (len+ci), and the
    # absent pad's port stays unbound (correct: SDL can't see it) instead of doubling onto a present pad.
    # The old `return ci` reused an evdev class-position in the SDL-index space and could collide -- the
    # 'both ports on one controller' bug this seater exists to prevent.
    return len(sdl_devs) + ci, (same[0].guid if same else None)


def _write_port_from_template(cfg_dir: Path, port0: int, template: str,
                              dev: Device, devs: list[Device],
                              sdl_devs: list, logger) -> bool:
    """Clone <template>.xml into controller<port0>.xml for `dev`, rewriting:
      * <uuid> = "<sdl_index>_<GUID>" where the GUID is the device's live SDL
        GUID (authoritative; falls back to the template's baked GUID), and the
        index is the device's live SDL joystick index — Cemu keys SDLController
        bindings on this exact "{index}_{guid}", so identical pads (same GUID)
        are told apart by index, and
      * <display_name> = the device's actual name.
    The template supplies the emulated <type>, axis/trigger blocks and the
    SDL-button <mappings> (identical across SDL gamepads). Returns True on
    success."""
    tpath = _template_path(cfg_dir, template)
    if not tpath.is_file():
        logger.warning(f"cemu: template {tpath.name!r} missing; leaving port "
                       f"{port0} (Controller {port0 + 1}) untouched")
        return False
    text = tpath.read_text(encoding="utf-8")
    baked_guid = _template_guid(text)
    # Resolve to the LIVE SDL joystick index + GUID (the index is what tells two
    # identical pads apart inside Cemu's "<index>_<guid>"). Was a NameError
    # (`sdl_guids`) + class_index, which made Cemu bind both ports to the first
    # pad; _sdl_match is the intended resolver (assign() passes sdl_devs).
    sdl_index, sdl_guid = _sdl_match(dev, devs, sdl_devs)
    guid = sdl_guid or baked_guid
    if guid is None:
        logger.warning(f"cemu: no SDL GUID for {dev.name!r} and template "
                       f"{tpath.name!r} has none; skipping port {port0}")
        return False
    src = "sdl" if sdl_guid else "template"
    new_uuid = f"{sdl_index}_{guid}"
    text = _UUID_RE.sub(rf"\g<1>{new_uuid}\g<4>", text, count=1)
    # 12.0: insert dev.name via a FUNCTION replacement so backslashes / "\g<n>"
    # in a device name aren't interpreted as regex backrefs, and XML-escape it
    # since it lands inside a <display_name> element.
    text = _DISPLAY_RE.sub(
        lambda m: m.group(1) + _xml_escape(dev.name) + m.group(3),
        text, count=1)
    fsutil.atomic_write(_port_path(cfg_dir, port0), text)
    logger.info(f"cemu: Controller {port0 + 1} <- {dev.name!r} "
                f"(template {template!r}, guid src={src}) uuid={new_uuid}")
    return True


# A profile can carry more than one <controller> block: the family device (with the real
# <mappings>) plus a Steam Deck co-source (empty mappings) in the "+ Steamdeck" variants.
# Only the family block(s) are re-pinned to the seated pad; a Deck co-source keeps its baked
# uuid, because there is exactly ONE Deck and Cemu's GUID-only fallback binds it regardless
# of index (so it never needs a live-index re-pin, and the old count=1 first-block-only sub
# left later family blocks stale — this walks every block).
_CONTROLLER_BLOCK_RE = re.compile(r"<controller>.*?</controller>", re.DOTALL)
_DECK_GUIDS = {"030079f6de280000ff11000001000000"}   # Steam Deck built-in pad (28de:11ff, Game Mode)


def repin_profile(text: str, dev: Device, devs: list[Device], sdl_devs: list,
                  display_name: str | None = None) -> str:
    """A controllerProfiles/<name>.xml body with every NON-Deck <controller> block's
    <uuid> re-pinned to `dev`'s live "<sdl_index>_<guid>" (two identical pads get distinct
    indices via _sdl_match), and the first such block's <display_name> set to
    `display_name` (defaults to dev.name). Steam Deck co-source blocks are left
    byte-identical (one Deck -> GUID-only bind; index irrelevant)."""
    sdl_index, sdl_guid = _sdl_match(dev, devs, sdl_devs)
    name = display_name if display_name is not None else dev.name
    did_display = False

    def _one(m):
        nonlocal did_display
        block = m.group(0)
        baked = _template_guid(block)
        if baked is not None and baked.lower() in _DECK_GUIDS:
            return block                              # Deck co-source: keep baked uuid
        guid = sdl_guid or baked
        if guid is None:
            return block
        block = _UUID_RE.sub(rf"\g<1>{sdl_index}_{guid}\g<4>", block, count=1)
        if not did_display and name:
            block = _DISPLAY_RE.sub(
                lambda mm: mm.group(1) + _xml_escape(name) + mm.group(3),
                block, count=1)
            did_display = True
        return block

    return _CONTROLLER_BLOCK_RE.sub(_one, text)


def _clear_port(cfg_dir: Path, port0: int, logger) -> None:
    """Remove a managed port file so Cemu treats that port as having no
    controller — RECOVERABLY (rule #5): MOVE it to a timestamped _TMP with a
    RECOVERY.txt instead of unlink. The one-time .router-backup only holds the
    pristine first-run original, so a hand-edit the user made AFTER MAD's first
    run would otherwise be unlink'd unrecoverably; now it lands in _TMP."""
    p = _port_path(cfg_dir, port0)
    if not p.is_file():
        return
    tmp = fsutil.recoverable_delete(
        p, tmp_base=Path.home() / "Downloads" / "_TMP",
        tag="cemu-cleared-port",
        recovery_note=(f"Cemu Controller {port0 + 1} ({p.name}) was cleared by the "
                       "controller-router because no pad resolved to this port. "
                       "To undo, move the file back to its original path."))
    staterev.bump("config")     # recoverable_delete doesn't bump; a cleared port
    logger.info(f"cemu: cleared Controller {port0 + 1} ({p.name}) -> {tmp}")


def assign(port_devs: dict[int, Device], devs: list[Device], cfg: dict,
           logger) -> int:
    """Apply the Wii U controller assignment.

    `port_devs` maps 1-based UI port -> resolved physical Device (strict: only
    pads matching the policy tokens, no catch-all). `devs` is the full
    enumeration (for class-index computation). `cfg` is the [backends.cemu]
    table. Returns 0 (informational; launch always continues).
    """
    cfg_dir = _expand(cfg.get("config_dir", "~/.config/Cemu/controllerProfiles"))
    managed0: list[int] = list(cfg.get("manage_ports", [0, 1]))
    templates: dict[str, str] = dict(cfg.get("templates", {}))
    handheld = cfg.get("handheld_profile", "")
    # P1 emulated as a Wii U GamePad (most compatible — games like NES Remix that
    # need the GamePad/touchscreen work; still maps to the physical P1 pad and is
    # 2-player with P2). "" = use the per-class template for P1 too.
    p1_gamepad = cfg.get("p1_gamepad_template", "")
    # Classes that signal "the user manages Cemu input themselves". If ANY such
    # pad is connected, the user's saved controllerN.xml is authoritative — e.g.
    # P1 = Steam Deck emulated as a Wii U GamePad (the Deck has a real
    # touchscreen, so the NES Remix / Miiverse dialog just works) + P2 = Wii U
    # Pro Controller — and the router leaves the whole config untouched.
    respect_classes = set(cfg.get("respect_user_config_classes", []))

    if not cfg_dir.is_dir():
        logger.warning(f"cemu: config dir {cfg_dir} not found; skipping")
        return 0

    # ── respect: a user-managed pad class is present → don't touch anything ──
    if respect_classes and any(vidpid(d) in respect_classes for d in devs):
        present = sorted({vidpid(d) for d in devs} & respect_classes)
        logger.info(f"cemu: user-managed pad(s) {present} detected -> respecting "
                    f"your saved Cemu input config; leaving controllerN.xml "
                    f"untouched")
        return 0

    # ── handheld fallback: no external Pro/DualSense connected ──
    if not port_devs:
        if not handheld:
            # No external pad and no handheld profile: disable all managed
            # (non-GamePad) player slots so only Controller 1 — left untouched,
            # e.g. the Steam Deck as the Wii U GamePad — remains for handheld play.
            _backup_once(cfg_dir, managed0, logger)
            for port0 in managed0:
                _clear_port(cfg_dir, port0, logger)
            logger.info("cemu: no external pad -> cleared managed slots "
                        "(handheld via Controller 1)")
            return 0
        if not _template_path(cfg_dir, handheld).is_file():
            logger.warning(f"cemu: handheld_profile {handheld!r} missing; "
                           "leaving config untouched")
            return 0
        _backup_once(cfg_dir, managed0, logger)
        # P1 = handheld profile (as-is, index 0); clear the rest.
        first = managed0[0]
        tpath = _template_path(cfg_dir, handheld)
        fsutil.atomic_write(_port_path(cfg_dir, first),
                            tpath.read_text(encoding="utf-8"))
        logger.info(f"cemu: no external pad -> Controller {first + 1} <- "
                    f"handheld {handheld!r}")
        for port0 in managed0[1:]:
            _clear_port(cfg_dir, port0, logger)
        return 0

    # ── external pad(s) present (no user-managed class): template mode ──
    _backup_once(cfg_dir, managed0, logger)
    sdl_devs = sdl_devices()   # one SDL init; live index + GUID per pad
    for slot_idx, port0 in enumerate(managed0):
        ui_port = port0 + 1                 # Cemu "Controller N" label
        # Pair resolved players in order to the managed slots: 1st resolved
        # player -> first managed slot, 2nd -> second, etc. (decoupled from the
        # Cemu slot number, so manage_ports=[1,2,3,4] fills Controller 2..5).
        dev = port_devs.get(slot_idx + 1)
        if dev is None:
            _clear_port(cfg_dir, port0, logger)
            continue
        cls = vidpid(dev)
        template = templates.get(cls)
        # First managed port → Wii U GamePad template (if configured + present).
        if port0 == managed0[0] and p1_gamepad and \
                _template_path(cfg_dir, p1_gamepad).is_file():
            template = p1_gamepad
        if template is None:
            logger.warning(f"cemu: no template for class {cls} "
                           f"({dev.name!r}); leaving Controller {ui_port}")
            continue
        _write_port_from_template(cfg_dir, port0, template, dev, devs,
                                  sdl_devs, logger)
    return 0
