"""pcsx2hk.* — the PCSX2 Hotkeys remapper (flat global [Hotkeys] section).

PCSX2 stores each hotkey action as ONE binding string in the flat [Hotkeys] section of
~/.config/PCSX2/inis/PCSX2.ini. A binding is a CHORD: tokens joined by " & " (all held
together), each token either `Keyboard/<QtKey>` or `SDL-N/<Name>` (with +/-/~ axis
prefixes), and a chord may mix keyboard + pad freely — mirroring PCSX2's own
`InputManager::ConvertInputBindingKeysToString`.

Routed through the generic input_map page (arg "pcsx2hk"); every row is kind "chord" so the
capture modal accumulates any simultaneously-held inputs (2+ keys, a pad chord, an analog
trigger, the Guide button, a DS4/DualSense trackpad click). Pad tokens are written with the
placeholder index `SDL-0/`; the launch binder (lib/switch_bind) rewrites that to router
Player 1's live SDL index at launch (transient, reverted on exit), so pad hotkeys always
fire on Player 1's controller. Keyboard tokens need no rewrite.

The action list is the compiled-in DEFINE_HOTKEY set (Hotkeys.cpp + GS/GS.cpp) grouped by
PCSX2's own 6 categories; any UNKNOWN live keys (e.g. ZoomIn/ZoomOut) are shown + PRESERVED,
never dropped. PCSX2 rewrites its ini on EXIT, so writes refuse while pcsx2-qt runs.
"""
from __future__ import annotations

from pathlib import Path

from .. import proc_guard
from . import capture_cmds, cfgutil
from .input_buffer import InputBuffer
from .input_translate import sdl_button_source, sdl_source_label, usb_keyboard_source
from .rpc import RpcError, method

_INI = Path("~/.config/PCSX2/inis/PCSX2.ini").expanduser()
_SECTION = "Hotkeys"

# The compiled-in DEFINE_HOTKEY actions (Hotkeys.cpp + GS/GS.cpp), grouped by PCSX2's own
# categories. See deck-docs/pcsx2-ini-encodings.md (verified vs /home/deck/pcsx2x6-src).
_ACTIONS = [
    ("Navigation", [
        ("ToggleFullscreen", "Toggle Fullscreen"),
        ("OpenPauseMenu", "Open Pause Menu"),
        ("OpenAchievementsList", "Open Achievements"),
        ("OpenLeaderboardsList", "Open Leaderboards"),
    ]),
    ("Frame control", [
        ("TogglePause", "Pause / Resume"),
        ("FrameAdvance", "Frame Advance"),
        ("ToggleFrameLimit", "Toggle Frame Limit"),
        ("ToggleTurbo", "Toggle Turbo (fast-forward)"),
        ("HoldTurbo", "Turbo (hold)"),
        ("ToggleSlowMotion", "Toggle Slow Motion"),
        ("IncreaseSpeed", "Increase Speed"),
        ("DecreaseSpeed", "Decrease Speed"),
    ]),
    ("System", [
        ("ShutdownVM", "Shut Down"),
        ("ResetVM", "Reset"),
        ("ReloadPatches", "Reload Patches"),
        ("SwapMemCards", "Swap Memory Cards"),
        ("InputRecToggleMode", "Input Recording Mode"),
        ("ToggleMouseLock", "Toggle Mouse Lock"),
    ]),
    ("Save states", [
        ("SaveStateToSlot", "Save State (current slot)"),
        ("LoadStateFromSlot", "Load State (current slot)"),
        ("LoadBackupStateFromSlot", "Load Backup State"),
        ("PreviousSaveStateSlot", "Previous Slot"),
        ("NextSaveStateSlot", "Next Slot"),
        ("SaveStateAndSelectNextSlot", "Save then Select Next Slot"),
        ("SelectNextSlotAndSaveState", "Select Next Slot then Save"),
    ]),
    ("Save to slot", [(f"SaveStateToSlot{n}", f"Save to Slot {n}") for n in range(1, 11)]),
    ("Load from slot", [(f"LoadStateFromSlot{n}", f"Load from Slot {n}") for n in range(1, 11)]),
    ("Audio", [
        ("Mute", "Toggle Mute"),
        ("IncreaseVolume", "Increase Volume"),
        ("DecreaseVolume", "Decrease Volume"),
    ]),
    ("Graphics", [
        ("Screenshot", "Screenshot"),
        ("ToggleVideoCapture", "Toggle Video Capture"),
        ("GSDumpSingleFrame", "GS Dump (single frame)"),
        ("GSDumpMultiFrame", "GS Dump (multi frame)"),
        ("ToggleSoftwareRendering", "Toggle Software Rendering"),
        ("IncreaseUpscaleMultiplier", "Increase Upscale"),
        ("DecreaseUpscaleMultiplier", "Decrease Upscale"),
        ("ToggleOSD", "Toggle On-Screen Display"),
        ("CycleAspectRatio", "Cycle Aspect Ratio"),
        ("ToggleMipmapMode", "Toggle Mipmapping"),
        ("CycleInterlaceMode", "Cycle Deinterlacing"),
        ("CycleTVShader", "Cycle TV Shader"),
        ("CycleBlendingAccuracy", "Cycle Blending Accuracy"),
        ("ToggleTextureDumping", "Toggle Texture Dumping"),
        ("ToggleTextureReplacements", "Toggle Texture Replacements"),
        ("ReloadTextureReplacements", "Reload Texture Replacements"),
    ]),
]
_KNOWN_KEYS = {k for _, binds in _ACTIONS for k, _ in binds}


def _running() -> bool:
    # exact=True → `pgrep -x pcsx2-qt` (process NAME match), like pcsx2_input_cmds._running.
    return proc_guard.process_running("pcsx2-qt", exact=True)


def _render_token(code: int) -> str | None:
    """A captured evdev code → a PCSX2 [Hotkeys] token, or None if unbindable. Keyboard keys
    → `Keyboard/<QtKey>`; pad buttons / triggers / Guide / trackpad → `SDL-0/<Name>` (the
    launch binder rewrites the 0 to Player 1's live SDL index)."""
    ra = capture_cmds.ra_keyname(code)          # a keyboard key?
    if ra:
        return usb_keyboard_source(ra)
    src = sdl_button_source(code)               # a pad button / trigger / Guide / trackpad
    return f"SDL-0/{src}" if src else None


def _label_token(tok: str) -> str:
    """Friendly label for one stored [Hotkeys] token."""
    tok = tok.strip()
    if tok.startswith("Keyboard/"):
        return tok.split("/", 1)[1]
    if tok.startswith("SDL-") and "/" in tok:
        return sdl_source_label(tok.split("/", 1)[1])
    return tok


def _render_value(stored: str | None) -> str:
    """Friendly display of a stored binding (' & '-joined), or '—' if unbound."""
    stored = (stored or "").strip()
    if not stored:
        return "—"
    return " + ".join(_label_token(t) for t in stored.split(" & ") if t.strip())


def _unknown_keys(text: str) -> list[str]:
    """[Hotkeys] keys present in the live ini that we don't hardcode — shown + preserved."""
    seen, out = set(), []
    for k in cfgutil.ini_keys(text, _SECTION):
        if k not in _KNOWN_KEYS and k not in seen:
            seen.add(k); out.append(k)
    return out


def _valid_key(key: str, text: str) -> bool:
    """A known action, or an unrecognised key that already exists (so we never create a
    junk key, but can still rebind/clear a hotkey PCSX2 itself wrote)."""
    return bool(key) and (key in _KNOWN_KEYS or key in cfgutil.ini_keys(text, _SECTION))


@method("pcsx2hk.input_get", slow=True)   # buffered: NO cache=("config",) — the in-memory buffer IS the cache
def _input_get(params):
    text = _buf.get(_INI)               # buffer-over-disk: reflects staged, unsaved edits
    run = _running()

    def row(key, label):
        # A hotkey may have several alternative binding lines (PCSX2 reads [Hotkeys] as a list);
        # show them all so a user who added alternatives in PCSX2's own UI sees the true state.
        vals = cfgutil.ini_read_all(text, _SECTION, key)
        value = " / ".join(_render_value(v) for v in vals) if vals else "—"
        return {"id": key, "label": label, "kind": "chord",
                "value": value, "capturable": not run}

    groups = [{"title": title, "binds": [row(k, l) for k, l in binds]}
              for title, binds in _ACTIONS]
    extra = _unknown_keys(text)
    if extra:
        groups.append({"title": "Other (set in PCSX2)",
                       "binds": [row(k, k) for k in extra]})
    note = ("Close PCSX2 first, it rewrites this file on exit." if run else
            "Bind each action to a keyboard key/combo or a controller button/chord "
            "(hold them together). Pad hotkeys use Player 1's controller. "
            "Highlight a row and press Start to clear it.")
    return {"running": run, "note": note, "groups": groups, "clearable": True,
            "buffered": True, "dirty": _buf.dirty}


# ---------------------------------------------------------------------------
# Buffered editor plumbing (X=Save / Y=Cancel). Edits stage in a per-ini InputBuffer and
# only reach disk on <ns>.input_save; <ns>.input_cancel drops them. ctx = the target ini
# PATH (never ()), so pcsx2x6's arcade + retail inis never share one buffer's state. The pure
# _apply + _binding_from_params + make_hotkey_buffer below are REUSED verbatim by
# pcsx2x6_hotkeys_cmds (pointed at the fork inis + the pcsx2x6 process guard).
# ---------------------------------------------------------------------------
def _binding_from_params(params) -> tuple[str, str]:
    """Build the ' & '-joined [Hotkeys] binding string from a capture. Accepts a `codes` chord
    list or a scalar `value` (the generic btn path). Raises EINVAL on empty / unmappable input.
    Returns (binding, rendered_value). Pure (codes -> string, no disk), so it runs at stage time."""
    codes = params.get("codes")
    if codes is None and str(params.get("value", "")).strip():
        try:
            codes = [int(params.get("value"))]
        except (TypeError, ValueError):
            codes = None
    if not codes:
        raise RpcError("EINVAL", "press a key or button, or hold a chord")
    tokens = []
    for c in codes:
        try:
            tok = _render_token(int(c))
        except (TypeError, ValueError):
            tok = None
        if tok is None:
            raise RpcError("EINVAL", "that input can't be bound as a hotkey")
        tokens.append(tok)
    binding = " & ".join(tokens)
    return binding, _render_value(binding)


def _apply(text: str, edit: dict, *, running, proc: str) -> str:
    """Apply one staged [Hotkeys] edit to `text`, returning the new text. Pure: NO disk I/O,
    NO staterev bump. Replayed verbatim by the buffer's flush onto a FRESH disk read, so a
    foreign edit to OTHER [Hotkeys] keys (and every other section) survives. The
    emulator-running EBUSY guard lives HERE so it fires at BOTH stage and save. Preserves the
    multi-line duplicate-binding collapse and the create-section-when-absent behaviour."""
    if running():
        raise RpcError("EBUSY", f"close {proc} first; it rewrites its config on exit")
    key = edit.get("id") or edit.get("key") or ""
    if not _valid_key(key, text):
        raise RpcError("EINVAL", f"{key!r} is not a PCSX2 hotkey action")
    if edit.get("op") == "clear":
        return cfgutil.ini_remove_all(text, _SECTION, key)   # drop EVERY alternative line for this action
    binding = edit.get("binding", "")
    t = text
    # PCSX2 allows a hotkey to have several alternative binding lines; collapse any pre-existing
    # duplicates so a rebind leaves exactly ONE line (not a stale alternative that keeps firing).
    if len(cfgutil.ini_read_all(t, _SECTION, key)) > 1:
        t = cfgutil.ini_remove_all(t, _SECTION, key)
    new = cfgutil.ini_set_or_insert(t, _SECTION, key, binding)
    if new is None:                       # [Hotkeys] section absent — create it, then insert
        base = t + ("" if not t or t.endswith("\n") else "\n") + f"[{_SECTION}]\n"
        new = cfgutil.ini_set_or_insert(base, _SECTION, key, binding)
    if new is None:
        raise RpcError("EIO", "could not write the [Hotkeys] section")
    return new


def make_hotkey_buffer(*, running, proc: str) -> InputBuffer:
    """Build an InputBuffer for a [Hotkeys] ini, keyed on ctx = the ini PATH. `running` is a
    zero-arg predicate (resolved at CALL time, so a test can swap the module's _running) and
    `proc` names the emulator in the guard/ENOENT messages. Reused by pcsx2hk (one ini) and
    pcsx2x6 (a separate buffer per fork ini)."""
    def _load(ctx) -> str:
        ini = Path(ctx)
        if not ini.is_file():
            raise RpcError("ENOENT", f"{proc} config not found at {ini}")
        return ini.read_text(encoding="utf-8", errors="replace")

    def _apply_edit(text: str, edit: dict):
        return _apply(text, edit, running=running, proc=proc), edit

    def _flush(ctx, disk: str, edits: list) -> str:
        ini = Path(ctx)
        if not ini.is_file():
            raise RpcError("ENOENT", f"{proc} config not found at {ini}")
        text = ini.read_text(encoding="utf-8", errors="replace")   # replay onto FRESH disk
        for edit in edits:
            text = _apply(text, edit, running=running, proc=proc)
        cfgutil.ensure_bak(ini)
        cfgutil.atomic_write(ini, text)
        return text

    return InputBuffer(load=_load, apply_edit=_apply_edit, flush=_flush)


_buf = make_hotkey_buffer(running=lambda: _running(), proc="PCSX2")


@method("pcsx2hk.input_set", slow=True)
def _input_set(params):
    key = params.get("id", "")
    # A chord sends the held evdev codes as `codes`; a single-button capture may send a scalar
    # `value` (the generic btn path) — accept both. Token building validates the input here; the
    # disk-dependent checks + EBUSY guard fire inside _apply, at both stage and save.
    binding, shown = _binding_from_params(params)
    _buf.set(_INI, {"op": "set", "id": key, "binding": binding})   # stage in memory; no disk write
    return {"id": key, "value": shown, "dirty": _buf.dirty,
            "message": f"{key} → {shown}"}


@method("pcsx2hk.input_clear", slow=True)
def _input_clear(params):
    key = params.get("id") or params.get("key") or ""
    _buf.set(_INI, {"op": "clear", "id": key})                     # stage in memory; no disk write
    return {"id": key, "value": "—", "dirty": _buf.dirty, "message": f"{key} cleared"}


@method("pcsx2hk.input_save", slow=True)
def _input_save(params):
    return {"saved": _buf.save(_INI), "dirty": _buf.dirty}


@method("pcsx2hk.input_cancel", slow=True)
def _input_cancel(params):
    _buf.cancel(_INI)
    return {"cancelled": True, "dirty": _buf.dirty}
