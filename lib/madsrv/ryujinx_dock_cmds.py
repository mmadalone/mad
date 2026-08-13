"""ryujinx_dock.* - the "Dock detection" toggle on the Ryujinx tile (System group).

Thin shim: all logic lives in the shared switch_dock engine. Ryujinx's applied key is its
JSON top-level `docked_mode` (vs the Yuzu forks' ini `[System] use_docked_mode`) - that
difference lives in lib/switch_bind, not in this preference toggle.
"""
from . import switch_dock

switch_dock.register("ryujinx_dock", "ryujinx", "Ryujinx")
