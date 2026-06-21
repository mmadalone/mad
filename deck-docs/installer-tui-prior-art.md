# Installer TUI: front-end decision (whiptail) + prior art

_Investigated 2026-06-21 (interactive-installer build). Cache per house rule #2._

## Decision: the installer picker uses `whiptail`, not a hand-rolled curses TUI.

### Why whiptail (verified on-device 2026-06-21)
- `/usr/bin/whiptail` is present on SteamOS 3.7.25, and `libnewt` (its lib) is
  `Required By: networkmanager` (`pacman -Qi libnewt`) — i.e. it's pulled in by a **core
  system package, so it's in the base image and survives SteamOS updates** (no reinstall,
  unlike the pacman-added `python-evdev`/`tk`). This overturns the old assumption ("whiptail
  isn't reliably present") that had pointed us at curses.
- whiptail ships `--checklist` (multi-select), `--radiolist`, `--menu`, `--yesno`, `--gauge`
  out of the box — exactly the "navigable pick-and-choose" UI we wanted, in pure bash.
- `zenity` + `kdialog` are also present but are **GTK/Qt GUIs** — they can't run over SSH/in
  the headless test path. `dialog` and `gum` are **absent**. So whiptail is the only
  terminal-UI option that's both present and update-proof.

### Why NOT stdlib curses
- Python's stdlib `curses` has **no checkbox/menu primitives** — the official HOWTO points
  you at Urwid (not on the base image). Hand-rolling checkbox/focus/scroll/resize is a lot of
  code with no library to copy. (See `python-curses-tty.md` for the `curl|bash` /dev/tty
  mechanics if curses is ever revisited.)

### `curl | bash` caveat (applies to whiptail too)
The one-liner pipes the script into bash, so the script's stdin is the pipe, not a TTY.
whiptail draws on the controlling terminal but needs a real stdin: capture its result with
the fd-swap idiom and feed it `/dev/tty`:
```
result=$(whiptail --checklist … 3>&1 1>&2 2>&3 </dev/tty)
```
`install.sh` already reads prompts from `/dev/tty`. No `/dev/tty` (truly headless / piped) →
the picker no-ops to defaults (the unattended install path is preserved).

### Prior art surveyed
- **EmuDeck / RetroDECK / Chimera** installers use GUIs (zenity / an AppImage web UI / the
  ES-DE Configurator) — none has a liftable shell-level picker, and none runs headless. So
  there was nothing to reuse; a small whiptail front-end fits MAD better.
- The "**config file = single source of truth, re-runnable installer doubles as reconfigure
  UI**" pattern (pi-hole, Ansible answer-files) is standard — that's `install.conf` here.

Source: local on-device probes 2026-06-21 (`command -v whiptail`, `pacman -Qi libnewt`);
docs.python.org curses HOWTO; github pi-hole issue 3323 (tty-under-curl|bash).
