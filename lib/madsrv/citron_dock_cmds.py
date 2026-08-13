"""citron_dock.* - the "Dock detection" toggle on the Citron tile.

Thin shim: all logic (and the full design note) lives in the shared switch_dock engine.
"""
from . import switch_dock

switch_dock.register("citron_dock", "citron", "Citron")
