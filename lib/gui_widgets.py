"""
Themed, controller-friendly widgets for the router-config GUI.

Every control is a focusable `tk.Button` (so the existing GamepadNav focus model
just works), styled from a `Style` (palette + font from gui_theme) and wired to
play ES-DE sounds. Per the design there is NO free-text entry — editable values
are finite sets: toggles (bool / membership), steppers (ints + hold time), and
pickers (single-choice; the selection *page* lives in the App, this module only
makes the control that opens it).

All builders return the primary focusable widget so callers can `.focus_set()`.
"""
from __future__ import annotations

import tkinter as tk


class Style:
    """Palette + font + sound, shared by all widgets. Built by the App from a
    gui_theme.Theme and a gui_sound.Sound."""

    def __init__(self, theme, sound):
        self.c = theme.colors
        self.font = theme.font          # callable(size, bold=, mono=)
        self.sound = sound

    def play(self, event):
        if self.sound:
            self.sound.play(event)


def _attach_focus_ring(b, c):
    """Strong 10-foot focus feedback: on focus the row inverts to the theme's
    selection colours (selectBg/selectFg, falling back to accent) with a thick
    border; on blur it returns to the resting row colours. Stored so we can
    restore exact resting colours regardless of per-widget overrides."""
    rest_bg = b.cget("bg")
    rest_fg = b.cget("fg")
    sel_bg = c.get("selectBg", c["accent"])
    sel_fg = c.get("selectFg", "#000000")
    ring = c.get("selectorColor", c["accent"])

    def on_focus(_e=None):
        # IDEMPOTENT: a real FocusIn and a cursor-generated one can BOTH fire — the
        # second must not re-capture the already-selected colours as "resting"
        # (that left a trail of stuck-selected tiles).
        if not getattr(b, "_mad_painted", False):
            b._mad_rest = (b.cget("bg"), b.cget("fg"))   # capture live resting colours
            b._mad_painted = True
        b.config(bg=sel_bg, fg=sel_fg, highlightthickness=3,
                 highlightbackground=ring, highlightcolor=ring)

    def on_blur(_e=None):
        # Under gamescope the WINDOW loses X focus every few seconds (focus churn) and
        # Tk fires FocusOut at the focused widget — but the nav cursor is still HERE.
        # Keep the selected look while the widget is the cursor (_mad_selected, set by
        # GamepadNav._set_cursor); only a real cursor move unpaints.
        if getattr(b, "_mad_selected", False):
            return
        b._mad_painted = False
        rb, rf = getattr(b, "_mad_rest", (rest_bg, rest_fg))
        b.config(bg=rb, fg=rf, highlightthickness=2,
                 highlightbackground=c["border"], highlightcolor=ring)

    b.bind("<FocusIn>", on_focus, add="+")
    b.bind("<FocusOut>", on_blur, add="+")


def button(parent, style: Style, text, cmd, *, sound_event="select",
           width=None, size=15, anchor="center", hmove=None, image=None,
           compound="left", wraplength=720, **kw):
    """A focusable, themed button that sizes to its CONTENT (no fixed character
    width → no clipping at large fonts, no dead space at small ones); long text
    WRAPS at `wraplength` px instead of clipping; text is centred. `width` is
    accepted but ignored (legacy callers). `hmove`=callable(direction ±1) drives
    Left/Right within the control. GamepadNav reads `widget._mad_hmove`."""
    c = style.c

    def run():
        style.play(sound_event)
        if cmd:
            cmd()

    b = tk.Button(parent, text=text, command=run, bg=c["row"], fg=c["text"],
                  activebackground=c.get("selectBg", c["accent"]),
                  activeforeground=c.get("selectFg", "#000000"),
                  highlightthickness=2, highlightbackground=c["border"],
                  highlightcolor=c.get("selectorColor", c["accent"]), relief="flat",
                  bd=0, padx=14, pady=8, font=style.font(size), anchor=anchor,
                  justify="center", wraplength=wraplength, **kw)
    if image is not None:
        b.config(image=image, compound=compound, padx=10)
    _attach_focus_ring(b, c)
    if hmove is not None:
        b._mad_hmove = hmove
    return b


def toggle(parent, style: Style, label, value, on_change, *, width=16, size=14):
    """A focusable SLIDE SWITCH — label + pill/knob drawn on a Canvas. A (or
    Left/Right, or a click) flips it. Reads as one control for the gamepad nav via
    `_mad_focusable` / `_mad_activate` / `_mad_hmove`. Sizes to its content."""
    import tkinter.font as tkfont
    c = style.c
    f = style.font(size)
    fnt = tkfont.Font(font=f)
    st = {"v": bool(value)}
    pill_w, pill_h = int(size * 2.6), int(size * 1.5)
    pad, gap = 12, 14
    W = pad + fnt.measure(label) + gap + pill_w + pad
    H = max(pill_h, fnt.metrics("linespace")) + 12
    try:
        pbg = parent["bg"]
    except Exception:
        pbg = c["bg"]
    cv = tk.Canvas(parent, width=W, height=H, bg=pbg, highlightthickness=2, bd=0,
                   highlightbackground=c["border"],
                   highlightcolor=c.get("selectorColor", c["accent"]), takefocus=1)

    def redraw():
        cv.delete("all")
        cy, on = H // 2, st["v"]
        cv.create_text(pad, cy, text=label, anchor="w", fill=c["text"], font=f)
        px = W - pad - pill_w
        r = pill_h // 2
        track = c.get("selectBg", c["accent"]) if on else c["border"]
        cv.create_oval(px, cy - r, px + pill_h, cy + r, fill=track, outline=track)
        cv.create_oval(px + pill_w - pill_h, cy - r, px + pill_w, cy + r, fill=track, outline=track)
        cv.create_rectangle(px + r, cy - r, px + pill_w - r, cy + r, fill=track, outline=track)
        kr = r - 3
        kx = (px + pill_w - r) if on else (px + r)
        cv.create_oval(kx - kr, cy - kr, kx + kr, cy + kr, outline="",
                       fill=(c.get("selectFg", "#ffffff") if on else c["text_dim"]))

    def flip(*_):
        old = st["v"]
        st["v"] = not old; style.play("nav"); redraw()
        try:
            on_change(st["v"])
        except Exception:
            st["v"] = old; redraw()          # keep the pill in sync if the write failed
            raise

    def set_dir(d):
        if d > 0 and not st["v"]:             # Right = on, Left = off (explicit)
            flip()
        elif d < 0 and st["v"]:
            flip()

    cv._mad_focusable = True
    cv._mad_activate = flip
    cv._mad_hmove = set_dir
    cv.bind("<FocusIn>", lambda _e: cv.config(highlightthickness=3,
            highlightbackground=c.get("selectorColor", c["accent"])), add="+")
    cv.bind("<FocusOut>", lambda _e: getattr(cv, "_mad_selected", False) or cv.config(
            highlightthickness=2, highlightbackground=c["border"]), add="+")
    cv.bind("<Button-1>", flip)
    redraw()
    return cv


def stepper(parent, style: Style, label, value, *, lo, hi, step, on_change,
            fmt=str, size=14):
    """`label   ‹  value  ›` — D-pad-friendly value picker. Left/− and right/+
    are buttons (also reachable as separate focus stops); the value label updates
    in place and `on_change(new)` fires. Works for ints and floats (via `step`)."""
    c = style.c
    state = {"v": value}
    row = tk.Frame(parent, bg=c["surface"]);
    tk.Label(row, text=f"  {label}", bg=c["surface"], fg=c["text"],
             font=style.font(size), anchor="w", width=24).pack(side="left", padx=6)
    val = tk.Label(row, text=f"{fmt(state['v'])}", bg=c["surface"], fg=c["accent"],
                   font=style.font(size + 1, bold=True), width=8)

    def bump(d):
        nv = round(state["v"] + d * step, 4)
        if isinstance(value, int) and float(nv).is_integer():
            nv = int(nv)
        nv = max(lo, min(hi, nv))
        if isinstance(value, int) and not isinstance(nv, int):
            nv = int(round(nv))              # int field never hands on_change a float
        if nv != state["v"]:
            old = state["v"]
            state["v"] = nv
            val.config(text=f"{fmt(nv)}")
            try:
                on_change(nv)
            except Exception:
                state["v"] = old                 # roll back UI to match persisted state
                val.config(text=f"{fmt(old)}")
                raise

    # Left/Right bumps the value from EITHER arrow (act-within-control), so you
    # never have to hop between the ‹ and › focus stops to change it.
    minus = button(row, style, "‹", lambda: bump(-1), sound_event="nav", width=3,
                   size=size, hmove=bump)
    minus.pack(side="left", padx=2)
    val.pack(side="left", padx=8)
    plus = button(row, style, "›", lambda: bump(1), sound_event="nav", width=3,
                  size=size, hmove=bump)
    plus.pack(side="left", padx=2)

    def _set_value(nv):
        """Set the displayed value programmatically (e.g. a preset updates the slider)
        WITHOUT firing on_change — the caller has already applied the change itself."""
        nv = max(lo, min(hi, round(nv, 4)))
        if isinstance(value, int) and float(nv).is_integer():
            nv = int(nv)
        state["v"] = nv
        val.config(text=f"{fmt(nv)}")
    minus._mad_set = _set_value          # callers: stepper(...)._mad_set(new_value)
    # Up/Down nav: report the WHOLE row as ‹'s nav rect so a stepper x-overlaps the left-anchored
    # buttons/toggles in the same column (App._rect honours _mad_navrect) — else Up/Down would
    # treat steppers as a separate right-hand column and skip them. Only ‹ gets the row rect: › KEEPS
    # its own (right-edge) rect so Right from ‹ can still reach › (its dx must stay > 0) — otherwise
    # both arrows share one rect, › becomes unreachable, and the value can only ever be DECREASED.
    minus._mad_navrect = row
    row.pack(fill="x", pady=3)
    return minus


def class_toggle_row(parent, style: Style, title, classes, selected, labels,
                     on_change, *, size=13):
    """A labelled row of on/off toggles over a set of vid:pid `classes` (the
    generalized pad_classes pattern). `selected` is the currently-on subset;
    `labels` maps class->display name; `on_change(cls, value)` persists."""
    c = style.c
    row = tk.Frame(parent, bg=c["surface"]); row.pack(fill="x", pady=4)
    if title:
        tk.Label(row, text=f"{title}", bg=c["surface"], fg=c["text"], width=14,
                 font=style.font(size), anchor="w").pack(side="left", padx=8)
    members = []
    for cls in classes:
        nm = labels.get(cls, cls)
        b = toggle(row, style, nm, cls in selected,
                   lambda v, k=cls: on_change(k, v), width=14, size=size)
        b.pack(side="left", padx=4)
        members.append(b)
    # Within a pad row, Left/Right walks between the toggles (A flips the focused
    # one), overriding toggle's default off/on hmove so the row reads as one unit.
    for i, m in enumerate(members):
        m._mad_hmove = (lambda d, i=i: members[(i + d) % len(members)].focus_set()) \
            if len(members) > 1 else m._mad_hmove
    return members[0] if members else None
