# Cemu / Eden (Yuzu) input config — CLI + Game-Mode reality

## Can you open the input-settings window directly (no game)?
**No CLI flag for it** — confirmed via each binary's own `--help` (2026-06-06):
- **Cemu** (`~/Applications/Cemu.AppImage`): launch opts are `-g/--game`, `-t/--title-id`, `-m/--mlc`, `-f/--fullscreen`, `-u/--ud`, `-a/--account`, `--force-interpreter`, `--enable-gdbstub`, `-e/--extract`. **No input/controller/settings flag.** Input config is GUI-only: **Options → Input Settings** (per cemu.cfw.guide/controller-configuration). Running `Cemu.AppImage` with **no `-g`** opens the main window (game list) — from there you reach Input Settings.
- **Eden/Yuzu** (`~/Applications/Eden-Linux-v0.0.3-steamdeck.AppImage`): Yuzu family CLI is `-g/--game`, `-f/--fullscreen`, `-h`, `-v` (manpages.debian.org/testing/yuzu/yuzu-cmd.1; suyu/yuzu "Add basic command line arguments"). **No flag to open controller config.** GUI-only: **Emulation → Configure → Controls**. Running with no game opens the main window.

→ So a "show input settings" feature must **launch the emulator GUI with no game**, then the user navigates to the input page (which shows live stick/button movement).

## Game-Mode (gamescope) reality — the catch
gamescope on the Deck is a **single-window microcompositor with NO window decorations** (no title bar / no X-to-close):
- "Gamescope only supports one window" → apps opening **pop-up / modal dialogs** are problematic; older behaviour flickered between parent+child; "Gaming mode only renders the parent window… until the child comes back." Valve added a **window switcher** to mitigate. (steamcommunity Deck discussions; ValveSoftware/SteamOS#1268; archwiki Gamescope.)
- Cemu **Input Settings** and Eden **Configure→Controls** are **child/modal dialogs** → may flicker / not render cleanly in Game Mode; the Steam **window switcher** (STEAM + L/R or the QAM) can help switch to the dialog.
- **Closing in Game Mode** (no window X): use the emulator's own **File → Exit** (clicked with a real mouse — the **X-Arcade trackball** works as a raw mouse; the **DualSense touchpad-as-mouse needs Steam Input**, which is OFF for the router, so it likely won't move the cursor), the **Steam QAM → Exit game**, or MAD's **quit-combo** (only if the emulator is started as a quit-combo-watched process).

## Verdict
Feasible: a MAD button that **launches the emulator GUI with no game** so the user can open the input page + see live sticks. Caveats: the input page is a **modal dialog → gamescope friction** (window switcher helps), there are **no X frames** (close via File→Exit / QAM / quit-combo), and reliable mouse nav needs the **X-Arcade trackball** (touchpad-as-mouse needs Steam Input, which is off). A jank-free alternative is a **live-input visualizer inside MAD** (shows the raw pad live in Game Mode), but it shows the pad, not the emulator's mapping.

Sources: cemu.cfw.guide/controller-configuration.html · wiki.cemu.info/wiki/Getting_Started · manpages.debian.org/testing/yuzu/yuzu-cmd.1 · wiki.archlinux.org/title/Gamescope · github.com/ValveSoftware/SteamOS/issues/1268 · steamcommunity Deck discussions (gamescope one-window / modal). Captured 2026-06-06.
