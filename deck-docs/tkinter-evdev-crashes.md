
## python-evdev hot-unplug + tkinter thread-safety crashes (2026-06-07)

### python-evdev read on removed device — does NOT segfault on modern versions
- Installed: python-evdev 1.7.1 (pacman). Path: read_one() -> _input.device_read(fd) (C).
- Historical segfaults in device_read_many()/read() were fixed in v0.3.1 (2012) and v0.4.0 (2013).
  Source: https://python-evdev.readthedocs.io/en/stable/changelog.html
- On hot-unplug / BT sleep, read()/read_one() raise OSError [Errno 19] ENODEV (a catchable Python
  exception), NOT a C segfault. select() may still report the fd readable (race), then the read
  errno's out. Source: https://github.com/gvalkov/python-evdev/issues/64 and #67.
- Robust pattern: catch OSError, check errno == errno.ENODEV, then close() the device and drop it
  from the poll set immediately (don't wait for the next periodic scan). Optionally os.path.exists()
  on the device path. grab()/ungrab() not required for read-only polling.

### tkinter thread-safety native crashes
- "Tcl_AsyncDelete: async handler deleted by the wrong thread" = SIGABRT panic that PRINTS that
  exact message to stderr (uncatchable). Triggered when a Tk object (e.g. PhotoImage) is
  garbage-collected on a non-main thread. Sources: https://bugs.python.org/issue39093 ,
  https://github.com/python/cpython/issues/113770 , PySimpleGUI#271.
  => If stderr shows NO such line, this crash did NOT happen.
- PhotoImage.zoom() segfault (https://bugs.python.org/issue25959): only with absurd zoom FACTORS
  (gigapixel result, e.g. zoom(54000)). zoom args are multipliers, not target dims. Bounded small
  factors are safe. Fixed in Tk trunk to raise "not enough free memory for image buffer".
- Tk pixmap exhaustion under X: "X Error ... BadAlloc ... X_CreatePixmap" — PRINTS to stderr too.
  Source: Tk bug #2617 (sourceforge), comp.lang.tcl threads.
- root.after(0, cb) is SAFE to call from the main thread; calling Tk from a worker thread is not.
  MAD's workers correctly only enqueue closures to a Queue drained by a main-thread pump (_ui_pump).
