"""
Parse / regenerate Hypseus (Daphne/Singe) hypinput.ini and write it back, preserving
everything except the joystick columns we deliberately edit. Plus the joystick-value
codec and the global + per-game writers. Backs MAD's "Daphne" button-config page.

FORMAT (classic — the one the live file and the X-Arcade use): a banner of leading
`#` comments, exactly one `[KEYBOARD]` header, one
    KEY_<ACTION> = <key1> <key2> <button> [<axis>]
line per action, then a literal `END`. col1/col2 = SDL keysyms (0 = none); col3 =
joystick BUTTON; col4 (honoured ONLY on UP/DOWN/LEFT/RIGHT) = joystick AXIS.

VALUE CODEC (the single most error-prone field — kept in ONE place here):
    button col = which*100 + button_index + 1   (0 = none; +1 because 0 is reserved)
    axis   col = sign + (which*100 + axis_index + 1), zero-padded to >=3 digits
`which` = SDL joystick instance (0 = first/X-Arcade P1 side, 1 = P2 side). Hypseus
ABORTS without the [KEYBOARD]/END envelope, so we keep every non-edited line verbatim
and only rewrite the col-3/col-4 tokens of the actions we change.

Source: github DirtBagXon/hypseus-singe doc/hypinput.ini + hypjsch; deck-docs/
hypseus-daphne-import.md. hypinput.ini is NOT rewritten by ES-DE (safe to edit
anytime); we still back up to .bak before the first write (CLAUDE.md rule #5).
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

GLOBAL_INI = Path.home() / "Applications" / "hypseus-singe" / "hypinput.ini"

# The 22 mapped actions Hypseus parses, in display order. (KEY_HOOK is a token with
# no default mapping — a Singe scripting hook — so it is not user-editable here.)
ACTIONS = ["UP", "DOWN", "LEFT", "RIGHT", "COIN1", "COIN2", "START1", "START2",
           "BUTTON1", "BUTTON2", "BUTTON3", "SKILL1", "SKILL2", "SKILL3",
           "SERVICE", "TEST", "RESET", "SCREENSHOT", "QUIT", "PAUSE", "CONSOLE", "TILT"]

# What a typical laserdisc player needs up front; the rest fold under "Advanced".
COMMON = ["COIN1", "START1", "UP", "DOWN", "LEFT", "RIGHT", "BUTTON1", "BUTTON2", "BUTTON3"]
ADVANCED = [a for a in ACTIONS if a not in COMMON]
DIRECTIONS = ["UP", "DOWN", "LEFT", "RIGHT"]          # the only rows the axis column honours

ACTION_LABELS = {
    "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right",
    "COIN1": "Insert coin", "COIN2": "Insert coin (P2)", "START1": "Start", "START2": "Start (P2)",
    "BUTTON1": "Action 1", "BUTTON2": "Action 2", "BUTTON3": "Action 3",
    "SKILL1": "Skill 1", "SKILL2": "Skill 2", "SKILL3": "Skill 3",
    "SERVICE": "Service", "TEST": "Test", "RESET": "Reset", "SCREENSHOT": "Screenshot",
    "QUIT": "Quit emulator", "PAUSE": "Pause", "CONSOLE": "Debug console", "TILT": "Tilt",
}

# Verified-from-source per-driver control notes (shown as a hint in per-game mode). Daphne
# "driving" games have NO analog pedal — gas/brake/booster are DIGITAL buttons (BUTTON1-3).
# Sources: Hypseus src/game/{gpworld,bega,mach3,cobraconv}.cpp (DirtBagXon/hypseus-singe).
GAME_HINTS = {
    "gpworld": "GP World — Steer: Left/Right · Action 1: gear shift · Action 2: accelerate · "
               "Action 3: brake. (Hypseus models the wheel + pedals digitally.)",
    "roadblaster": "Road Blaster — Steer: Left/Right · Action 1: gas · Action 2: brake · Action 3: booster.",
    "mach3": "M.A.C.H. 3 — Action 1: fire (gun) · Action 2: drop bombs.",
    "uvt": "Us vs. Them — Action 1 & Action 2: your two weapons.",
    "cobra": "Cobra Command — Action 1: gun · Action 2: missile (buttons, not a pedal).",
}

# X-Arcade / Xbox-layout SDL joystick button index -> friendly name (file value = index+1).
# Decoded live from 045e:02a1 — deck-docs/hypseus-daphne-import.md:75.
XARCADE_BTN_NAME = {0: "A", 1: "B", 2: "X", 3: "Y", 4: "L1", 5: "R1",
                    6: "Select", 7: "Start", 8: "Guide", 9: "L3", 10: "R3"}

# A clean classic default (the current working live layout) for reset-to-defaults / new files.
DEFAULT_TEMPLATE = """\
# Hypseus input map (classic joystick format).  Columns:  Key1  Key2  Button  (Axis)
# X-Arcade / Steam Deck buttons:  A=1 B=2 X=3 Y=4  L1=5 R1=6  Select=7 Start=8  L3=10 R3=11

[KEYBOARD]
KEY_UP = SDLK_UP SDLK_r 0 -002
KEY_DOWN = SDLK_DOWN SDLK_f 0 +002
KEY_LEFT = SDLK_LEFT SDLK_d 0 -001
KEY_RIGHT = SDLK_RIGHT SDLK_g 0 +001
KEY_COIN1 = SDLK_5 0 7
KEY_COIN2 = SDLK_6 0 0
KEY_START1 = SDLK_1 0 8
KEY_START2 = SDLK_2 0 0
KEY_BUTTON1 = SDLK_LCTRL SDLK_a 1
KEY_BUTTON2 = SDLK_LALT SDLK_s 2
KEY_BUTTON3 = SDLK_SPACE SDLK_d 3
KEY_SKILL1 = SDLK_LSHIFT SDLK_w 3
KEY_SKILL2 = SDLK_z SDLK_i 4
KEY_SKILL3 = SDLK_x SDLK_k 2
KEY_SERVICE = SDLK_9 0 0
KEY_TEST = SDLK_F2 0 0
KEY_RESET = SDLK_0 0 0
KEY_SCREENSHOT = SDLK_F12 0 0
KEY_QUIT = SDLK_ESCAPE SDLK_q 17
KEY_PAUSE = SDLK_p 0 0
KEY_CONSOLE = SDLK_BACKSLASH 0 0
KEY_TILT = SDLK_t 0 0
END
"""


# ---------------------------------------------------------------------------
# value codec (button col-3 / axis col-4) — the off-by-one + which*100 lives ONLY here
# ---------------------------------------------------------------------------
def encode_button(index: int, which: int = 0) -> int:
    """SDL joystick button index (0-based) -> hypinput col-3 value."""
    return which * 100 + index + 1


def decode_button(value) -> tuple[int, int] | None:
    """col-3 value -> (which, button_index), or None when 0/blank/non-numeric."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    which, rem = divmod(v, 100)
    return which, rem - 1


def button_label(value) -> str:
    """Friendly label for a col-3 value: 2 -> 'B', 102 -> 'P2 B', 0 -> '— (unbound)',
    17 -> '#17 (legacy)' (a value with no current X-Arcade button — never mis-shown)."""
    d = decode_button(value)
    if d is None:
        return "— (unbound)"
    which, idx = d
    name = XARCADE_BTN_NAME.get(idx)
    if name is None:
        return f"#{value} (legacy?)"
    return (f"P{which + 1} " if which else "") + name


def encode_axis(axis_index: int, positive: bool, which: int = 0) -> str:
    """SDL axis index + direction -> hypinput col-4 value, e.g. (1, False) -> '-002'."""
    sign = "+" if positive else "-"
    return f"{sign}{which * 100 + axis_index + 1:03d}"


def decode_axis(value) -> tuple[int, int, bool] | None:
    """col-4 value -> (which, axis_index, positive), or None when 0/blank."""
    if value in (None, "", "0", 0):
        return None
    s = str(value).strip()
    positive = not s.startswith("-")
    try:
        n = abs(int(s))
    except ValueError:
        return None
    if n <= 0:
        return None
    which, rem = divmod(n, 100)
    return which, rem - 1, positive


# ---------------------------------------------------------------------------
# line-preserving parser / model
# ---------------------------------------------------------------------------
_KEY_RE = re.compile(r"^(?P<prefix>\s*)KEY_(?P<action>[A-Z0-9]+)(?P<sep>\s*=\s*)(?P<rhs>.*?)(?P<suffix>\s*)$")


class HypInput:
    """An ordered view of a hypinput.ini. Editing only touches the col-3/col-4 tokens
    of the actions changed; every other line round-trips verbatim."""

    def __init__(self, lines: list[str], entries: dict, had_final_newline: bool):
        self.lines = lines            # original physical lines (no newline chars)
        self.entries = entries        # ACTION -> dict(idx, key1, key2, button, axis, prefix, sep)
        self._nl = had_final_newline

    # -- reads --
    def has(self, action: str) -> bool:
        return action in self.entries

    def button_value(self, action: str) -> int:
        e = self.entries.get(action)
        if not e:
            return 0
        try:
            return int(e["button"])
        except (TypeError, ValueError):
            return 0

    def axis_value(self, action: str):
        e = self.entries.get(action)
        return e["axis"] if e else None

    def keysyms(self, action: str) -> tuple[str, str]:
        e = self.entries.get(action)
        return (e["key1"], e["key2"]) if e else ("0", "0")

    # -- writes (in-memory) --
    def set_button(self, action: str, value: int) -> None:
        e = self.entries.get(action)
        if e is None:
            return
        e["button"] = str(int(value))
        self._rebuild(action)

    def clear_button(self, action: str) -> None:
        self.set_button(action, 0)

    def set_axis(self, action: str, value) -> None:
        """Set/clear the col-4 axis token (only meaningful on UP/DOWN/LEFT/RIGHT)."""
        e = self.entries.get(action)
        if e is None:
            return
        e["axis"] = None if value in (None, "", 0, "0") else str(value)
        self._rebuild(action)

    def _rebuild(self, action: str) -> None:
        e = self.entries[action]
        toks = [e["key1"], e["key2"], e["button"]]
        if e["axis"] is not None:
            toks.append(e["axis"])
        self.lines[e["idx"]] = f'{e["prefix"]}KEY_{action}{e["sep"]}{" ".join(toks)}'

    def text(self) -> str:
        out = "\n".join(self.lines)
        return out + "\n" if self._nl else out


def parse(text: str) -> HypInput:
    had_nl = text.endswith("\n")
    lines = text.split("\n")
    if had_nl and lines and lines[-1] == "":
        lines.pop()                                   # drop the artdefact empty tail from split
    entries: dict = {}
    for i, ln in enumerate(lines):
        m = _KEY_RE.match(ln)
        if not m:
            continue
        action = m.group("action")
        if action not in ACTIONS:                     # ignore HOOK / unknown — kept verbatim
            continue
        toks = m.group("rhs").split()
        entries[action] = {
            "idx": i,
            "key1": toks[0] if len(toks) > 0 else "0",
            "key2": toks[1] if len(toks) > 1 else "0",
            "button": toks[2] if len(toks) > 2 else "0",
            "axis": toks[3] if len(toks) > 3 else None,
            "prefix": m.group("prefix"),
            "sep": m.group("sep"),
        }
    return HypInput(lines, entries, had_nl)


def load(path=GLOBAL_INI) -> HypInput:
    """Parse an existing hypinput.ini (or the default template if it's missing)."""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else DEFAULT_TEMPLATE
    return parse(text)


def load_default() -> HypInput:
    """A fresh map from the built-in default template (for reset / new per-game)."""
    return parse(DEFAULT_TEMPLATE)


# ---------------------------------------------------------------------------
# writers (atomic; back up once before the first write — CLAUDE.md rule #5)
# ---------------------------------------------------------------------------
def _atomic_write(path: Path, text: str, *, backup: bool = True) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.is_file():
        bak = path.with_suffix(path.suffix + ".bak")
        if not bak.exists():                          # keep the EARLIEST pristine original
            shutil.copy2(path, bak)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_global(hi: HypInput) -> None:
    _atomic_write(GLOBAL_INI, hi.text())


def per_game_ini(gamedir, basename) -> Path:
    """The per-game keymap file. MUST end .ini, and is LOWERCASE-named: Hypseus lowercases the
    -keymapfile path internally (a Windows-ism), so on case-sensitive Linux the file must be
    lowercase or the lookup misses it (e.g. Time_Holo -> time_holo.ini)."""
    return Path(gamedir) / f"{basename.lower()}.ini"


def per_game_commands(gamedir, basename) -> Path:
    return Path(gamedir) / f"{basename}.commands"


def write_per_game(gamedir, basename, hi: HypInput) -> Path:
    """Write <gamedir>/<basename>.ini and ensure <basename>.commands injects it.
    Returns the .ini path."""
    ini = per_game_ini(gamedir, basename)
    _atomic_write(ini, hi.text())
    merge_keymapfile_commands(gamedir, basename, ini)
    return ini


def merge_keymapfile_commands(gamedir, basename, ini_path) -> None:
    """Ensure <basename>.commands carries `-keymapfile <ini_path>`, MERGING with any
    existing single-line flags (e.g. Time_Holo's `-x 798 -y 532 ...`): drop a prior
    -keymapfile/-config pair, keep everything else, append the new pair."""
    cpath = per_game_commands(gamedir, basename)
    existing = ""
    if cpath.is_file():
        existing = cpath.read_text(encoding="utf-8", errors="replace").strip()
    toks = existing.split()
    out: list[str] = []
    i = 0
    while i < len(toks):
        if toks[i] in ("-keymapfile", "-config"):
            i += 2                                    # drop the flag AND its value
            continue
        out.append(toks[i])
        i += 1
    # RELATIVE, lowercase filename — NOT an absolute path. Hypseus lowercases the -keymapfile
    # path and then can't find /home/deck/ROMs/... (-> /home/deck/roms/...) on a case-sensitive
    # filesystem, crashing with "Invalid -keymapfile file [Use .ini]". A bare filename resolves
    # against -homedir (= the game dir, set by hypseus-pin.sh) and its lowercase matches the file
    # per_game_ini writes. (ini_path is the absolute file we wrote; we use only its name.)
    out += ["-keymapfile", per_game_ini(gamedir, basename).name]
    _atomic_write(cpath, " ".join(out) + "\n")


def has_per_game(gamedir, basename) -> bool:
    return per_game_ini(gamedir, basename).is_file()


# ---------------------------------------------------------------------------
# Hypseus runtime flags — "instant scene transitions" = -seek_frames_per_ms 0
# (disables the emulated laserdisc SEEK delay). GLOBAL flags live in a small args
# file that hypseus-pin.sh appends to EVERY daphne launch; PER-GAME flags ride the
# existing <game>.commands %INJECT% (composing with the -keymapfile merge above).
# ---------------------------------------------------------------------------
GLOBAL_ARGS = Path.home() / "Emulation" / "storage" / "hypseus" / "global-args"
SEEK_FLAG = "-seek_frames_per_ms"
SEEK_INSTANT = "0"


def _read_args(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _has_flag(line: str, flag: str) -> bool:
    return flag in (line or "").split()


def _set_value_flag(line: str, flag: str, value: str, on: bool) -> str:
    """Add or remove `flag value` in a space-delimited args line, preserving every other
    token. Any existing `flag` (+ the single token after it) is dropped first, so a changed
    value is replaced not duplicated."""
    parts = (line or "").split()
    out: list[str] = []
    i = 0
    while i < len(parts):
        if parts[i] == flag:
            i += 2                                # drop the flag + its one value
            continue
        out.append(parts[i])
        i += 1
    if on:
        out += [flag, value]
    return " ".join(out)


def global_seek_instant() -> bool:
    return _has_flag(_read_args(GLOBAL_ARGS), SEEK_FLAG)


def set_global_seek(on: bool) -> None:
    line = _set_value_flag(_read_args(GLOBAL_ARGS), SEEK_FLAG, SEEK_INSTANT, on)
    GLOBAL_ARGS.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(GLOBAL_ARGS, line + "\n" if line else "", backup=False)   # "" → launcher appends nothing


def per_game_seek_instant(gamedir, basename) -> bool:
    return _has_flag(_read_args(per_game_commands(gamedir, basename)), SEEK_FLAG)


def set_per_game_seek(gamedir, basename, on: bool) -> None:
    cpath = per_game_commands(gamedir, basename)
    line = _set_value_flag(_read_args(cpath), SEEK_FLAG, SEEK_INSTANT, on)
    _atomic_write(cpath, line + "\n" if line else "", backup=True)


# ---------------------------------------------------------------------------
# headless self-test:  python3 lib/hypinput.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    ok = True

    # codec round-trips
    assert encode_button(1) == 2 and decode_button(2) == (0, 1)
    assert encode_button(10, which=1) == 111 and decode_button(111) == (1, 10)
    assert button_label(2) == "B" and button_label(102) == "P2 B"
    assert button_label(0) == "— (unbound)" and "legacy" in button_label(17)
    assert encode_axis(1, False) == "-002" and encode_axis(0, True) == "+001"
    assert decode_axis("-002") == (0, 1, False) and decode_axis("0") is None
    print("codec: OK")

    # parse + edit + round-trip on the default template (no unintended drift)
    hi = load_default()
    before = hi.text()
    assert hi.button_value("BUTTON1") == 1
    hi.set_button("BUTTON1", encode_button(1))         # A(1) -> B(2)
    after = hi.text()
    diff = [(a, b) for a, b in zip(before.split("\n"), after.split("\n")) if a != b]
    assert diff == [("KEY_BUTTON1 = SDLK_LCTRL SDLK_a 1", "KEY_BUTTON1 = SDLK_LCTRL SDLK_a 2")], diff
    assert before.count("\n") == after.count("\n"), "line count changed"
    print("parse/edit single-line diff: OK")

    # round-trip the LIVE file unchanged (byte-identical)
    if GLOBAL_INI.is_file():
        raw = GLOBAL_INI.read_text(encoding="utf-8", errors="replace")
        if parse(raw).text() == raw:
            print(f"live round-trip byte-identical: OK ({GLOBAL_INI})")
        else:
            print("live round-trip MISMATCH", file=sys.stderr)
            ok = False

    # .commands merge preserves existing flags
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        c = Path(d) / "game.commands"
        c.write_text("-x 798 -y 532 -fullscreen_window -noserversend\n")
        merge_keymapfile_commands(d, "game", Path(d) / "game.ini")
        got = c.read_text().strip()
        # -keymapfile is a RELATIVE lowercase name (resolves via -homedir; Hypseus lowercases paths)
        assert got == "-x 798 -y 532 -fullscreen_window -noserversend -keymapfile game.ini", got
        # an uppercase basename lowercases its .ini name
        assert per_game_ini(d, "Time_Holo").name == "time_holo.ini", per_game_ini(d, "Time_Holo").name
        # re-merge must not duplicate
        merge_keymapfile_commands(d, "game", Path(d) / "game.ini")
        assert c.read_text().count("-keymapfile") == 1
        print("commands merge (preserve + no-dup): OK")

    # seek-flag add/remove preserves the rest of the args line
    assert _set_value_flag("", SEEK_FLAG, "0", True) == "-seek_frames_per_ms 0"
    assert _set_value_flag("-seek_frames_per_ms 0", SEEK_FLAG, "0", False) == ""
    assert _set_value_flag("-x 798 -y 532 -seek_frames_per_ms 0 -noserversend", SEEK_FLAG, "0", False) \
        == "-x 798 -y 532 -noserversend"
    assert _set_value_flag("-x 798 -noserversend", SEEK_FLAG, "0", True) \
        == "-x 798 -noserversend -seek_frames_per_ms 0"
    assert _has_flag("-x 1 -seek_frames_per_ms 0", SEEK_FLAG) and not _has_flag("-x 1", SEEK_FLAG)
    print("seek flag add/remove (preserve others): OK")

    print("ALL OK" if ok else "FAILURES")
    sys.exit(0 if ok else 1)
