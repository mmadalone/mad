"""sinden.* / camera.* methods — the Lightgun section (MAD native-panel phase 3).

Ports the Tk lightgun pages' logic 1:1 (router-config-gui.py lightgun() /
_button_map_page / _gun_behavior_page / _camera_tune_page) on top of the
Tk-free lib.sinden_cfg. MAD only CALLS the sinden-*.sh scripts — it never
absorbs their logic (detached, logged to ~/Emulation/storage/control-panel/).

The camera preview is a Stream: its cleanup kills ffmpeg and restores the
driver/LED to the pre-preview state, so the daemon teardown invariant (EOF/
SIGTERM ⇒ children killed, drivers restored) covers every exit path.

The button-map live-press dots are NOT served here: the driver synthesizes
key/mouse events at the display-server level, which reach ES-DE as ordinary
SDL input — the native page matches them in C++ (same semantics as the Tk
page's KeyPress feed).
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path

from .. import sinden_cfg
from .. import fsutil
from .. import mad_paths
from .rpc import RpcError, Stream, method, stop_stream

HERE = Path(__file__).resolve().parent.parent.parent     # lib/madsrv/../.. = launchers
LOGDIR = mad_paths.storage("control-panel")
SMOOTH_OFF = mad_paths.storage("sinden", ".smoothing-off")
SIN_TOOLS = Path.home() / "ROMs" / "sinden"
CONF = HERE / "sinden.conf"
CAM_TMP = Path("/tmp/mad-cam.ppm")

# Driver pause/restore state for the camera preview (port of the Tk
# _cam_driver_paused/_cam_driver_was_running flags). Single panel ⇒ module
# state; only ever touched from camera.* methods + the preview stream.
_drv = {"paused": False, "was_running": False}
# Per-player slider values (seeded from the config on camera.get; live-applied
# while previewing; persisted by camera.save) + the live preview bookkeeping.
# _CAM_LOCK serializes the registration handoff: camera.preview runs on the
# worker pool, cleanup() on stream threads, camera.set on the stdin thread.
_cam = {"vals": {}, "player": None, "stream": None}
_CAM_LOCK = threading.Lock()


def _detached(argv, label: str, interactive: bool = False) -> str:
    """Port of App._run: launch a tool detached, log to control-panel/<label>.log.
    UNLIKE the Tk app, stdin is ALWAYS /dev/null: the daemon's stdin is the
    NDJSON protocol pipe — an inherited read would eat protocol bytes. The
    interactive flag is kept for signature parity only."""
    del interactive
    try:
        LOGDIR.mkdir(parents=True, exist_ok=True)
        with open(LOGDIR / f"{label}.log", "ab") as lf:
            subprocess.Popen([str(a) for a in argv], stdout=lf, stderr=lf,
                             stdin=subprocess.DEVNULL,
                             start_new_session=True)
        return f"▶ {label} started   (log: control-panel/{label}.log)"
    except Exception as ex:
        return f"⚠ couldn't launch {label}: {ex}"


def _driver_running() -> bool:
    try:
        return subprocess.run(["pgrep", "-f", "LightgunMono.exe"],
                              capture_output=True, timeout=3).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _led_webhook(which: str) -> None:
    """Fire the TV-border LED webhook — port of App._sinden_led."""
    var = "SINDEN_LED_WEBHOOK_START" if which == "start" else "SINDEN_LED_WEBHOOK_STOP"
    cmd = ('. "' + str(CONF) + '" 2>/dev/null; '
           '[ "${SINDEN_LED_ENABLED:-0}" = "1" ] && [ -n "${SINDEN_LED_HA_BASE:-}" ] && '
           'curl -fsS -m 3 -X POST "$SINDEN_LED_HA_BASE/api/webhook/$' + var +
           '" >/dev/null 2>&1')
    _detached(["bash", "-c", cmd], "sinden-led")


def _restart_driver() -> str:
    _drv["paused"] = False
    return _detached(["bash", "-c",
                      f"{HERE}/sinden-stop.sh; sleep 1; {HERE}/sinden-start.sh"],
                     "sinden-restart")


def _led_enabled() -> bool:
    try:
        m = re.search(r'^\s*SINDEN_LED_ENABLED\s*=\s*(\d+)', CONF.read_text(), re.M)
        return bool(m) and m.group(1) != "0"
    except Exception:
        return False


@method("sinden.health")
def _health(params):
    """Driver-installation state for the Lightgun page's INSTALL banner.
    Fast: three file stats + a PATH lookup."""
    import shutil
    lg = Path.home() / "Lightgun"
    driver = all((lg / f).is_file() for f in
                 ("LightgunMono.exe", "libCameraInterface.so", "libSdlInterface.so"))
    return {"driver": driver,
            "mono": shutil.which("mono") is not None,
            "config": (lg / "LightgunMono.exe.config").is_file()}


@method("sinden.install")
def _install(params):
    """Run sinden-install.sh (downloads the OFFICIAL bundle from
    sindenlightgun.com — we never redistribute the binaries), streaming its
    progress lines. Reuses the backup module's process-group stream plumbing
    (stop-watcher killpg; {done, rc} on every path; RunFullStream releases
    _RUN_ACTIVE in its finally, so it MUST be acquired here first — shared
    single-flight with backup.run_full, which is correct: both are heavy
    exclusive script jobs)."""
    from .backup_cmds import _RUN_ACTIVE, LAUNCHERS, RunFullStream
    if not _RUN_ACTIVE.acquire(blocking=False):
        raise RpcError("EBUSY", "another install/backup job is already running")
    try:
        return {"stream": RunFullStream([str(LAUNCHERS / "sinden-install.sh")]).start()}
    except Exception:
        _RUN_ACTIVE.release()
        raise


@method("sinden.status", slow=True)
def _status(params):
    """Root-page state (slow: pgrep). Smoother enabled = no .smoothing-off marker."""
    alpha, deadzone, snap = sinden_cfg.smoother_get()
    return {"driver_running": _driver_running(),
            "smoother": {"alpha": alpha, "deadzone": deadzone, "snap": snap,
                         "enabled": not SMOOTH_OFF.exists()},
            "led_enabled": _led_enabled(),
            "cams": {str(p): d for p, d in sinden_cfg.CAM.items()}}


@method("sinden.driver", slow=True)
def _driver(params):
    """start | stop | restart | calibrate | test — detached scripts, Tk parity."""
    action = params.get("action")
    if action == "start":
        return {"message": _detached([HERE / "sinden-start.sh"], "sinden-start")}
    if action == "stop":
        return {"message": _detached([HERE / "sinden-stop.sh"], "sinden-stop")}
    if action == "restart":
        _restart_driver()
        return {"message": "↻ restarting driver… (~3 s)"}
    if action == "calibrate":
        return {"message": _detached([HERE / "sinden-calibrate.sh"], "sinden-calibrate",
                                     interactive=True)}
    if action == "test":
        _detached([HERE / "sinden-test.sh"], "sinden-test")
        return {"message": "Both guns active (driver + MPX up). Aim in a game, or use "
                           "Calibrate, to SEE both cursors — they don't render over this "
                           "panel in Game Mode. Stop when done."}
    raise RpcError("EINVAL", f"unknown driver action {action!r}")


@method("sinden.apply", slow=True)
def _apply(params):
    """Apply saved settings: restart the driver ONLY if it's running (Tk
    _sinden_apply — a stopped driver picks the saved config up on next Start)."""
    if _driver_running():
        _restart_driver()
        return {"message": "↻ restarting driver… (~3 s)", "restarted": True}
    return {"message": "✓ saved — driver not running (applies on next Start)",
            "restarted": False}


@method("sinden.smoother_set")
def _smoother_set(params):
    """Apply smoother values live (sinden-smoother-preset.sh SIGHUPs the daemon)."""
    alpha = float(params["alpha"])
    deadzone = float(params["deadzone"])
    snap = int(params["snap"])
    return {"message": _detached([HERE / "sinden-smoother-preset.sh",
                                  f"{alpha:.2f}", f"{deadzone:.1f}", str(snap)],
                                 "smoother-tune")}


@method("sinden.smoother_toggle")
def _smoother_toggle(params):
    """Flip the cursor smoother via the canonical toggle script (the
    .smoothing-off marker is its state; re-read sinden.status for truth)."""
    return {"message": _detached([SIN_TOOLS / "Toggle Cursor Smoother.sh"],
                                 "smoother-toggle")}


@method("sinden.led_set")
def _led_set(params):
    enabled = bool(params["enabled"])
    try:
        text = CONF.read_text()
    except OSError as ex:
        raise RpcError("EINTERNAL", f"sinden.conf unreadable: {ex}")
    new = re.sub(r'^\s*SINDEN_LED_ENABLED\s*=\s*\d+',
                 f'SINDEN_LED_ENABLED={1 if enabled else 0}', text, flags=re.M)
    if new == text:
        raise RpcError("EINVAL", "sinden.conf: SINDEN_LED_ENABLED line not found")
    fsutil.atomic_write(CONF, new)
    return {"message": f"TV LED strip {'ON' if enabled else 'OFF'} on driver start/stop"}


def _int_or(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@method("sinden.buttons", slow=True)
def _buttons(params):
    """The P1/P2 button-map page data (slow: pgrep for the live-dots note).
    Rows carry on/off/mod codes + labels; the picker groups and modifier table
    come straight from sinden_cfg. The C++ page lights the ● dots itself."""
    player = int(params.get("player", 1))
    rows = []
    for base in sinden_cfg.BUTTONS:
        on_key = sinden_cfg.key(base, player)
        off_key = sinden_cfg.key(base, player, offscreen=True)
        mod_key = sinden_cfg.key(base, player, mod=True)
        on_val = sinden_cfg.get(on_key)
        off_val = sinden_cfg.get(off_key)
        mod_val = _int_or(sinden_cfg.get(mod_key, "0"), 0)
        rows.append({"base": base, "label": sinden_cfg.BUTTON_LABELS[base],
                     "key": on_key, "code": _int_or(on_val, 0),
                     "code_label": sinden_cfg.label_for(on_val),
                     "off_key": off_key, "off_code": _int_or(off_val, 0),
                     "off_label": sinden_cfg.label_for(off_val),
                     "mod_key": mod_key, "mod": mod_val,
                     "mod_label": dict(sinden_cfg.MODIFIERS).get(mod_val, "None")})
    return {"player": player, "driver_running": _driver_running(), "rows": rows,
            "groups": [{"name": name,
                        "options": [{"value": v, "label": lbl} for v, lbl in opts]}
                       for name, opts in sinden_cfg.ACTION_GROUPS],
            "modifiers": [{"value": v, "label": lbl} for v, lbl in sinden_cfg.MODIFIERS]}


@method("sinden.set_keys")
def _set_keys(params):
    """Write config keys (button picks, modifiers, behavior knobs). backup_once
    + atomic set_many; unsafe keys (SerialPort*/JoystickMode*) are skipped by
    sinden_cfg itself."""
    pairs = params.get("pairs")
    if not isinstance(pairs, dict) or not pairs:
        raise RpcError("EINVAL", "pairs must be a non-empty object")
    sinden_cfg.backup_once()
    sinden_cfg.set_many({str(k): str(v) for k, v in pairs.items()})
    return {"message": "saved"}


@method("sinden.behavior")
def _behavior(params):
    """The P1/P2 recoil & behavior page values (Tk _gun_behavior_page reads)."""
    player = int(params.get("player", 1))
    sfx = "P2" if player == 2 else ""

    def geti(base, default):
        return _int_or(sinden_cfg.get(base + sfx), default)

    handed = sinden_cfg.get("GangstaSetting" + sfx) or "2"
    return {"player": player,
            "recoil": sinden_cfg.get("EnableRecoil" + sfx) == "1",
            "strength": geti("RecoilStrength", 100),
            "auto_recoil": sinden_cfg.get("TriggerRecoilNormalOrRepeat" + sfx) == "1",
            "auto_strength": geti("AutoRecoilStrength", 40),
            "auto_speed": geti("AutoRecoilDelayBetweenPulses", 13),
            "handedness": handed,
            "handedness_label": {"0": "Off", "1": "Left-handed",
                                 "2": "Right-handed"}.get(handed, "?"),
            "offscreen_reload": sinden_cfg.get("OffscreenReload" + sfx) == "1",
            "suffix": sfx}


# ── camera tuning (live preview) ──

def _cam_seed_vals() -> None:
    """Seed per-player slider values from the config (Tk _camera_tune_page)."""
    for p in (1, 2):
        sfx = "P2" if p == 2 else ""

        def geti(base, default, sfx=sfx):
            return _int_or(sinden_cfg.get(base + sfx), default)

        _cam["vals"][p] = {
            "Brightness": geti("CameraBrightness", 100),
            "Contrast": geti("CameraContrast", 50),
            "auto": (sinden_cfg.get("CameraExposureAuto" + sfx) or "1") == "3",
            "Exposure": geti("CameraExposure", 80),
        }


def _cam_apply_live(player: int) -> None:
    dev, v = sinden_cfg.CAM[player], _cam["vals"][player]
    sinden_cfg.set_ctrl(dev, "brightness", v["Brightness"])
    sinden_cfg.set_ctrl(dev, "contrast", v["Contrast"])
    sinden_cfg.set_ctrl(dev, "auto_exposure", 3 if v["auto"] else 1)
    if not v["auto"]:
        sinden_cfg.set_ctrl(dev, "exposure_time_absolute", v["Exposure"])


def _restore_driver_state() -> str:
    """Leaving the preview: restore the PRE-preview state (Tk _cam_restore_driver).
    Driver WAS running → restart (guns + LED back). Was NOT running → leave it
    off and force the border LED OFF so tuning never leaves it stuck on."""
    if not _drv["paused"]:
        return ""
    if _drv["was_running"]:
        _restart_driver()
        return "↻ restarting driver… (~3 s)"
    _drv["paused"] = False
    _led_webhook("stop")
    return "Preview stopped — driver left off, LED off."


class CameraPreviewStream(Stream):
    """ffmpeg -update 1 → /tmp/mad-cam.ppm; the panel polls the file itself
    (no frame events — same poll model as the Tk 66 ms tick). cleanup kills
    ffmpeg and restores the driver/LED on EVERY exit path incl. daemon
    teardown."""

    def __init__(self, player: int):
        super().__init__()
        self.player = player
        self.proc = None

    def run(self):
        # Pause the driver so the camera is free (guns dead — by design). On a
        # gun SWITCH the pause is handed over: _drv stays paused and
        # was_running keeps the ORIGINAL pre-tuning value (Tk parity — the
        # driver must only be restored on the real exit).
        if not _drv["paused"]:
            _drv["was_running"] = _driver_running()
            try:
                subprocess.run([str(HERE / "sinden-stop.sh")], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=15)
            except Exception:
                pass
            _drv["paused"] = True
        _cam_apply_live(self.player)  # First frame already reflects the sliders.
        try:
            CAM_TMP.unlink()
        except OSError:
            pass
        LOGDIR.mkdir(parents=True, exist_ok=True)
        try:
            self.proc = subprocess.Popen(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2",
                 "-input_format", "mjpeg", "-video_size", "640x480",
                 "-i", sinden_cfg.CAM[self.player],
                 # The Sinden camera is mounted upside-down (the Mono driver
                 # compensates internally for tracking) — flip the raw feed so
                 # the preview reads right-side up.
                 "-vf", "vflip",
                 "-pix_fmt", "rgb24", "-f", "image2", "-update", "1", "-y",
                 str(CAM_TMP)],
                stdout=subprocess.DEVNULL,
                stderr=open(LOGDIR / "sinden-preview.log", "ab"),
                start_new_session=True)
        except Exception as ex:
            self.emit({"error": f"ffmpeg failed: {ex}"})
            return
        self.emit({"ready": True, "path": str(CAM_TMP)})
        # Border LED on, DELAYED so it reliably beats sinden-stop.sh's
        # backgrounded LED-off webhook (the Tk 700 ms fix). Skipped if the
        # preview is stopped within the delay.
        if not self.stopped.wait(0.7):
            _led_webhook("start")
        while not self.stopped.wait(0.5):
            if self.proc.poll() is not None:
                self.emit({"error": "ffmpeg exited — see control-panel/"
                                    "sinden-preview.log"})
                break

    def cleanup(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        # Only the CURRENT stream owns the registration and the paused driver.
        # A superseded stream (gun switch handed the pause to its successor)
        # must neither restore the driver nor clobber the new bookkeeping.
        with _CAM_LOCK:
            current = _cam["stream"] == self.token
            if current:
                _cam["player"] = None
                _cam["stream"] = None
        if current:
            message = _restore_driver_state()
            if message:
                self.emit({"status": message})


@method("camera.get")
def _camera_get(params):
    _cam_seed_vals()
    return {"cams": {str(p): d for p, d in sinden_cfg.CAM.items()},
            "vals": {str(p): v for p, v in _cam["vals"].items()}}


@method("camera.preview", slow=True)
def _camera_preview(params):
    """Start (or toggle off) the live preview for one gun. Returns the stream
    token + the frame file the panel polls."""
    player = int(params["player"])
    if player not in (1, 2):
        raise RpcError("EINVAL", "player must be 1 or 2")
    with _CAM_LOCK:
        if _cam["stream"] is not None and _cam["player"] == player:
            stop_stream(_cam["stream"])  # Second press on the live gun → stop.
            return {"stopped": True}
        if not _cam["vals"]:
            _cam_seed_vals()
        # Gun switch: register the successor FIRST, then stop the old stream —
        # its cleanup sees it is no longer current and only kills its ffmpeg
        # (the driver stays paused, was_running keeps the pre-tuning truth;
        # P1/P2 are different /dev/video nodes so there is no device handover).
        old = _cam["stream"]
        stream = CameraPreviewStream(player)
        _cam["player"] = player
        _cam["stream"] = stream.token
        if old is not None:
            stop_stream(old)
    token = stream.start()
    return {"stream": token, "path": str(CAM_TMP)}


@method("camera.preview_stop")
def _camera_preview_stop(params):
    with _CAM_LOCK:
        token = _cam["stream"]
    if token is not None:
        stop_stream(token)
        return {"stopped": True}
    return {"stopped": False}


@method("camera.set")
def _camera_set(params):
    """Slider move: remember + apply live iff previewing that gun (Tk _cam_set)."""
    player = int(params["player"])
    ctrl = params["ctrl"]
    value = params["value"]
    if not _cam["vals"]:
        _cam_seed_vals()
    if ctrl not in ("Brightness", "Contrast", "auto", "Exposure"):
        raise RpcError("EINVAL", f"unknown ctrl {ctrl!r}")
    _cam["vals"][player][ctrl] = bool(value) if ctrl == "auto" else int(value)
    if _cam["player"] == player:
        dev = sinden_cfg.CAM[player]
        vals = _cam["vals"][player]
        if ctrl == "auto":
            sinden_cfg.set_ctrl(dev, "auto_exposure", 3 if vals["auto"] else 1)
            if not vals["auto"]:
                sinden_cfg.set_ctrl(dev, "exposure_time_absolute", vals["Exposure"])
        elif ctrl == "Exposure":
            if not vals["auto"]:
                sinden_cfg.set_ctrl(dev, "exposure_time_absolute", vals["Exposure"])
        else:
            sinden_cfg.set_ctrl(dev, sinden_cfg.CAM_CTRL[ctrl], vals[ctrl])
    return {}


@method("camera.save", slow=True)
def _camera_save(params):
    """Persist the slider values to the config and restore the driver to its
    pre-tuning state (Tk _cam_save — never force-start a driver that was off)."""
    if not _cam["vals"]:
        _cam_seed_vals()
    sinden_cfg.backup_once()
    pairs = {}
    for p in (1, 2):
        sfx = "P2" if p == 2 else ""
        v = _cam["vals"][p]
        pairs[f"CameraBrightness{sfx}"] = v["Brightness"]
        pairs[f"CameraContrast{sfx}"] = v["Contrast"]
        pairs[f"CameraExposureAuto{sfx}"] = 3 if v["auto"] else 1
        pairs[f"CameraExposure{sfx}"] = "" if v["auto"] else v["Exposure"]
    sinden_cfg.set_many({k: str(v) for k, v in pairs.items()})
    with _CAM_LOCK:
        token = _cam["stream"]
    if token is not None:
        stop_stream(token)  # cleanup() restores the driver/LED state.
        return {"message": "Saved camera settings."}
    if _drv["paused"]:
        message = _restore_driver_state()
        return {"message": "Saved camera settings. " + message}
    if _driver_running():
        _restart_driver()
        return {"message": "Saved camera settings. ↻ restarting driver… (~3 s)"}
    return {"message": "Saved camera settings — driver not running "
                       "(applies on next Start)"}
