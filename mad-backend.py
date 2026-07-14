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

from lib import mad_paths  # noqa: E402

RUN_DIR = mad_paths.storage("controller-router")
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
                         mad_backup, mad_config, pad_assign, policy, routing,
                         standalone_preview)
        from lib.madsrv import (backends_cmds, backup_cmds, bezel_cmds,  # noqa: F401
                                capture_cmds, cemu_games, cemu_packs_cmds, cemu_pergame,
                                cemu_pg_input_cmds, cemu_res_cmds, cemu_settings, daphne_cmds, device_cmds,
                                dolphin_settings, dolphin_hotkeys_cmds, dolphin_gc_input_cmds, dolphin_gc_dock_cmds, dolphin_gc_pads_cmds, dolphin_games, dolphin_pergame_cmds, dolphin_codes_cmds, dolphin_wii_hh_cmds, eden_cmds, eden_dock_cmds, eden_input_cmds,
                                eden_addons_cmds, eden_cheats_cmds, eden_hotkeys_cmds, eden_pergame,
                                eden_pg_input_cmds, eden_settings,
                                citron_addons_cmds, citron_cheats_cmds, citron_dock_cmds, citron_games,
                                citron_hotkeys_cmds, citron_input_cmds, citron_pergame,
                                citron_pg_input_cmds, citron_settings,
                                guncon2_retail_input_cmds,
                                lindbergh_cmds, model2_cmds,
                                model3_cmds, pads_cmds, pcsx2_blacklist_cmds, pcsx2_cmds, pcsx2_games, pcsx2_hotkeys_cmds, pcsx2_input_cmds,
                                pcsx2_fork_settings, pcsx2_pergame_cmds, pcsx2_pergame_input_cmds, pcsx2_settings,
                                pcsx2x6_cmds, pcsx2x6_global_cmds, pcsx2x6_hotkeys_cmds, pcsx2x6_input_cmds, pcsx2x6_lightgun_cmds, pcsx2x6_retail_input_cmds,
                                policy_cmds, policy_settings_cmds, preview_cmds,
                                retroarch_cmds, retroarch_game_cmds, retroarch_settings, rpc,
                                rpcs3_input_cmds, rpcs3_patches_cmds, rpcs3_pergame_cmds, rpcs3_pergame_input_cmds, rpcs3_settings, ryujinx_cmds, ryujinx_dock_cmds,
                                ryujinx_addons_cmds, ryujinx_cheats_cmds, ryujinx_hotkeys_cmds,
                                ryujinx_input_cmds, ryujinx_pergame,
                                ryujinx_settings,
                                onthego_cmds, sidebar_cmds, sinden_cmds, standalones_cmds,
                                systems_cmds, tester_cmds, xemu_input_cmds)
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

    import threading

    from lib import devices as _devices

    # Warm the device probes in the background ASAP so they overlap the daemon's
    # own imports + the panel handshake instead of blocking the first Preview.
    # enumerate_devices first (warms the per-node evdev cache — ~1 s cold, ~1 ms
    # after — so the fast preview.devices is instant), THEN the slow ~6 s SDL
    # identity probe (preview.all). Daemon thread; pure prefetch, errors ignored.
    def _warm_sdl():
        try:
            _devices.enumerate_devices()
        except Exception:
            pass
        try:
            _devices.sdl_devices()
            # The pump=False RPC readers (e.g. preview) return [] while THIS warm holds
            # _SDL_LOCK, and that empty result can get pinned in the staterev response
            # cache. Bump 'devices' now SDL is warm so those entries recompute against
            # the real list — the watch thread only bumps on an evdev path-set change,
            # which never fires when pads are already connected at MAD open. (pads.get
            # uses pump=True and waits out this warm, so it's not one of those readers.)
            from lib import staterev
            staterev.bump("devices")
        except Exception:
            pass

    threading.Thread(target=_warm_sdl, daemon=True, name="mad-warm-sdl").start()

    from lib.madsrv import rpc
    from lib import staterev
    from lib.madsrv import (backends_cmds, backup_cmds, bezel_cmds,  # noqa: F401
                            capture_cmds, cemu_games, cemu_packs_cmds, cemu_pergame,
                            cemu_pg_input_cmds, cemu_res_cmds, cemu_settings, daphne_cmds, device_cmds,
                            dolphin_settings, dolphin_hotkeys_cmds, dolphin_gc_input_cmds, dolphin_gc_dock_cmds, dolphin_gc_pads_cmds, dolphin_games, dolphin_pergame_cmds, dolphin_codes_cmds, dolphin_wii_hh_cmds, eden_cmds, eden_dock_cmds, eden_input_cmds,
                            eden_addons_cmds, eden_cheats_cmds, eden_hotkeys_cmds, eden_pergame,
                            eden_pg_input_cmds, eden_settings,
                            citron_addons_cmds, citron_cheats_cmds, citron_dock_cmds, citron_games,
                            citron_hotkeys_cmds, citron_input_cmds, citron_pergame,
                            citron_pg_input_cmds, citron_settings,
                                guncon2_retail_input_cmds,
                                lindbergh_cmds, model2_cmds,
                            model3_cmds, pads_cmds, pcsx2_blacklist_cmds, pcsx2_cmds, pcsx2_games, pcsx2_hotkeys_cmds, pcsx2_input_cmds,
                                pcsx2_fork_settings, pcsx2_pergame_cmds, pcsx2_pergame_input_cmds, pcsx2_settings,
                            pcsx2x6_cmds, pcsx2x6_global_cmds, pcsx2x6_hotkeys_cmds, pcsx2x6_input_cmds, pcsx2x6_lightgun_cmds, pcsx2x6_retail_input_cmds,
                            policy_cmds, policy_settings_cmds, preview_cmds,
                            retroarch_cmds, retroarch_game_cmds, retroarch_settings,
                            rpcs3_input_cmds, rpcs3_patches_cmds, rpcs3_pergame_cmds, rpcs3_pergame_input_cmds, rpcs3_settings, ryujinx_addons_cmds, ryujinx_cheats_cmds, ryujinx_cmds,
                            ryujinx_dock_cmds, ryujinx_hotkeys_cmds,
                            ryujinx_input_cmds, ryujinx_pergame,
                            ryujinx_settings,
                            onthego_cmds, sidebar_cmds, sinden_cmds, standalones_cmds, systems_cmds,
                            tester_cmds, xemu_input_cmds)  # (register)
    assert "tkinter" not in sys.modules, "tkinter leaked into the backend!"

    # Push a state.rev event whenever a revision bumps (config/devices/bezels) so
    # the panel can drop the kept-alive pages that depend on the changed state.
    staterev.set_listener(lambda revs: rpc.event("state.rev", revs))

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
        "revs": staterev.all(),      # seed the panel's page-cache epoch
    })

    # SDL is already warming (started right after stdout setup); also prime the
    # DolphinBar Wiimote probe so the first Preview's wii line is ready too.
    def _warm_wii():
        try:
            device_cmds._devices_wiimotes({})
        except Exception:
            pass

    threading.Thread(target=_warm_wii, daemon=True, name="mad-warm-wii").start()

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
        from lib.madsrv.rpc import stop_all_streams, shutdown_pool
        stop_all_streams()
        shutdown_pool()   # 10.0: exit promptly — don't wait on in-flight slow pool tasks
        try:              # release the persistent SDL joystick subsystem
            _devices.sdl_quit()
        except Exception:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
