"""
Game classification for the controller router.

ES-DE invokes hook scripts (and our wrapper) with positional args:
    $1 = ROM path (may contain literal backslash escapes for spaces)
    $2 = game name (the display name, e.g. "Super Mario Bros.")
    $3 = system name (e.g. "nes", "fba", "naomi")
    $4 = system fullname (e.g. "Nintendo Entertainment System")

This module turns that into a `GameContext` and computes a `policy_key`
that controller-router uses to look up the right priority lists in
controller-policy.toml. An ES-DE custom COLLECTION the ROM belongs to always
wins over the bare system name — a Duck Hunt launch from NES still routes as the
lightgun collection because Duck Hunt is a member (see lib/collections.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    from . import es_collections as collections          # normal: imported as lib.classify
except ImportError:                                       # running this file directly (self-test)
    import es_collections as collections


@dataclass(frozen=True)
class GameContext:
    rom_path: str         # unescaped (backslashes stripped)
    name: str             # ES-DE display name
    system: str           # ES-DE system shortname
    fullname: str         # ES-DE system display name
    collection: str | None  # enabled custom collection the ROM belongs to (or None)
    policy_key: str       # collection display name if a member, else `system`

    @property
    def rom_basename(self) -> str:
        """Filename without directory or extension — matches RetroArch's
        per-game override naming convention (`<core>/<basename>.cfg`)."""
        stem = Path(self.rom_path).stem
        # RetroArch sometimes treats .cue/.gdi as composite; the override
        # filename is just the basename without the final extension, which
        # `stem` already gives us.
        return stem


def _strip_escapes(rom: str) -> str:
    """ES-DE passes spaces escaped: 'Duck\\ Hunt\\ \\(World\\).zip'.
    Mirror the unescape sinden.sh does with `${1//\\\\/}`."""
    return rom.replace("\\", "")


def classify(argv: list[str]) -> GameContext:
    """argv layout matches ES-DE hooks: [_, rom, name, system, fullname].
    Extra args are ignored. Missing args default to empty string."""
    def arg(i): return argv[i] if len(argv) > i else ""

    rom = _strip_escapes(arg(1))
    name = arg(2)
    system = arg(3)
    fullname = arg(4)
    coll = collections.collection_for_rom(rom)
    policy_key = coll if coll else system

    return GameContext(
        rom_path=rom,
        name=name,
        system=system,
        fullname=fullname,
        collection=coll,
        policy_key=policy_key,
    )


if __name__ == "__main__":
    # Quick self-test: feed ROMs (collection member vs not) and print the result.
    tests = [
        # (argv, expected in-a-collection?)
        (["x", "/home/deck/ROMs/arcade/duckhunt.zip", "Duck Hunt",
          "arcade", "Arcade"], True),
        (["x", "/home/deck/ROMs/nes/Super Mario Bros. (World).zip",
          "Super Mario Bros.", "nes", "Nintendo Entertainment System"], False),
        (["x", "/home/deck/ROMs/dreamcast/Silent Scope.cdi", "Silent Scope",
          "dreamcast", "Sega Dreamcast"], True),
    ]
    for argv, expected_member in tests:
        ctx = classify(argv)
        ok = bool(ctx.collection) == expected_member
        status = "OK" if ok else "FAIL"
        print(f"{status}: rom={Path(ctx.rom_path).name!r:55s} "
              f"system={ctx.system!r:12s} collection={ctx.collection!r} "
              f"(member expected {expected_member})  policy_key={ctx.policy_key!r}")
