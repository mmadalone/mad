#!/usr/bin/env python3
"""mad-backend — headless JSON daemon behind the ES-DE-native MAD control panel.

Spawned by the panel (es-app/src/guis/mad/MadBackend.cpp) with pipes on
stdin/stdout; speaks newline-delimited JSON (spec: deck-docs/mad-backend-protocol.md,
protocol version PROTO below). stderr is the daemon's log (the panel points it
at ~/Emulation/storage/controller-router/mad-backend.log).

Lifecycle invariants:
  • stdin EOF or SIGTERM  ⇒ stop every stream (ungrab all evdev, kill children,
    restore anything paused), flush, exit 0 — a dead panel can never leave a
    grabbed pad behind.
  • PR_SET_PDEATHSIG(SIGTERM) as belt-and-braces if ES-DE dies without closing
    the pipe.
  • One instance: exclusive flock on mad-backend.lock — a second daemon exits
    with a structured "fatal" event (two daemons = lost localpolicy writes).

`--selfcheck` (used by deck-post-update.sh): import everything + print OK.
NEVER import tkinter here (the whole point is a Tk-free backend); deps are
python3 + python-evdev only.
"""
from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

PROTO = 1
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

RUN_DIR = Path.home() / "Emulation/storage/controller-router"
LOCK_FILE = RUN_DIR / "mad-backend.lock"


def _fatal(code: str, message: str, exit_code: int) -> None:
    print(json.dumps({"event": "fatal", "data": {"code": code, "message": message}}),
          flush=True)
    sys.exit(exit_code)


def _backend_version() -> str:
    try:
        return (HERE / "VERSION").read_text().strip()
    except OSError:
        return "unknown"


def _caps() -> list[str]:
    import glob
    import shutil
    caps = ["evdev"]
    if glob.glob("/dev/hidraw*"):
        caps.append("hidraw")
    if shutil.which("v4l2-ctl"):
        caps.append("v4l2")
    caps.append("sdl")           # probed lazily on the first devices.sdl call
    return caps


def main() -> int:
    # ── deps guard (before any lib import — lib.devices SystemExits without evdev)
    try:
        import evdev  # noqa: F401
    except ImportError:
        _fatal("ENODEPS",
               "python-evdev missing (SteamOS update wiped it?) — run "
               "deck-post-update.sh from Desktop Mode", 3)

    if "--selfcheck" in sys.argv:
        from lib import (devices, es_collections, es_systems, localpolicy,  # noqa: F401
                         mad_backup, mad_config, policy, routing,
                         standalone_preview)
        from lib.madsrv import (capture_cmds, device_cmds, policy_cmds,  # noqa: F401
                                preview_cmds, rpc, systems_cmds)
        assert "tkinter" not in sys.modules, "tkinter leaked into the backend!"
        print(f"mad-backend selfcheck OK (proto {PROTO}, version {_backend_version()})")
        return 0

    # ── single instance (flock held for the process lifetime)
    import fcntl
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        _fatal("EBUSY", "another mad-backend instance is running", 4)
    os.write(lock_fd, f"{os.getpid()}\n".encode())

    # ── die with the panel even if the pipe lingers (belt-and-braces; EOF is primary)
    try:
        import ctypes
        PR_SET_PDEATHSIG = 1
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except Exception:
        pass

    sys.stdout.reconfigure(line_buffering=True)

    from lib.madsrv import rpc
    from lib.madsrv import (capture_cmds, device_cmds, policy_cmds,  # noqa: F401
                            preview_cmds, systems_cmds)             # (register methods)
    assert "tkinter" not in sys.modules, "tkinter leaked into the backend!"

    @rpc.method("hello.ack")
    def _hello_ack(params):
        if params.get("proto") not in (None, PROTO):
            print(f"mad-backend: panel speaks proto {params.get('proto')}, "
                  f"we speak {PROTO}", file=sys.stderr)
        return {"proto": PROTO}

    @rpc.method("shutdown")
    def _shutdown(params):
        raise KeyboardInterrupt          # caught below → clean teardown path

    stopping = {"sig": False}

    def _on_term(signum, frame):
        stopping["sig"] = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)

    rpc.event("hello", {
        "proto": PROTO,
        "backend_version": _backend_version(),
        "python": ".".join(map(str, sys.version_info[:3])),
        "caps": _caps(),
        "pid": os.getpid(),
    })

    code = 0
    try:
        for line in sys.stdin:           # EOF (panel gone) ends the loop
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError:
                rpc.event("protocol_error", {"message": f"bad JSON: {line[:200]}"})
                continue
            try:
                rpc.dispatch(req)
            except KeyboardInterrupt:    # "shutdown" method (dispatched inline)
                break
    except KeyboardInterrupt:
        pass
    except Exception as e:               # never die without the teardown below
        print(f"mad-backend: main loop error: {e!r}", file=sys.stderr)
        code = 1
    finally:
        # THE teardown invariant: no grab/child/paused-driver survives this.
        from lib.madsrv.rpc import stop_all_streams
        stop_all_streams()
    return code


if __name__ == "__main__":
    sys.exit(main())
