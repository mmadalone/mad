"""
Fullscreen blocking Proceed / Cancel dialog for the controller router.

Mirrors `show-launchscreen.py`'s window setup (fullscreen, black bg, no cursor)
but instead of a timed splash this one blocks until the user picks Proceed
(exit 0) or Cancel (exit 1). The controller-router-wrap.sh wrapper translates
a non-zero exit into a failed launch, which ES-DE displays as a generic error.

Keybinds (keyboard):
    Return / Enter        → Proceed
    a / A                 → Proceed   (some pads map BTN_A to KEY_A)
    z                     → Proceed   (Sinden P1 FrontRight side button)
    Space                 → Proceed
    Escape                → Cancel
    b / B                 → Cancel    (some pads map BTN_B to KEY_B)
    x                     → Cancel    (Sinden P1 RearRight side button)
    q / Q                 → Cancel

Joypad (evdev — covers the X-Arcade in Xbox mode and any real gamepad, whose
button presses tkinter NEVER sees as key events — this is why the dialog was
un-dismissable with only the stick connected, observed 2026-05-29 test 2):
    B (BTN_EAST) / Select → Cancel
    any other pad button  → Proceed

The z / x bindings are deliberate: when no gamepad is plugged in (the very
case we're warning about) the user might still have a Sinden in hand, and
Sinden side buttons emit those keys per the existing keymap in retroarch.cfg.

Usage:
    python3 -m lib.warning_dialog "Title" "Body text\\nmore lines OK"
    echo $?       # 0=Proceed, 1=Cancel
"""
from __future__ import annotations

import os
import sys

try:
    import tkinter as tk
except Exception:                 # tk can be wiped by a SteamOS update; don't crash with a
    tk = None                     # Python-default exit 1 (looks like Cancel) — main() returns 3

try:
    import evdev
    from evdev import ecodes
except Exception:                      # evdev missing → keyboard-only fallback
    evdev = None
    ecodes = None


PROCEED_EXIT = 0
CANCEL_EXIT = 1

# Auto-Proceed if the user doesn't choose within this many seconds. A hard
# guarantee the dialog can NEVER trap a launch (it did, 2026-05-29 test 2).
AUTO_PROCEED_SECONDS = 30

# Gamepad button code range (BTN_GAMEPAD/BTN_SOUTH=0x130 .. BTN_THUMBR=0x13f).
_BTN_LO, _BTN_HI = 0x130, 0x13f


def _open_joypads() -> list:
    """Open every joypad-like evdev device (has gamepad buttons) non-blocking.
    Includes the X-Arcade's two Xbox-mode interfaces and any real pad; Sinden
    side buttons already come through as keys, but the gun also has BTN_* so it
    works here too. Returns [] if evdev is unavailable."""
    pads = []
    if evdev is None:
        return pads
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
            keys = d.capabilities().get(ecodes.EV_KEY, [])
            if any(_BTN_LO <= k <= _BTN_HI for k in keys):
                os.set_blocking(d.fd, False)
                pads.append(d)
        except Exception:
            pass
    return pads


def show_warning(title: str, body: str) -> int:
    """Show the dialog and return an exit code (0=Proceed, 1=Cancel)."""
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.configure(background="black", cursor="none")

    bg = tk.Frame(root, bg="black")
    bg.place(relx=0, rely=0, relwidth=1, relheight=1)

    frame = tk.Frame(bg, bg="black")
    frame.place(relx=0.5, rely=0.5, anchor="center")

    tk.Label(
        frame, text=title,
        font=("DejaVu Sans", 36, "bold"),
        fg="#ffcc00",                       # warm yellow = warning
        bg="black", justify="center",
    ).pack(pady=(0, 24))

    tk.Label(
        frame, text=body,
        font=("DejaVu Sans", 18),
        fg="white", bg="black", justify="center",
        wraplength=int(root.winfo_screenwidth() * 0.7),
    ).pack(pady=(0, 36))

    state = {"code": CANCEL_EXIT, "done": False}

    def _proceed(_e=None):
        state["code"] = PROCEED_EXIT
        state["done"] = True
        root.destroy()

    def _cancel(_e=None):
        state["code"] = CANCEL_EXIT
        state["done"] = True
        root.destroy()

    btnrow = tk.Frame(frame, bg="black")
    btnrow.pack()

    tk.Button(
        btnrow, text="Proceed   (A · Enter · Z)",
        command=_proceed, width=24, height=2,
        font=("DejaVu Sans", 16, "bold"),
        bg="#1a8c1a", fg="white",
        activebackground="#22b522", activeforeground="white",
        relief="flat", borderwidth=0,
    ).pack(side="left", padx=(0, 24))

    tk.Button(
        btnrow, text="Cancel   (B · Esc · X)",
        command=_cancel, width=24, height=2,
        font=("DejaVu Sans", 16, "bold"),
        bg="#8c1a1a", fg="white",
        activebackground="#b52222", activeforeground="white",
        relief="flat", borderwidth=0,
    ).pack(side="left")

    countdown = tk.Label(
        frame, text="",
        font=("DejaVu Sans", 14),
        fg="#888888", bg="black", justify="center",
    )
    countdown.pack(pady=(28, 0))

    for key in ("Return", "space", "a", "A", "z", "Z"):
        root.bind(f"<KeyPress-{key}>", _proceed)
    for key in ("Escape", "b", "B", "x", "X", "q", "Q"):
        root.bind(f"<KeyPress-{key}>", _cancel)

    # ── joypad polling (X-Arcade / any real pad) ──
    pads = _open_joypads()
    cancel_btns = {ecodes.BTN_EAST, ecodes.BTN_SELECT} if ecodes else set()

    def _poll_pads():
        if state["done"]:
            return
        for d in pads:
            try:
                ev = d.read_one()
                while ev is not None:
                    if ev.type == ecodes.EV_KEY and ev.value == 1 \
                            and _BTN_LO <= ev.code <= _BTN_HI:
                        if ev.code in cancel_btns:
                            _cancel()
                        else:
                            _proceed()
                        return
                    ev = d.read_one()
            except OSError:
                pass               # device went away mid-poll; ignore
        if not state["done"]:
            root.after(40, _poll_pads)

    # Drain events buffered before the dialog opened, so a press from a moment
    # ago doesn't instantly dismiss it.
    for d in pads:
        try:
            while d.read_one() is not None:
                pass
        except OSError:
            pass

    if pads:
        root.after(40, _poll_pads)

    # ── auto-proceed safety timeout ──
    remaining = {"s": AUTO_PROCEED_SECONDS}

    def _tick():
        if state["done"]:
            return
        if remaining["s"] <= 0:
            _proceed()
            return
        countdown.config(text=f"Auto-proceeding in {remaining['s']}s…")
        remaining["s"] -= 1
        root.after(1000, _tick)

    _tick()

    root.focus_force()
    root.mainloop()
    return state["code"]


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(f"usage: {argv[0]} <title> <body>", file=sys.stderr)
        return 2
    if tk is None:
        print("warning_dialog: tkinter unavailable; cannot display", file=sys.stderr)
        return 3                               # 10.3: couldn't display != Cancel
    try:
        return show_warning(argv[1], argv[2])
    except Exception as e:                      # no display / display died / render crash
        print(f"warning_dialog: could not display ({e!r})", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
