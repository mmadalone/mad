#!/usr/bin/env python3
"""Display a fullscreen launching.png.

Modes:
  show-launchscreen.py <img> [seconds]
        Show for `seconds` (default 2), then exit. (Legacy/synchronous use.)

  show-launchscreen.py <img> [max_seconds] --hold
        Show and KEEP showing until the game window takes focus (FocusOut), or
        `max_seconds` elapses as a safety (default 60). Used as a non-blocking
        ES-DE game-start splash that bridges the black gap while a slow emulator
        (Proton/OpenBOR, Eden) loads. The game-end hook also kills it, and under
        gamescope the game window covers it regardless — FocusOut just lets it
        close cleanly so it doesn't flash after the game exits.

If the SPLASH_READY env var is set, the file at that path is created once the
splash window has actually been drawn — the game-start hook waits for it so the
splash is guaranteed on screen BEFORE ES-DE launches the emulator (otherwise a
fast emulator could map its window first and the splash would cover it).

Pre-scales the source with ffmpeg (tkinter PhotoImage only integer-scales), and
CACHES the scaled result under storage/launchscreens keyed by source path +
mtime + size + WxH: the source image and screen size are identical launch after
launch, so re-running ffmpeg every time was ~150 ms of the blocking SPLASH_READY
wait for a byte-identical output (AUDIT-2026-08-12 PERFORMANCE-4). Docked and
handheld resolutions cache side by side; a theme update (new mtime) re-scales
and prunes the stale variant; ANY cache trouble falls back to the old
scale-into-a-tempfile path, so a broken cache can never lose the splash. The
whole cache dir is disposable.

Usage: show-launchscreen.py <image-path> [seconds] [--hold]
"""
import hashlib
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _cache_dir() -> Path:
    """storage/launchscreens via lib.mad_paths (honors $MAD_DATA_ROOT). Raises
    if the lib is unavailable — prepare_image() then falls back to a tempfile."""
    sys.path.insert(0, str(HERE))
    from lib import mad_paths
    return mad_paths.storage("launchscreens")


def _ffmpeg_scale(src: str, sw: int, sh: int, dest: str) -> None:
    """Scale src to sw x sh preserving aspect ratio with black padding."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
            "-vf",
            f"scale={sw}:{sh}:force_original_aspect_ratio=decrease,"
            f"pad={sw}:{sh}:(ow-iw)/2:(oh-ih)/2:color=black",
            "-frames:v", "1", dest,
        ],
        check=True,
    )


def prepare_image(src: str, sw: int, sh: int) -> tuple[str, bool]:
    """Return (scaled_png_path, is_temporary).

    Cache hit: the stable path, nothing runs. Miss: ffmpeg into a dotfile in the
    cache dir, atomic os.replace to the keyed name, then prune stale variants of
    the SAME source at the SAME resolution (older mtime/size = theme update);
    other resolutions (docked vs handheld) are left alone. Any cache failure
    (unreadable dir, missing lib, ...) falls back to the legacy
    scale-into-a-tempfile path, which the caller deletes on exit."""
    try:
        real = os.path.realpath(src)
        st = os.stat(real)
        key = hashlib.sha1(real.encode()).hexdigest()[:16]
        name = f"{key}-{st.st_mtime_ns}-{st.st_size}-{sw}x{sh}.png"
        cdir = _cache_dir()
        cached = cdir / name
        # A hit must be a PLAUSIBLE png: a hard power-off in ext4's writeback
        # window after a first-time install can leave a zero-length file under a
        # still-valid key, which would otherwise poison this splash forever
        # (main() also unlinks-and-retries if Tk rejects the cached bytes).
        if cached.is_file() and cached.stat().st_size > 0:
            return str(cached), False
        cdir.mkdir(parents=True, exist_ok=True)
        # Sweep orphaned dotfile tmps (a splash killed mid-scale leaks one here,
        # and the SIGTERM handler is not installed yet at that point); age-gated
        # so a concurrent scale's live tmp is never touched.
        now = time.time()
        for orphan in cdir.glob(".*.png"):
            try:
                if now - orphan.stat().st_mtime > 3600:
                    orphan.unlink()
            except OSError:
                pass
        # dotfile (invisible to the prune glob) but .png-suffixed: ffmpeg picks
        # its output muxer from the extension and errors on a bare .tmpNNN name
        tmp = cdir / f".{name}.{os.getpid()}.png"
        try:
            _ffmpeg_scale(src, sw, sh, str(tmp))
            os.replace(tmp, cached)
        finally:
            tmp.unlink(missing_ok=True)
        for stale in cdir.glob(f"{key}-*-{sw}x{sh}.png"):
            if stale.name != name:
                stale.unlink(missing_ok=True)
        return str(cached), False
    except Exception:
        tmp_name = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
        try:
            _ffmpeg_scale(src, sw, sh, tmp_name)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return tmp_name, True


def main() -> None:
    import tkinter as tk
    from tkinter import PhotoImage

    argv = sys.argv[1:]
    hold = "--hold" in argv
    args = [a for a in argv if a != "--hold"]
    if not args or not os.path.isfile(args[0]):
        sys.exit(0)

    src = args[0]
    secs = float(args[1]) if len(args) > 1 else (60.0 if hold else 2.0)
    ready_path = os.environ.get("SPLASH_READY")

    # ONE Tk root: it reads the screen size (a screen property — same answer the
    # old throwaway probe window gave) and later shows the image. The window maps
    # only at the first update() below, after the image is placed, so nothing
    # flashes while ffmpeg runs on a cache miss.
    root = tk.Tk()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()

    scaled, scaled_is_temp = prepare_image(src, sw, sh)

    def cleanup():
        if scaled_is_temp:
            try:
                os.unlink(scaled)
            except OSError:
                pass

    try:
        root.attributes("-fullscreen", True)
        root.configure(background="black", cursor="none")
        try:
            img = PhotoImage(file=scaled)
        except Exception:
            # A corrupt CACHED entry (e.g. truncated by a power cut) must cost
            # one launch, not every launch: drop it and rebuild once. A broken
            # temp scale keeps the old behavior (no splash this launch).
            if scaled_is_temp:
                raise
            try:
                os.unlink(scaled)
            except OSError:
                pass
            scaled, scaled_is_temp = prepare_image(src, sw, sh)
            img = PhotoImage(file=scaled)
        tk.Label(root, image=img, bg="black", borderwidth=0).place(
            relx=0.5, rely=0.5, anchor="center"
        )

        def bye(*_):
            try:
                root.destroy()
            except tk.TclError:
                pass

        # SIGTERM (the game-end hook) closes us cleanly; keep the interpreter ticking
        # so the signal is serviced promptly from inside Tk's C mainloop.
        signal.signal(signal.SIGTERM, lambda *_: bye())

        def tick():
            root.after(250, tick)

        tick()

        # Force the window to map and draw, then signal readiness so the hook can let
        # ES-DE launch the emulator knowing the splash is already on screen.
        root.update_idletasks()
        root.update()
        if ready_path:
            try:
                open(ready_path, "w").close()
            except OSError:
                pass

        # Safety auto-close (both modes).
        root.after(int(secs * 1000), bye)

        if hold:
            # Keep the splash up until the game window covers it. gamescope shows the
            # splash as long as it EXISTS (newest window), so we must NOT destroy it
            # early: Proton/OpenBOR churn focus during startup BEFORE the real game
            # window appears, so closing on that transient FocusOut is exactly what
            # reopened the black gap. The game window covers us when it maps, and the
            # game-end hook kills us (with the safety timeout as a last resort).
            root.focus_force()

        root.mainloop()
    finally:
        cleanup()


if __name__ == "__main__":
    main()
