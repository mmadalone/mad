# Python stdlib curses under `curl | bash` (reference — NOT used)

_Investigated 2026-06-21. We chose whiptail instead (see `installer-tui-prior-art.md`);
this is kept only so a future curses attempt doesn't re-derive the gotchas._

## Why we did NOT use stdlib curses for the installer picker
- Stdlib `curses` has **no checkbox / multi-select / dialog primitives** — the official
  HOWTO recommends Urwid, which is not on the SteamOS base image (and we ship stdlib-only).
- `curses.newterm()` / `set_term()` are **NOT in CPython's `_curses`** (bpo-45934; `hasattr`
  False on 3.13). `curses.wrapper()` has no fd parameter, and ncurses `initscr` can EXIT the
  process uncatchably on a terminal error.

## If curses is ever revisited — the canonical `curl|bash` pattern (stdlib only)
The piped script's stdin is the curl stream, not a TTY, so don't `initscr` on the default
streams. Reopen fds 0/1 to `/dev/tty` first:
1. `locale.setlocale(locale.LC_ALL, "")`.
2. Detect headless: `try: fd = os.open(os.ctermid()/"/dev/tty", os.O_RDWR|os.O_NOCTTY)` —
   `OSError` (errno 6 ENXIO) ⇒ no controlling terminal ⇒ fall back to express defaults,
   never call `initscr`.
3. Open `/dev/tty` r+b unbuffered; `saved_in,saved_out = os.dup(0),os.dup(1)`;
   `os.dup2(tty.fileno(), 0)` and onto `1`; `os.environ.setdefault("TERM","xterm-256color")`;
   then `curses.wrapper(app)`. `finally:` dup2 the saved fds back, close them.
4. Resize: handle `ch == curses.KEY_RESIZE` (410) → `update_lines_cols()` + redraw; wrap
   edge `addstr` in `try/except curses.error`.
5. Render with ACS box-drawing (`window.box()`/`border()`), NOT Unicode/emoji.

Source: docs.python.org/3 curses library + HOWTO; man7 initscr.3x; cpython `_cursesmodule.c`;
bpo-45934; empirical on-device 2026-06-21.
