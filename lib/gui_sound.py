"""
ES-DE navigation sound effects for the router-config GUI.

Plays the same .wav set ES-DE uses for menu navigation so the GUI feels native.
Sound files are resolved in priority order:
  1. the ACTIVE ES-DE theme's `_inc/sounds/` (so it matches whatever theme is on),
  2. any installed theme that ships a `_inc/sounds/` set,
  3. a bundled fallback copied into `launchers/data/sounds/` (self-contained).

Playback is fire-and-forget via `pw-play`/`paplay` (PipeWire/Pulse, both present
on SteamOS, no pip), debounced so a fast D-pad scroll doesn't machine-gun. Honors
ES-DE's NavigationSounds setting and a GUI mute toggle. Best-effort throughout:
if nothing can play, the GUI is silent but fully functional.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from . import esde_settings

HERE = Path(__file__).resolve().parent          # .../launchers/lib
BUNDLED = HERE.parent / "data" / "sounds"        # .../launchers/data/sounds

# Logical GUI event -> ES-DE sound filename. ES-DE uses scroll/select/back/launch
# (+ systembrowse/quicksysselect/favorite); we map our events onto that set.
EVENT_FILE = {
    "nav": "scroll.wav",       # focus moved
    "select": "select.wav",    # A / activate
    "back": "back.wav",        # B / escape
    "launch": "launch.wav",    # entered a section / committed an action
}
_NAV_DEBOUNCE = 0.11           # s — collapse rapid scroll ticks


def _player_cmd(path: Path, volume: float) -> list[str] | None:
    """A non-blocking play command for `path` at `volume` (0..1), or None."""
    if shutil.which("pw-play"):
        return ["pw-play", "--volume", f"{volume:.2f}", str(path)]
    if shutil.which("paplay"):
        # paplay volume is 0..65536 (65536 = 100%).
        return ["paplay", f"--volume={int(max(0.0, min(1.0, volume)) * 65536)}", str(path)]
    if shutil.which("aplay"):
        return ["aplay", "-q", str(path)]   # no volume control; last resort
    return None


def _find_sound_dir() -> Path | None:
    """Active theme's sounds → any theme's sounds → bundled fallback."""
    active = esde_settings.active_theme_dir()
    if active:
        d = active / "_inc" / "sounds"
        if d.is_dir() and any(d.glob("*.wav")):
            return d
    for base in esde_settings.themes_dirs():
        for d in sorted(base.glob("*/_inc/sounds")):
            if d.is_dir() and any(d.glob("*.wav")):
                return d
    if BUNDLED.is_dir() and any(BUNDLED.glob("*.wav")):
        return BUNDLED
    return None


class Sound:
    def __init__(self, muted: bool = False):
        s = esde_settings.read()
        # Off if the user disabled ES-DE nav sounds or muted the GUI, or if no
        # sound dir / player is available.
        self.sound_dir = _find_sound_dir()
        self.volume = max(0.0, min(1.0, s.get("nav_volume", 80) / 100.0))
        self._esde_on = bool(s.get("nav_sounds", True))
        self.muted = muted
        self._last_nav = 0.0
        self._files = {}
        if self.sound_dir:
            for ev, fn in EVENT_FILE.items():
                p = self.sound_dir / fn
                if p.is_file():
                    self._files[ev] = p

    @property
    def enabled(self) -> bool:
        return self._esde_on and not self.muted and bool(self._files)

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)

    def play(self, event: str) -> None:
        if not self.enabled:
            return
        path = self._files.get(event)
        if not path:
            return
        if event == "nav":
            now = time.monotonic()
            if now - self._last_nav < _NAV_DEBOUNCE:
                return
            self._last_nav = now
        cmd = _player_cmd(path, self.volume)
        if not cmd:
            return
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError:
            pass


if __name__ == "__main__":
    snd = Sound()
    print("sound_dir:", snd.sound_dir)
    print("files:", {k: str(v) for k, v in snd._files.items()})
    print("enabled:", snd.enabled, "volume:", snd.volume)
    import sys
    if "--play" in sys.argv:
        for ev in ("nav", "select", "back"):
            print("playing", ev); snd.play(ev); time.sleep(0.6)
