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

Pre-scales the source with ffmpeg (tkinter PhotoImage only integer-scales).

Usage: show-launchscreen.py <image-path> [seconds] [--hold]
"""
import os
import signal
import subprocess
import sys
import tempfile
import time
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

# Probe screen size with a throwaway hidden window.
probe = tk.Tk()
probe.withdraw()
sw = probe.winfo_screenwidth()
sh = probe.winfo_screenheight()
probe.destroy()

# Scale source PNG to screen size, preserving aspect ratio with black padding.
scaled = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name


def cleanup():
    try:
        os.unlink(scaled)
    except OSError:
        pass


try:
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
            "-vf",
            f"scale={sw}:{sh}:force_original_aspect_ratio=decrease,"
            f"pad={sw}:{sh}:(ow-iw)/2:(oh-ih)/2:color=black",
            "-frames:v", "1", scaled,
        ],
        check=True,
    )

    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.configure(background="black", cursor="none")
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
