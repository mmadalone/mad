"""citron.input_* — per-button input mapping for Citron (Switch, a Yuzu fork).

Thin shim over the shared Yuzu-fork page in `yuzu_input` (identical binding format to Eden --
same Yuzu lineage -- so the logic lives once there). This file only pins Citron's config path +
the two Citron-specific behaviours, and registers the RPC methods:
  • Citron flips the `<key>\\default=false` twin on EVERY write (frontend_common/config.cpp
    ReadSettingGeneric discards a stored value whose `\\default` is true/absent);
  • the payload note points at the per-game named-profile picker under Per-game settings.

Edits the per-player bindings in [Controls] of ~/.config/citron/qt-config.ini; a per-button
remap changes only the device-exact `button:M` / `axis:N` token, preserving the device guid/port.
Switch is `router_skip = true`; Citron rewrites qt-config.ini on exit, so edits are refused while
it runs. The named-profile PICKER is a per-game feature; this global page is the per-button map.
"""
from __future__ import annotations

from pathlib import Path

from .. import proc_guard          # noqa: F401 (tests patch citron_input_cmds.proc_guard)
from . import yuzu_input
from .rpc import method

_FILE = Path.home() / ".config/citron/qt-config.ini"

_impl = yuzu_input.YuzuInputPage(
    file_getter=lambda: _FILE, proc="citron", flip_default_on_write=True,
    note_suffix=" Per-game named profiles live under Per-game settings.")
_buf = _impl.buf                    # module alias (same InputBuffer object) for tests
_configured_pad = yuzu_input._configured_pad   # re-export (pure) for tests


def _scheme(guid: str) -> str:
    return _impl.scheme(guid)


@method("citron.input_get", slow=True)   # buffered: NO cache=("config",) — the buffer is truth
def _input_get(params):
    return _impl.input_get(params)


@method("citron.input_set", slow=True)
def _input_set(params):
    return _impl.input_set(params)


@method("citron.selector_set", slow=True)
def _selector_set(params):
    return _impl.selector_set(params)


@method("citron.input_clear", slow=True)
def _input_clear(params):
    return _impl.input_clear(params)


@method("citron.input_save", slow=True)
def _input_save(params):
    return _impl.input_save(params)


@method("citron.input_cancel", slow=True)
def _input_cancel(params):
    return _impl.input_cancel(params)
