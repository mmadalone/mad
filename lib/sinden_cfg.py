"""Read/write the Sinden LightgunMono.exe.config + the gun cameras' live V4L2 controls + the
cursor-smoother ini — backing MAD's lightgun config pages.

Scope guards (house rules): MOUSE MODE ONLY — never writes the JoystickMode* key family; and
never writes SerialPortWrite*/SerialPortSecondary* (pinned by sinden-serial-preflight.py). A
one-time backup is taken before the first write.
"""
import configparser
import html
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import fsutil
from . import mad_paths

CONFIG = Path.home() / "Lightgun" / "LightgunMono.exe.config"
BACKUP = CONFIG.with_name(CONFIG.name + ".mad-backup")

# Camera nodes are FIXED to what the driver actually opens. The udev/v4l2 LABELS are inverted
# (v4l2-ctl calls /dev/video0 "SindenCamD"), but calibrate.log shows the driver opens P1 on
# /dev/video0 and P2 on /dev/video2. So map by node, NEVER by camera name.
CAM = {1: "/dev/video0", 2: "/dev/video2"}

# Gun buttons (mouse mode). Base names used to build the config keys + friendly labels.
BUTTONS = ["Trigger", "PumpAction", "FrontLeft", "RearLeft", "FrontRight", "RearRight",
           "Up", "Down", "Left", "Right"]
BUTTON_LABELS = {
    "Trigger": "Trigger", "PumpAction": "Pump action", "FrontLeft": "Front-left",
    "RearLeft": "Rear-left", "FrontRight": "Front-right", "RearRight": "Rear-right",
    "Up": "D-pad Up", "Down": "D-pad Down", "Left": "D-pad Left", "Right": "D-pad Right",
}
# Modifier numeric encoding is NOT documented in the config (only the text "shift/ctrl/alt").
# Defaults stay 0 everywhere, so this is advanced/low-risk; confirm on-box if ever used.
MODIFIERS = [(0, "None"), (1, "Shift"), (2, "Ctrl"), (3, "Alt")]


def key(base, player=1, *, offscreen=False, mod=False):
    """Config key name for a gun button (MOUSE MODE only — never JoystickMode*).
    key('Trigger') -> 'ButtonTrigger'; key('Trigger', 2, offscreen=True) ->
    'ButtonTriggerOffscreenP2'; key('Trigger', 1, mod=True) -> 'TriggerMod';
    key('Trigger', 2, offscreen=True, mod=True) -> 'TriggerOffscreenModP2'.
    (Mod keys drop the 'Button' prefix; 'P2' is always appended last — verified vs the file.)"""
    off = "Offscreen" if offscreen else ""
    name = f"{base}{off}Mod" if mod else f"Button{base}{off}"
    return name + ("P2" if player == 2 else "")


def cam_key(base, player=1):
    """Camera config key: cam_key('Brightness', 2) -> 'CameraBrightnessP2'.
    base in {Brightness, Contrast, Exposure, ExposureAuto}."""
    return f"Camera{base}" + ("P2" if player == 2 else "")


# ---- assignable-action value table (from the config's own legend, lines 285-300) ----
_DIGITS = [(8 + i, f"Key {i}") for i in range(10)]                 # 8-17  = 0-9
_UPPER = [(18 + i, f"Key {chr(65 + i)}") for i in range(26)]       # 18-43 = A-Z
_LOWER = [(44 + i, f"Key {chr(97 + i)}") for i in range(26)]       # 44-69 = a-z (recommended)
_MOUSE = [(1, "Mouse Left"), (2, "Mouse Middle"), (3, "Mouse Right"),
          (4, "Pause movement"), (5, "Turbo-fire (L)"), (6, "Turbo-fire / Reload")]
_SPECIAL = [(70, "Return"), (71, "Space"), (72, "Escape"), (73, "Tab"),
            (74, "Arrow Up"), (75, "Arrow Down"), (76, "Arrow Left"), (77, "Arrow Right"),
            (78, "+"), (79, "-"), (80, ".")]                       # 78-81 legend = "+,-." (81 unclear → omit)
_FKEYS = [(82 + i, f"F{i + 1}") for i in range(12)]               # 82-93 = F1-F12
_JOY = [(94 + i, f"Joystick {i + 1}") for i in range(20)]         # 94-113 (joystick — not used here)

# label lookup over ALL codes (so an existing value still displays, even uppercase/joystick)
_LABELS = {0: "None", 7: "AltB (border)", 81: "(81)"}
for _grp in (_MOUSE, _DIGITS, _UPPER, _LOWER, _SPECIAL, _FKEYS, _JOY):
    _LABELS.update(dict(_grp))

# Picker groups for MOUSE MODE: no joystick, lowercase letters only (the recommended set).
ACTION_GROUPS = [
    ("None", [(0, "None")]),
    ("Mouse", _MOUSE),
    ("Keyboard a-z", _LOWER),
    ("Digits 0-9", _DIGITS),
    ("Special keys", _SPECIAL),
    ("Function keys", _FKEYS),
]


def label_for(value):
    """Friendly label for a stored action value (e.g. '72' -> 'Escape')."""
    try:
        return _LABELS.get(int(value), f"#{value}")
    except (TypeError, ValueError):
        return str(value or "—")


# ---- config read/write ----
def get(key_name, default=""):
    """Current value of a config key (or default if file/key missing)."""
    try:
        txt = CONFIG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default
    m = re.search(r'<add key="' + re.escape(key_name) + r'" value="([^"]*)"', txt)
    return m.group(1) if m else default


def backup_once():
    """One-time backup before MAD's first write (recoverable). Returns the path if it made one."""
    try:
        if not BACKUP.exists() and CONFIG.is_file():
            shutil.copy2(CONFIG, BACKUP)
            return BACKUP
    except OSError:
        pass
    return None


def _safe(k):
    """MAD must never write pinned serial keys or the joystick-mode family."""
    return not (k.startswith("SerialPort") or k.startswith("JoystickMode"))


def set_many(pairs):
    """Write {key: value} into the config in one atomic pass (tmp + rename, with a one-time
    backup). Unsafe keys (SerialPort*, JoystickMode*) are skipped. Keys absent from the file
    are silently ignored (every key we write already exists in the stock config)."""
    txt = CONFIG.read_text(encoding="utf-8", errors="replace")
    for k, v in pairs.items():
        if not _safe(k):
            continue
        pat = re.compile(r'(<add key="' + re.escape(k) + r'" value=")[^"]*(")')
        # html.escape so a value containing & < > " can't corrupt the XML attribute.
        txt = pat.sub(lambda m, val=html.escape(str(v)): m.group(1) + val + m.group(2), txt, count=1)
    # Atomic write + one-time backup: the backup copy RAISES on failure (not swallowed), and a
    # mid-write crash can't leave a stray .tmp or a half-written config.
    fsutil.atomic_write_text(CONFIG, txt, backup_once_suffix=".mad-backup")


# ---- live V4L2 camera controls ----
# config camera base -> V4L2 control name (the driver just sets these at start; we set them live)
CAM_CTRL = {"Brightness": "brightness", "Contrast": "contrast",
            "ExposureAuto": "auto_exposure", "Exposure": "exposure_time_absolute"}


def list_ctrl(dev, name):
    """{'min','max','step','default','value','flags'} for a V4L2 control, or None."""
    try:
        out = subprocess.run(["v4l2-ctl", "-d", dev, "--list-ctrls"],
                             capture_output=True, text=True, timeout=4).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(rf"^\s*{re.escape(name)}\s+0x[0-9a-f]+\s+\(\w+\)\s*:\s*(.*)$", out, re.M)
    if not m:
        return None
    tail, d = m.group(1), {}
    for fld in ("min", "max", "step", "default", "value"):
        mm = re.search(rf"\b{fld}=(-?\d+)", tail)
        if mm:
            d[fld] = int(mm.group(1))
    fm = re.search(r"flags=(\S+)", tail)
    d["flags"] = fm.group(1) if fm else ""
    return d or None


def get_ctrl(dev, name):
    try:
        out = subprocess.run(["v4l2-ctl", "-d", dev, "-C", name],
                             capture_output=True, text=True, timeout=4).stdout
        m = re.search(r":\s*(-?\d+)", out)
        return int(m.group(1)) if m else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def set_ctrl(dev, name, value):
    """Set a V4L2 control live (fire-and-forget; the camera may be momentarily busy)."""
    try:
        subprocess.run(["v4l2-ctl", "-d", dev, "--set-ctrl", f"{name}={value}"],
                      capture_output=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        pass


# ---- cursor smoother (smoother.ini; applied live via sinden-smoother-preset.sh SIGHUP) ----
SMOOTHER_INI = mad_paths.storage("sinden", "smoother.ini")


def smoother_get():
    """(alpha, deadzone, snap_threshold) floats, with the daemon's defaults if unset."""
    a, dz, snap = 0.12, 1.6, 1000.0
    try:
        cp = configparser.ConfigParser()
        cp.read(SMOOTHER_INI)
        s = cp["smoothing"]
        a = float(s.get("alpha", a))
        dz = float(s.get("deadzone", dz))
        snap = float(s.get("snap_threshold", snap))
    except Exception:
        pass
    return a, dz, snap
