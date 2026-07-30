"""env_hygiene — the ONE place that strips Steam's Game Mode overlay from LD_PRELOAD.

Steam launches ES-DE (and thus this daemon) with LD_PRELOAD pointing at
gameoverlayrenderer.so for BOTH arches. Children we spawn inherit it, with two
noisy/harmful effects:
  - the 32-bit .so can't load into our 64-bit tools (bash/rclone/tar/sudo/...),
    so ld.so prints an 'object ... cannot be preloaded (wrong ELF class):
    ignored' ERROR for every spawn — under the post-update reapply PTY that
    flood interleaves with sudo's prompt and breaks the password handshake;
  - the 64-bit .so DOES load, and every fork-without-exec (bash subshells,
    command substitutions) makes it print 'pid X != Y, skipping destruction
    (fork without exec?)' at exit — hundreds of lines per transfer job .out.
Nothing we spawn needs the Steam overlay, so every spawn site cleans its env
through here (was: per-module _clean_env copies with a keep-in-sync comment).
"""
from __future__ import annotations

import os


def clean_env(base: dict | None = None) -> dict:
    """A copy of `base` (default os.environ) with gameoverlayrenderer.so stripped
    from LD_PRELOAD. Idempotent; the variable is dropped entirely if nothing is left."""
    env = dict(os.environ if base is None else base)
    pre = env.get("LD_PRELOAD", "")
    if "gameoverlayrenderer.so" in pre:
        kept = [p for p in pre.replace(":", " ").split() if "gameoverlayrenderer.so" not in p]
        if kept:
            env["LD_PRELOAD"] = " ".join(kept)
        else:
            env.pop("LD_PRELOAD", None)
    return env
