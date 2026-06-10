"""MAD page mixin: Daphne/Hypseus button mapping (the "Daphne" sidebar page).

Extracted verbatim from router-config-gui.py (MAD task #13 modularization).
DaphnePageMixin is NOT standalone — it must be mixed into MAD's App class and
expects the host to provide: self.root / self.c / self.font / self.nav /
self._ui_q, the App helpers _title/_scroll/_lbl/_btn/_toggle/_textwrap/
_replace/_run/_select_page, and getattr-guarded cleanup of
_dp_capturing/_dp_proc/_dp_gen in App._clear().
"""
from __future__ import annotations

import sys
from pathlib import Path

import tkinter as tk

from . import hypinput

HERE = Path(__file__).resolve().parent.parent             # lib/.. = the launchers dir


class DaphnePageMixin:
    def daphne(self):
        """Map the X-Arcade to Hypseus laserdisc controls — press a button to bind it.
        Edits the GLOBAL hypinput.ini; Save writes it (backup .bak). Per-game maps and
        direction/advanced editing build on this in later passes."""
        self._title("Daphne / Hypseus controls")
        inner = self._scroll()
        self._dp_status_lbl = self._lbl(inner, "", role="dim", size=12, anchor="w", pady=(0, 6))
        self._lbl(inner,
                  "Map your X-Arcade to Hypseus laserdisc-game controls: focus a row, choose "
                  "“Press to bind”, then press the button on the cabinet. “Save” writes "
                  "the global map (used by every Daphne game). No keyboard needed.",
                  role="text", size=13, anchor="w", pady=(0, 10),
                  wraplength=self._textwrap(), justify="left")

        if not hasattr(self, "_dp_scope"):
            self._dp_scope = "global"            # "global" | "game" (persists across page entries)
            self._dp_game = None                 # (gamedir: Path, basename: str)
        self._dp_dirty = False
        self._dp_capturing = False
        self._dp_proc = None
        self._dp_cells = {}

        # scope selector — edit the GLOBAL map (every game) or a PER-GAME override.
        scope = tk.Frame(inner, bg=self.c["bg"]); scope.pack(anchor="w", pady=(0, 10))
        tk.Label(scope, text="Map:", bg=self.c["bg"], fg=self.c["text"],
                 font=self.font(14)).pack(side="left", padx=(2, 8))
        self._btn(scope, ("▸ Global" if self._dp_scope == "global" else "Global"),
                  self._dp_set_global).pack(side="left", padx=4)
        gname = (self._dp_game_names().get(self._dp_game[1], self._dp_game[1])
                 if self._dp_game else "pick…")
        self._btn(scope, (f"▸ This game: {gname}" if self._dp_scope == "game" else "This game…"),
                  self._dp_pick_game).pack(side="left", padx=4)

        if self._dp_scope == "game" and self._dp_game:
            gd, base = self._dp_game
            if hypinput.has_per_game(gd, base):
                self._dp_hi = hypinput.load(hypinput.per_game_ini(gd, base))
                self._dp_scope_caption = f"per-game map  ({base}.ini)"
            else:
                self._dp_hi = hypinput.load()          # seed a new per-game map from the global one
                self._dp_scope_caption = f"new {base}.ini  (copied from global; Save creates it)"
        else:
            self._dp_hi = hypinput.load()
            self._dp_scope_caption = "global map  (" + str(hypinput.GLOBAL_INI) + ")"

        if self._dp_scope == "game" and self._dp_game:
            hint = hypinput.GAME_HINTS.get(self._dp_game[1])
            if hint:
                self._lbl(inner, "ℹ  " + hint, role="accent", size=12, anchor="w",
                          pady=(0, 8), wraplength=self._textwrap(), justify="left")

        # Scene transitions — instant seek (-seek_frames_per_ms 0); scope follows the selector above.
        self._lbl(inner, "Scene transitions", role="accent", size=15, anchor="w", pady=(6, 2))
        tf = tk.Frame(inner, bg=self.c["bg"]); tf.pack(anchor="w", fill="x")
        per_game = (self._dp_scope == "game" and self._dp_game)
        scope_word = self._dp_game[1] if per_game else "all laserdisc games"
        self._toggle(tf, f"Instant ({scope_word})", self._dp_seek_get(), self._dp_seek_set)
        self._lbl(inner,
                  "Skips the emulated laserdisc SEEK delay between scenes (removes the loading wait). "
                  "If a game's audio/timing ever feels off, switch it back off.",
                  role="dim", size=11, anchor="w", pady=(2, 8),
                  wraplength=self._textwrap(), justify="left")

        # Seek-index builder — pre-build each scene's .dat so games never pause to rebuild a
        # seek index mid-play. Scope follows the selector: per-game builds that game, global
        # builds them all. Runs Hypseus ON-SCREEN and returns here when finished.
        self._lbl(inner, "Seek-index builder", role="accent", size=15, anchor="w", pady=(10, 2))
        xf = tk.Frame(inner, bg=self.c["bg"]); xf.pack(anchor="w", fill="x")
        if per_game:
            self._btn(xf, f"⚙ Build seek index — {self._dp_game[1]}",
                      lambda f=self._dp_game[0].name: self._dp_build_index(f)).pack(side="left", padx=(0, 10))
        else:
            self._btn(xf, "⚙ Build seek indexes — ALL games",
                      lambda: self._dp_build_index("all")).pack(side="left", padx=(0, 10))
        self._lbl(inner,
                  "Builds the laserdisc seek indexes up front so scene changes never stop to "
                  "“seek”. Runs on-screen — you'll see it flash through scenes, then it returns "
                  "here. One-time; already-built scenes are skipped. ALL games can take several "
                  "minutes; hold Start+Select to abort the current game.",
                  role="dim", size=11, anchor="w", pady=(2, 8),
                  wraplength=self._textwrap(), justify="left")

        self._lbl(inner, "Buttons", role="accent", size=15, anchor="w", pady=(4, 2))
        bf = tk.Frame(inner, bg=self.c["bg"]); bf.pack(anchor="w", fill="x")
        for action in ("COIN1", "START1", "BUTTON1", "BUTTON2", "BUTTON3"):
            self._dp_action_row(bf, action, bindable=True)

        self._lbl(inner, "Player 2 (coin + start only)", role="accent", size=15, anchor="w", pady=(14, 2))
        self._lbl(inner,
                  "Hypseus laserdisc games have NO separate P2 gameplay buttons — both players share "
                  "the controls above. Only Coin 2 / Start 2 are P2-specific:",
                  role="dim", size=11, anchor="w", pady=(0, 4),
                  wraplength=self._textwrap(), justify="left")
        p2 = tk.Frame(inner, bg=self.c["bg"]); p2.pack(anchor="w", fill="x")
        for action in ("COIN2", "START2"):
            self._dp_action_row(p2, action, bindable=True)

        self._lbl(inner, "Stick / steering (directions)", role="accent", size=15, anchor="w", pady=(14, 2))
        self._lbl(inner,
                  "The joystick / STEERING-WHEEL inputs. Bind by pushing the stick (or turning the "
                  "wheel) in that direction — an analog axis is captured; a digital d-pad records as a "
                  "button. Driving games (GP World, Road Blaster) steer with Left/Right; their "
                  "gas/brake are the action BUTTONS above (Hypseus has no analog pedal).",
                  role="dim", size=11, anchor="w", pady=(0, 6),
                  wraplength=self._textwrap(), justify="left")
        df = tk.Frame(inner, bg=self.c["bg"]); df.pack(anchor="w", fill="x")
        for action in hypinput.DIRECTIONS:
            self._dp_action_row(df, action, bindable=True)

        bar = tk.Frame(inner, bg=self.c["bg"]); bar.pack(anchor="w", pady=(4, 20))
        self._btn(bar, "\U0001f4be Save", self._dp_save).pack(side="left", padx=(0, 10))
        self._btn(bar, "↺ Reload", lambda: self._replace(self.daphne)).pack(side="left", padx=(0, 10))
        self._dp_status("Editing the " + self._dp_scope_caption)

    def _dp_build_index(self, arg):
        """Launch singe-indexer.sh on-screen to pre-build Hypseus seek indexes (returns here when
        done). arg = "all" or a game folder name (e.g. gpworld.daphne)."""
        self._run([str(HERE / "singe-indexer.sh"), arg], self._dp_status_lbl, "singe-indexer")

    def _dp_action_row(self, parent, action, *, bindable):
        c = self.c
        row = tk.Frame(parent, bg=c["surface"]); row.pack(fill="x", pady=3)
        tk.Label(row, text="  " + hypinput.ACTION_LABELS.get(action, action), bg=c["surface"],
                 fg=c["text"], font=self.font(14), anchor="w", width=18).pack(side="left", padx=6)
        cell = tk.Label(row, text="", bg=c["surface"], font=self.font(14, bold=True),
                        anchor="w", width=16)
        cell.pack(side="left", padx=6)
        self._dp_cells[action] = cell
        if bindable:
            self._btn(row, "✎ Press to bind",
                      lambda a=action: self._dp_bind_press(a)).pack(side="left", padx=6)
            self._btn(row, "Clear",
                      lambda a=action: self._dp_clear(a)).pack(side="left", padx=6)
        self._dp_update_cell(action)

    def _dp_update_cell(self, action):
        cell = self._dp_cells.get(action)
        if cell is None or not cell.winfo_exists():
            return
        bval = self._dp_hi.button_value(action)
        if action in hypinput.DIRECTIONS:
            ax = self._dp_hi.axis_value(action)
            txt = hypinput.button_label(bval) if bval else (f"axis {ax}" if ax else "— (unbound)")
            cell.config(text=txt, fg=self.c["text"])
        else:
            # 0 = action unreachable on the stick. Only the PRIMARY P1 controls warn (Coin2/Start2
            # default to unbound, which is normal — don't nag).
            warn = (bval == 0 and action in ("COIN1", "START1", "BUTTON1", "BUTTON2", "BUTTON3"))
            cell.config(text=hypinput.button_label(bval),
                        fg=self.c.get("warn", "#ff6b5e") if warn else self.c["accent"])

    def _dp_status(self, text, *, warn=False):
        lbl = getattr(self, "_dp_status_lbl", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.config(text=text, fg=self.c.get("warn", "#ff6b5e") if warn else self.c["text_dim"])

    def _dp_clear(self, action):
        self._dp_hi.clear_button(action)
        self._dp_dirty = True
        self._dp_update_cell(action)
        self._dp_status(f"{hypinput.ACTION_LABELS.get(action, action)} unbound. Save to apply.")

    def _dp_bind_press(self, action):
        """Arm an SDL-subprocess capture of ONE X-Arcade press; menu nav is locked meanwhile.
        The capture runs in a separate process (SDL + Tk in-process risks a segfault)."""
        import subprocess
        import threading
        if getattr(self, "_dp_capturing", False):
            return
        self._dp_capturing = True
        lbl = hypinput.ACTION_LABELS.get(action, action)
        is_dir = action in hypinput.DIRECTIONS
        verb = "Push the stick / wheel for" if is_dir else "Press the control for"
        self._dp_status(f"{verb} “{lbl}” on your X-Arcade… (10s)")
        # A non-None nav.capture suppresses ALL menu nav; this no-op ignores the evdev press
        # (the SDL subprocess is the real capture). Released in _dp_bind_done / _clear.
        self.nav._held = set()
        self.nav.capture = lambda held, dev=None: None
        gen = self._dp_gen = getattr(self, "_dp_gen", 0) + 1
        argv = [sys.executable, str(HERE / "lib" / "hypseus_capture.py"), "--timeout", "10"]
        if not is_dir:
            argv += ["--no-axis", "--no-hat"]    # buttons / coin / start are digital buttons only

        def worker():
            import json
            res = {"error": "timeout"}
            try:
                proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True)
                self._dp_proc = proc
                out, _ = proc.communicate(timeout=14)
                if proc.returncode == 0 and out.strip():
                    res = json.loads(out.strip())
                elif proc.returncode == 4:
                    res = {"error": "no_xarcade"}
            except Exception:
                _p = getattr(self, "_dp_proc", None)
                if _p is not None:
                    try:
                        _p.kill()
                    except Exception:
                        pass
            self._ui_q.put(lambda: self._dp_bind_done(action, res, gen))

        threading.Thread(target=worker, daemon=True).start()

    def _dp_bind_done(self, action, res, gen):
        if gen != getattr(self, "_dp_gen", 0):
            return                                   # stale: page left or a newer bind superseded this
        self.nav.capture = None
        self._dp_capturing = False
        self._dp_proc = None
        label = hypinput.ACTION_LABELS.get(action, action)
        if not res or res.get("error") == "no_xarcade":
            self._dp_status("X-Arcade not detected — Identify it first on the Preview page.", warn=True)
            return
        if res.get("error"):
            self._dp_status(f"Cancelled — no button pressed for {label}.")
            return
        kind = res.get("kind")
        is_dir = action in hypinput.DIRECTIONS
        if kind == "button":
            self._dp_hi.set_button(action, int(res["value"]))
            if is_dir:
                self._dp_hi.set_axis(action, None)            # digital direction → clear any axis
            self._dp_dirty = True
            self._dp_update_cell(action)
            self._dp_status(f"{label} → {res.get('name', res['value'])}.  Save to apply.")
        elif kind == "axis" and is_dir:
            self._dp_hi.set_axis(action, res["value"])
            self._dp_hi.set_button(action, 0)                 # analog steering → axis, not a button
            self._dp_dirty = True
            self._dp_update_cell(action)
            self._dp_status(f"{label} → axis {res['value']}.  Save to apply.")
        elif kind == "hat" and is_dir:
            v = int(res["value"])
            if v > 0:                                         # hat on P2/P3 stick → enable via KEY_UP
                self._dp_hi.set_button("UP", v)
                self._dp_dirty = True
                self._dp_update_cell("UP")
                self._dp_status(f"D-pad hat (P{v // 100 + 1}) enabled for all directions. Verify on-screen.")
            else:
                self._dp_status("Your d-pad reads as a HAT on the primary stick — Hypseus uses it "
                                "automatically. If directions don't respond, bind them as an axis.")
        else:
            want = "a stick direction" if is_dir else "a BUTTON"
            self._dp_status(f"That was a {kind} — bind {want} for {label}.", warn=True)

    def _dp_save(self):
        try:
            if self._dp_scope == "game" and self._dp_game:
                gd, base = self._dp_game
                hypinput.write_per_game(gd, base, self._dp_hi)
                self._dp_dirty = False
                self._dp_status(f"Saved {base}.ini and linked it in {base}.commands. "
                                "Applies to this game on its next launch.")
            else:
                hypinput.write_global(self._dp_hi)
                self._dp_dirty = False
                self._dp_status("Saved hypinput.ini (backup: hypinput.ini.bak). "
                                "Applies to every Daphne game on the next launch.")
        except Exception as ex:
            self._dp_status(f"Save failed: {ex}", warn=True)

    def _dp_seek_get(self):
        if self._dp_scope == "game" and self._dp_game:
            return hypinput.per_game_seek_instant(self._dp_game[0], self._dp_game[1])
        return hypinput.global_seek_instant()

    def _dp_seek_set(self, on):
        try:
            if self._dp_scope == "game" and self._dp_game:
                hypinput.set_per_game_seek(self._dp_game[0], self._dp_game[1], on)
                tgt = self._dp_game[1]
            else:
                hypinput.set_global_seek(on)
                tgt = "all laserdisc games"
            self._dp_status(f"Instant transitions {'ON' if on else 'off'} for {tgt}. "
                            "Applies on the next launch.")
        except Exception as ex:
            self._dp_status(f"Couldn't change transitions: {ex}", warn=True)

    def _dp_set_global(self):
        self._dp_scope = "global"
        self._replace(self.daphne)

    def _dp_list_games(self):
        """(gamedir, basename) for every Daphne/Singe game dir under ~/ROMs/daphne."""
        root = Path.home() / "ROMs" / "daphne"
        out = []
        if root.is_dir():
            for p in sorted(root.iterdir()):
                if p.is_dir() and p.suffix in (".daphne", ".singe"):
                    out.append((p, p.stem))
        return out

    def _dp_game_names(self):
        """basename -> display <name> from the daphne gamelist (read-only; ES-DE-safe)."""
        import xml.etree.ElementTree as ET
        out = {}
        gl = Path.home() / "ES-DE" / "gamelists" / "daphne" / "gamelist.xml"
        try:
            for g in ET.parse(gl).getroot().findall("game"):
                stem = Path((g.findtext("path") or "").strip()).stem
                nm = (g.findtext("name") or "").strip()
                if stem and nm:
                    out[stem] = nm
        except Exception:
            pass
        return out

    def _dp_pick_game(self):
        games = self._dp_list_games()
        if not games:
            self._dp_status("No Daphne games found under ~/ROMs/daphne.", warn=True)
            return
        names = self._dp_game_names()              # full display names from the gamelist

        def choose(val):
            self._dp_scope = "game"
            self._dp_game = val

        # Show the full game name on each chooser button (fall back to the folder name); the
        # stored value keeps the basename (used for the <game>.ini / <game>.commands filenames).
        opts = [((gd, base), names.get(base, base)) for gd, base in games]
        opts.sort(key=lambda t: t[1].lower())
        self._select_page("Pick a Daphne game",
                          "The map you edit next applies to ONLY this game (a per-game override).",
                          opts, choose, focus_value=getattr(self, "_dp_game", None))
