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

from .. import proc_guard, staterev
from . import capture_cmds, cfgutil
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


@method("pcsx2hk.input_get", slow=True, cache=("config",))
def _input_get(params):
    if not _INI.is_file():
        raise RpcError("ENOENT", f"PCSX2 config not found at {_INI}")
    text = _INI.read_text(encoding="utf-8", errors="replace")
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
    return {"running": run, "note": note, "groups": groups, "clearable": True}


def _write(key: str, binding: str) -> None:
    if not _INI.is_file():
        raise RpcError("ENOENT", f"PCSX2 config not found at {_INI}")
    if _running():
        raise RpcError("EBUSY", "close PCSX2 first; it rewrites its config on exit")
    orig = _INI.read_text(encoding="utf-8", errors="replace")
    if not _valid_key(key, orig):
        raise RpcError("EINVAL", f"{key!r} is not a PCSX2 hotkey action")
    text = orig
    # PCSX2 allows a hotkey to have several alternative binding lines; collapse any pre-existing
    # duplicates so a rebind leaves exactly ONE line (not a stale alternative that keeps firing).
    if len(cfgutil.ini_read_all(text, _SECTION, key)) > 1:
        text = cfgutil.ini_remove_all(text, _SECTION, key)
    new = cfgutil.ini_set_or_insert(text, _SECTION, key, binding)
    if new is None:                       # [Hotkeys] section absent — create it, then insert
        base = text + ("" if not text or text.endswith("\n") else "\n") + f"[{_SECTION}]\n"
        new = cfgutil.ini_set_or_insert(base, _SECTION, key, binding)
    if new is None:
        raise RpcError("EIO", "could not write the [Hotkeys] section")
    if new != orig:
        cfgutil.ensure_bak(_INI)
        cfgutil.atomic_write(_INI, new)
    staterev.bump("config")


@method("pcsx2hk.input_set", slow=True)
def _input_set(params):
    key = params.get("id", "")
    # A chord sends the held evdev codes as `codes`; a single-button capture may send a
    # scalar `value` (the generic btn path) — accept both.
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
    _write(key, binding)
    return {"id": key, "value": _render_value(binding),
            "message": f"{key} → {_render_value(binding)}"}


@method("pcsx2hk.input_clear", slow=True)
def _input_clear(params):
    key = params.get("id") or params.get("key") or ""
    if not _INI.is_file():
        raise RpcError("ENOENT", f"PCSX2 config not found at {_INI}")
    if _running():
        raise RpcError("EBUSY", "close PCSX2 first; it rewrites its config on exit")
    text = _INI.read_text(encoding="utf-8", errors="replace")
    if not _valid_key(key, text):
        raise RpcError("EINVAL", f"{key!r} is not a PCSX2 hotkey action")
    new = cfgutil.ini_remove_all(text, _SECTION, key)   # drop EVERY alternative line for this action
    if new != text:
        cfgutil.ensure_bak(_INI)
        cfgutil.atomic_write(_INI, new)
    staterev.bump("config")
    return {"id": key, "value": "—", "message": f"{key} cleared"}
