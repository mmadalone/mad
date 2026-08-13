r"""Shared Yuzu-fork (Citron/Eden) settings-page primitives.

citron_settings.py and eden_settings.py are NOT clones and must never be merged - their
enum option tables intentionally diverge (e.g. resolution_setup: 14 entries with 1.25x at
index 12 on Citron vs 13 entries with 1.25x at index 4 on Eden; writing an index under the
wrong table silently mis-sets the user's setting). What IS shared, byte-identical, and
correctness-critical lives here:

- yuzu_write: the fork family's one subtle write rule. Every value write must also flip
  the `key\default=false` twin, else the fork treats the line as "compiled default" and
  DISCARDS it on read (the latent silent-discard bug both settings modules document).
- make_builders: the _bool/_enum/_int/_float descriptor builders, parameterized on the
  descriptors' baked "file" key so it can never drift from the module's _FILE (the _F
  trap: descriptors bake the name at import while do_get keys file_texts at call time -
  pass the same value to do_get's file_key).
"""
from __future__ import annotations

from . import cfgutil


def yuzu_write(text: str, section: str, key: str, value: str) -> str | None:
    """Replace-fn for cfgutil.apply_set: write `key=value` AND flip `key\\default=false`
    so the fork honours the value (a `\\default=true`/absent twin makes it discard the
    line). ini_set_or_insert replaces in place if present, else appends to the section."""
    t = cfgutil.ini_set_or_insert(text, section, key, value)
    if t is None:
        return None
    t2 = cfgutil.ini_set_or_insert(t, section, key + "\\default", "false")
    return t2 if t2 is not None else t


def make_builders(file_name: str):
    """(_bool, _enum, _int, _float) descriptor builders baking "file": file_name.
    The caller must pass the SAME file_name as do_get's file_key."""

    def _bool(key, label, section, *, true="true", false="false"):
        return {"key": key, "label": label, "file": file_name, "section": section,
                "type": "bool", "bool_true": true, "bool_false": false}

    def _enum(key, label, section, options, *, mode="index", stored=None):
        it = {"key": key, "label": label, "file": file_name, "section": section,
              "type": "enum", "write_mode": mode, "options_display": options}
        if stored is not None:
            it["options_stored"] = stored
        return it

    def _int(key, label, section, lo, hi, step=1):
        return {"key": key, "label": label, "file": file_name, "section": section,
                "type": "int", "min": lo, "max": hi, "step": step}

    def _float(key, label, section, lo, hi, step):
        return {"key": key, "label": label, "file": file_name, "section": section,
                "type": "float", "min": lo, "max": hi, "step": step}

    return _bool, _enum, _int, _float
