"""eden.input_* — per-button input mapping for Eden (Switch).

Thin shim over the shared Yuzu-fork page in `yuzu_input` (Eden and Citron store identical
bindings, so the logic lives once there). This file only pins Eden's config path + behaviour
(Eden does NOT flip the `\\default` twin on a button/stick write) and registers the RPC methods.

Edits the Player bindings in `[Controls]` of ~/.config/eden/qt-config.ini; a per-button remap
changes only the device-exact `button:M` / `axis:N` token, preserving the device guid/port.
Switch is `router_skip = true` (the router never rewrites this at launch); Eden rewrites
qt-config.ini on exit, so edits are refused while it runs.
"""
from __future__ import annotations

from pathlib import Path

from .. import proc_guard          # noqa: F401 (tests patch eden_input_cmds.proc_guard)
from . import yuzu_input
from .rpc import method

_FILE = Path.home() / ".config/eden/qt-config.ini"

# The instance reads _FILE through a getter so tests can repoint the module global.
_impl = yuzu_input.YuzuInputPage(
    file_getter=lambda: _FILE, proc="eden", flip_default_on_write=False)
_buf = _impl.buf                    # module alias (same InputBuffer object) for tests
_configured_pad = yuzu_input._configured_pad   # re-export (pure) for test_mad_pages


def _scheme(guid: str) -> str:
    return _impl.scheme(guid)


@method("eden.input_get", slow=True)   # buffered: NO cache=("config",) — the buffer is truth
def _input_get(params):
    return _impl.input_get(params)


@method("eden.input_set", slow=True)
def _input_set(params):
    return _impl.input_set(params)


@method("eden.selector_set", slow=True)
def _selector_set(params):
    return _impl.selector_set(params)


@method("eden.input_clear", slow=True)
def _input_clear(params):
    return _impl.input_clear(params)


@method("eden.input_save", slow=True)
def _input_save(params):
    return _impl.input_save(params)


@method("eden.input_cancel", slow=True)
def _input_cancel(params):
    return _impl.input_cancel(params)
