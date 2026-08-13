"""Per-emulator CONFIG + DATA map for the granular "emucfg" backup/restore category (P7).

Curated, NOT a filesystem classifier: each EMULATOR is a tile that drills into a small set of tickable
GROUPS (Config / Controller / Per-game / Keys / Saves & memcards / Textures & mods / ...), and each group is
a hand-listed set of on-disk roots under $HOME. This owns the stuff the per-game path (RetroArch + mGBA
saves, keyed by ROM stem) and the wholesale "precious" cloud backup do NOT reach granularly: shared memory
cards, opaque console saves, and above all the emulator CONFIG itself (which nothing else backs up).

rel = "emucfg/<path relative to $HOME, FRONT-DOOR side>" - the TRUE restore key. We use the LOGICAL $HOME
side (never realpath) so a front-door symlink (e.g. Cemu Emulation/saves/Cemu/saves -> mlc01, or a flatpak
data dir) stays lexically under $HOME and inside the allowlist; the copy follows the symlink on read/write,
exactly like the shipped saves/bios categories.

SIZE-GATING: the huge dirs (PCSX2 textures 39G, Dolphin Load 33G, ...) are emitted as a SINGLE folder row
(kind "folder") so we never enumerate millions of files into a manifest; their size is a bounded
best-effort lower bound so the slow live RPC stays responsive. EVERY group defaults to TICKED, the big ones
included: a backup that silently omits the expensive-to-rebuild data is the failure mode that matters here,
and the leaf lists each group with its size so skipping one is a deliberate untick (settled 2026-08-13).
test_group_defaults.py fences the explain text against the flag, so the two can never disagree again.

RESTORE SAFETY: because the emucfg category anchors at $HOME (broad), a restore is bounded by ALLOWED_PREFIXES
(derived from this table so it can never drift) - a forged manifest rel outside every emulator dir is
rejected. The per-emulator running guard keys off `backend` (proc_guard name). Read-only.
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from . import backup_debris

REL_PREFIX = "emucfg/"

# Canonical group key -> {label, explain, order}. A BACKUP source stores only the group KEY (in extra.group),
# so this lets the restore browse show the same nice label/explanation as the live view without any device.
GROUP_INFO = {
    "config":       {"label": "Config", "order": 0,
                     "explain": "The emulator's own settings (video, audio, folders, hotkeys)."},
    "controller":   {"label": "Controller", "order": 1,
                     "explain": "Your controller button mappings and input profiles for this emulator."},
    "pergame":      {"label": "Per-game settings", "order": 2,
                     "explain": "Per-game override settings (one file per game/serial)."},
    "keys":         {"label": "Keys", "order": 3,
                     "explain": "Console keys required to run games (prod/title keys). Small and high-value."},
    "firmware":     {"label": "Firmware", "order": 3,
                     "explain": "Decrypted console firmware (e.g. RPCS3 dev_flash) - required to run games."},
    "system":       {"label": "System files & BIOS", "order": 3,
                     "explain": "BIOS/firmware the emulator keeps in its OWN system folder (outside the BIOS "
                                "store, so the BIOS category cannot reach it)."},
    "saves":        {"label": "Saves & memory cards", "order": 4,
                     "explain": "Memory cards and console save data this emulator keeps outside the ROM."},
    "graphicpacks": {"label": "Graphic packs", "order": 5,
                     "explain": "Downloaded graphic packs / enhancement packs (re-downloadable)."},
    "textures":     {"label": "Textures & HD packs", "order": 6,
                     "explain": "Custom/HD texture packs. Can be VERY large (tens of GB) - ticked by default, untick to skip."},
    "mods":         {"label": "Mods & add-ons", "order": 7,
                     "explain": "Game mods and add-ons. Can be large - ticked by default, untick to skip."},
    "shader":       {"label": "Shader cache", "order": 11,
                     "explain": "Compiled shader/pipeline cache (rebuilt by playing; avoids first-run "
                                "stutter). Can be large - ticked by default, untick to skip."},
    "wii_nand":     {"label": "Wii NAND", "order": 8,
                     "explain": "The full Wii system NAND (channels + Wii save data). Large - ticked by default."},
    "hdd":          {"label": "Xbox hard disk image", "order": 9,
                     "explain": "The whole Xbox HDD image (holds all saves). Large - ticked by default."},
    "overlays":     {"label": "Overlays & shaders", "order": 10,
                     "explain": "RetroArch overlays and shader presets (re-downloadable)."},
    "states":       {"label": "Save states", "order": 12,
                     "explain": "Save-state snapshots (bigger than saves, regenerable - ticked by default)."},
    "playlists":    {"label": "Playlists", "order": 13,
                     "explain": "Your RetroArch playlists and 'recently played' history."},
    "cheats":       {"label": "Cheats & patches", "order": 14,
                     "explain": "Cheat and patch files you added for this emulator."},
}


def _ginfo(key: str) -> dict:
    return GROUP_INFO.get(key, {"label": key.title(), "explain": "", "order": 99})


# A group SPEC enumerates on-disk rows. mode:
#   "file"   - a single file at `path`.
#   "glob"   - files matching `pat` directly in dir `path` (non-recursive).
#   "walk"   - every file under dir `path` (recursive, follows symlinks; a `seen` guard breaks cycles).
#   "folder" - ONE folder row for `path` (do NOT enumerate; huge opt-in dirs). Size is bounded.
# `exclude` = fnmatch patterns tested against EACH path component (so "cache" skips a cache subdir and
# "*.bak*" skips backup debris by basename). All `path`s are relative to $HOME (POSIX).
#
# Each emulator: key (tile + manifest system axis), label, backend (proc_guard name for the running guard),
# art_key (a representative console for the tile art; falls back to the backup-emu icon), probe (a path whose
# existence means the emulator is present), and groups [{key, default, specs:[...]}].
EMULATORS: list = [
    {"key": "pcsx2", "label": "PCSX2", "backend": "pcsx2", "art_key": "ps2",
     "probe": ".config/PCSX2",
     "groups": [
        {"key": "config", "default": True, "specs": [
            {"path": ".config/PCSX2/inis", "mode": "glob", "pat": "*.ini", "exclude": ["*.bak*"]},
            {"path": ".config/PCSX2/cheats", "mode": "walk"}]},
        {"key": "controller", "default": True, "specs": [
            {"path": ".config/PCSX2/inputprofiles", "mode": "glob", "pat": "*.ini"}]},
        {"key": "pergame", "default": True, "specs": [
            {"path": ".config/PCSX2/gamesettings", "mode": "glob", "pat": "*.ini"}]},
        {"key": "saves", "default": True, "specs": [
            {"path": "Emulation/saves/pcsx2/saves", "mode": "walk"},
            {"path": "Emulation/saves/pcsx2/states", "mode": "glob", "pat": "*.p2s"}]},
        {"key": "textures", "default": True, "specs": [
            {"path": "Emulation/storage/pcsx2/textures", "mode": "folder"}]},
        {"key": "shader", "default": True, "specs": [
            {"path": "Emulation/storage/pcsx2/cache", "mode": "glob", "pat": "vulkan_*"}]},
     ]},
    {"key": "pcsx2x6", "label": "PCSX2 (Namco)", "backend": "pcsx2x6", "art_key": "ps2",
     "probe": "Applications/pcsx2x6/PCSX2x6",
     "groups": [
        {"key": "config", "default": True, "specs": [
            {"path": "Applications/pcsx2x6/PCSX2x6/inis", "mode": "glob", "pat": "*.ini",
             "exclude": ["*.bak*"]}]},
        {"key": "saves", "default": True, "specs": [
            {"path": "Applications/pcsx2x6/PCSX2x6/memcards", "mode": "walk"}]},
        {"key": "textures", "default": True, "specs": [
            {"path": "Applications/pcsx2x6/PCSX2x6/textures", "mode": "folder"}]},
     ]},
    {"key": "dolphin", "label": "Dolphin", "backend": "dolphin", "art_key": "gc",
     "probe": ".var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu",
     "groups": [
        {"key": "config", "default": True, "specs": [
            {"path": ".var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu", "mode": "glob",
             "pat": "*.ini", "exclude": ["*.bak*"]}]},
        {"key": "controller", "default": True, "specs": [
            {"path": ".var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu/Profiles", "mode": "walk"}]},
        {"key": "pergame", "default": True, "specs": [
            {"path": ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu/GameSettings", "mode": "glob",
             "pat": "*.ini"}]},
        {"key": "saves", "default": True, "specs": [
            {"path": ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu/GC", "mode": "walk"}]},
        {"key": "wii_nand", "default": True, "specs": [
            {"path": ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu/Wii", "mode": "folder"}]},
        {"key": "textures", "default": True, "specs": [
            {"path": ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu/Load", "mode": "folder"}]},
     ]},
    {"key": "ryujinx", "label": "Ryujinx", "backend": "ryujinx", "art_key": "switch",
     "probe": ".config/Ryujinx",
     "groups": [
        {"key": "config", "default": True, "specs": [
            {"path": ".config/Ryujinx/Config.json", "mode": "file"},
            {"path": ".config/Ryujinx/profiles", "mode": "walk"}]},
        {"key": "keys", "default": True, "specs": [
            {"path": ".config/Ryujinx/system/prod.keys", "mode": "file"},
            {"path": ".config/Ryujinx/system/title.keys", "mode": "file"}]},
        {"key": "saves", "default": True, "specs": [
            {"path": ".config/Ryujinx/bis/user/save", "mode": "walk"},
            {"path": ".config/Ryujinx/bis/user/saveMeta", "mode": "walk"}]},
        {"key": "mods", "default": True, "specs": [
            {"path": ".config/Ryujinx/mods", "mode": "folder"}]},
        {"key": "shader", "default": True, "specs": [
            {"path": ".config/Ryujinx/games", "mode": "folder"}]},
     ]},
    {"key": "eden", "label": "Eden", "backend": "eden", "art_key": "switch",
     "probe": ".config/eden",
     "groups": [
        {"key": "config", "default": True, "specs": [
            {"path": ".config/eden/qt-config.ini", "mode": "file"},
            {"path": ".config/eden/input", "mode": "walk"},
            {"path": ".config/eden/custom", "mode": "walk"}]},
        {"key": "keys", "default": True, "specs": [
            {"path": ".local/share/eden/keys/prod.keys", "mode": "file"},
            {"path": ".local/share/eden/keys/title.keys", "mode": "file"}]},
        {"key": "saves", "default": True, "specs": [
            {"path": ".local/share/eden/nand/user/save", "mode": "walk", "exclude": ["cache"]}]},
        {"key": "mods", "default": True, "specs": [
            {"path": ".local/share/eden/load", "mode": "folder"}]},
        {"key": "shader", "default": True, "specs": [
            {"path": ".local/share/eden/shader", "mode": "folder"}]},
     ]},
    {"key": "citron", "label": "Citron", "backend": "citron", "art_key": "switch",
     "probe": ".config/citron",
     "groups": [
        {"key": "config", "default": True, "specs": [
            {"path": ".config/citron/qt-config.ini", "mode": "file"},
            {"path": ".config/citron/input", "mode": "walk"},
            {"path": ".config/citron/custom", "mode": "walk"}]},
        {"key": "keys", "default": True, "specs": [
            {"path": ".local/share/citron/keys/prod.keys", "mode": "file"},
            {"path": ".local/share/citron/keys/title.keys", "mode": "file"}]},
        {"key": "saves", "default": True, "specs": [
            {"path": ".local/share/citron/nand/user/save", "mode": "walk", "exclude": ["cache"]}]},
        {"key": "mods", "default": True, "specs": [
            {"path": ".local/share/citron/load", "mode": "folder"}]},
        {"key": "shader", "default": True, "specs": [
            {"path": ".local/share/citron/shader", "mode": "folder"}]},
     ]},
    {"key": "cemu", "label": "Cemu", "backend": "cemu", "art_key": "wiiu",
     "probe": ".config/Cemu",
     "groups": [
        {"key": "config", "default": True, "specs": [
            {"path": ".config/Cemu/settings.xml", "mode": "file"},
            {"path": ".config/Cemu/gameProfiles", "mode": "walk"}]},
        {"key": "controller", "default": True, "specs": [
            {"path": ".config/Cemu/controllerProfiles", "mode": "walk"}]},
        {"key": "keys", "default": True, "specs": [
            {"path": ".local/share/Cemu/keys.txt", "mode": "file"}]},
        {"key": "saves", "default": True, "specs": [
            {"path": "Emulation/saves/Cemu/saves", "mode": "walk"}]},
        {"key": "graphicpacks", "default": True, "specs": [
            {"path": ".local/share/Cemu/graphicPacks", "mode": "folder"}]},
        {"key": "shader", "default": True, "specs": [
            {"path": ".cache/Cemu/shaderCache", "mode": "folder"}]},
     ]},
    {"key": "rpcs3", "label": "RPCS3", "backend": "rpcs3", "art_key": "ps3",
     "probe": ".config/rpcs3",
     "groups": [
        {"key": "config", "default": True, "specs": [
            {"path": ".config/rpcs3/config.yml", "mode": "file"},
            {"path": ".config/rpcs3/vfs.yml", "mode": "file"},
            {"path": ".config/rpcs3/custom_configs", "mode": "walk"},
            {"path": ".config/rpcs3/patches", "mode": "walk"}]},
        {"key": "controller", "default": True, "specs": [
            {"path": ".config/rpcs3/input_configs", "mode": "walk"}]},
        # PS3 firmware: dev_flash lives in RPCS3's own config dir, NOT under the BIOS store, so ONLY the
        # emucfg category can reach it (P10). Folder mode = one row for the whole ~190MB tree.
        {"key": "firmware", "default": True, "specs": [
            {"path": ".config/rpcs3/dev_flash", "mode": "folder"},
            {"path": ".config/rpcs3/dev_flash2", "mode": "folder"}]},
        {"key": "saves", "default": True, "specs": [
            {"path": "Emulation/storage/rpcs3/dev_hdd0/home/00000001/savedata", "mode": "walk"},
            {"path": "Emulation/storage/rpcs3/dev_hdd0/home/00000001/trophy", "mode": "walk"}]},
        {"key": "shader", "default": True, "specs": [
            {"path": ".cache/rpcs3", "mode": "folder"}]},
     ]},
    {"key": "ppsspp", "label": "PPSSPP", "backend": "ppsspp", "art_key": "psp",
     "probe": ".var/app/org.ppsspp.PPSSPP/config/ppsspp",
     "groups": [
        {"key": "config", "default": True, "specs": [
            {"path": ".var/app/org.ppsspp.PPSSPP/config/ppsspp/PSP/SYSTEM/ppsspp.ini", "mode": "file"},
            {"path": ".var/app/org.ppsspp.PPSSPP/config/ppsspp/PSP/SYSTEM/controls.ini", "mode": "file"}]},
        {"key": "saves", "default": True, "specs": [
            {"path": ".var/app/org.ppsspp.PPSSPP/config/ppsspp/PSP/SAVEDATA", "mode": "walk"},
            {"path": ".var/app/org.ppsspp.PPSSPP/config/ppsspp/PSP/PPSSPP_STATE", "mode": "walk"}]},
     ]},
    {"key": "xemu", "label": "xemu", "backend": "xemu", "art_key": "xbox",
     "probe": ".var/app/app.xemu.xemu/data/xemu/xemu",
     "groups": [
        {"key": "config", "default": True, "specs": [
            {"path": ".var/app/app.xemu.xemu/data/xemu/xemu/xemu.toml", "mode": "file"},
            {"path": "Emulation/storage/xemu/eeprom.bin", "mode": "file"}]},
        {"key": "hdd", "default": True, "specs": [
            {"path": "Emulation/storage/xemu/xbox_hdd.qcow2", "mode": "file"}]},
     ]},
    {"key": "flycast", "label": "Flycast", "backend": "flycast", "art_key": "dreamcast",
     "probe": ".var/app/org.flycast.Flycast/config/flycast",
     "groups": [
        {"key": "config", "default": True, "specs": [
            {"path": ".var/app/org.flycast.Flycast/config/flycast/emu.cfg", "mode": "file"},
            {"path": ".var/app/org.flycast.Flycast/config/flycast/mappings", "mode": "walk"}]},
        {"key": "saves", "default": True, "specs": [
            {"path": ".var/app/org.flycast.Flycast/data/flycast", "mode": "walk"}]},
     ]},
    {"key": "duckstation", "label": "DuckStation", "backend": "duckstation", "art_key": "psx",
     "probe": ".var/app/org.duckstation.DuckStation/config/duckstation",
     "groups": [
        {"key": "config", "default": True, "specs": [
            {"path": ".var/app/org.duckstation.DuckStation/config/duckstation/settings.ini",
             "mode": "file"}]},
        {"key": "saves", "default": True, "specs": [
            {"path": "Emulation/saves/duckstation/saves", "mode": "walk"},
            {"path": "Emulation/saves/duckstation/states", "mode": "walk"}]},
     ]},
    {"key": "supermodel", "label": "Supermodel", "backend": "supermodel", "art_key": "model3",
     "probe": ".supermodel",
     "groups": [
        {"key": "config", "default": True, "specs": [
            {"path": ".supermodel/Config", "mode": "walk"}]},
        {"key": "saves", "default": True, "specs": [
            {"path": ".supermodel/NVRAM", "mode": "walk"}]},
     ]},
    {"key": "mame", "label": "MAME", "backend": "mame", "art_key": "arcade",
     "probe": ".mame",
     "groups": [
        {"key": "config", "default": True, "specs": [
            {"path": ".mame/mame.ini", "mode": "file"},
            {"path": ".mame/ini", "mode": "walk"},
            {"path": ".mame/cfg", "mode": "walk"}]},
        {"key": "controller", "default": True, "specs": [
            {"path": ".mame/ctrlr", "mode": "walk"}]},
        {"key": "saves", "default": True, "specs": [
            {"path": ".mame/nvram", "mode": "walk"}]},
     ]},
    {"key": "retroarch", "label": "RetroArch", "backend": "retroarch", "art_key": "retroarch",
     "probe": ".var/app/org.libretro.RetroArch/config/retroarch",
     "groups": [
        # CONFIG + system/. The FLAT per-game .srm rides the per-game path (P2/P3); the NESTED per-core saves
        # (arcade NVRAM, Flycast VMU, ...) do NOT, so a wholesale saves/ (+ states/ + playlists/) group is
        # added below. The per-core/per-game override tree (config/, ~19k tiny files incl. remaps + core
        # options) is ONE folder row so it never floods the manifest; re-downloadable dirs (assets/cores/
        # database/downloads/thumbnails) are not listed. system/ (RA's own BIOS/firmware) is NOT under the BIOS
        # store (~/Emulation/bios), so the BIOS category cannot reach it - only emucfg can (P10); own group.
        {"key": "config", "default": True, "specs": [
            {"path": ".var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg", "mode": "file"},
            {"path": ".var/app/org.libretro.RetroArch/config/retroarch/retroarch-core-options.cfg",
             "mode": "file"}]},
        {"key": "pergame", "default": True, "specs": [
            {"path": ".var/app/org.libretro.RetroArch/config/retroarch/config", "mode": "folder"}]},
        {"key": "system", "default": True, "specs": [
            {"path": ".var/app/org.libretro.RetroArch/config/retroarch/system", "mode": "folder"}]},
        # Controller device-bind autoconfigs the MAD router writes (per-pad input profiles) - the gamepad
        # ROUTING for RetroArch. NOT under the per-core config/ tree, so a dedicated folder-row group.
        {"key": "controller", "default": True, "specs": [
            {"path": ".var/app/org.libretro.RetroArch/config/retroarch/autoconfig", "mode": "folder"}]},
        {"key": "overlays", "default": True, "specs": [
            {"path": ".var/app/org.libretro.RetroArch/config/retroarch/overlays", "mode": "folder"},
            {"path": ".var/app/org.libretro.RetroArch/config/retroarch/shaders", "mode": "folder"}]},
        # SAVES: most cores nest per-game saves in a core-subdir (saves/MAME/mame/nvram/<set>/, saves/
        # Flycast/reicast/..., saves/FinalBurn Neo/fbneo/..., saves/opera/per_game/...) that the per-game
        # .srm resolver never reaches, so the whole saves/ tree is backed up wholesale here (arcade NVRAM,
        # Flycast VMU, 3DO memory, Saturn Kronos/Yaba). Overlaps the per-game .srm by design (P7). states/
        # (bigger, regenerable) is ticked by default; playlists = your 'recently played' lists.
        {"key": "saves", "default": True, "specs": [
            {"path": ".var/app/org.libretro.RetroArch/config/retroarch/saves", "mode": "folder"}]},
        {"key": "states", "default": True, "specs": [
            {"path": ".var/app/org.libretro.RetroArch/config/retroarch/states", "mode": "folder"}]},
        {"key": "playlists", "default": True, "specs": [
            {"path": ".var/app/org.libretro.RetroArch/config/retroarch/playlists", "mode": "folder"}]},
        # cheats/ (per-core .cht) - the per-game RA cheat asset (P13) lives under here, so its dir MUST be an
        # allowlist prefix; also a wholesale opt-in group. (Empty on this device; the prefix exists regardless.)
        {"key": "cheats", "default": True, "specs": [
            {"path": ".var/app/org.libretro.RetroArch/config/retroarch/cheats", "mode": "folder"}]},
     ]},
    {"key": "shadps4", "label": "shadPS4", "backend": "shadps4", "art_key": "ps4",
     "probe": ".local/share/shadPS4/config.toml",
     "groups": [
        {"key": "config", "default": True, "specs": [
            {"path": ".local/share/shadPS4/config.toml", "mode": "file"},
            {"path": ".local/share/shadPS4/qt_ui.ini", "mode": "file"}]},
        {"key": "controller", "default": True, "specs": [
            {"path": ".local/share/shadPS4/inputConfig", "mode": "walk"}]},
        {"key": "saves", "default": True, "specs": [
            {"path": ".local/share/shadPS4/savedata", "mode": "walk"},
            {"path": ".local/share/shadPS4/custom_trophy", "mode": "walk"}]},
        {"key": "cheats", "default": True, "specs": [
            {"path": ".local/share/shadPS4/cheats", "mode": "walk"},
            {"path": ".local/share/shadPS4/patches", "mode": "walk"}]},
        {"key": "shader", "default": True, "specs": [
            {"path": ".local/share/shadPS4/shader", "mode": "folder"}]},
     ]},
    {"key": "hypseus", "label": "Hypseus (LaserDisc)", "backend": "hypseus", "art_key": "daphne",
     "probe": ".hypseus/ram",
     "groups": [
        {"key": "saves", "default": True, "specs": [
            {"path": ".hypseus/ram", "mode": "walk"}]},
        {"key": "config", "default": True, "specs": [
            {"path": "Applications/hypseus-singe/hypinput.ini", "mode": "file"}]},
     ]},
]

# key -> proc_guard backend name (for the per-emulator running guard). Derived from the table.
BACKEND = {e["key"]: e["backend"] for e in EMULATORS}
# key -> friendly label (for a backup's manifest system_label + restore refusal message).
LABELS = {e["key"]: e["label"] for e in EMULATORS}
# key -> representative console art key (for the tile art; "" -> the C++ falls back to the emu-config icon).
ART_KEYS = {e["key"]: e.get("art_key", "") for e in EMULATORS}


def label_for(emulator: str) -> str:
    return LABELS.get(emulator, emulator)


def art_key_for(emulator: str) -> str:
    return ART_KEYS.get(emulator, "")

# Restore allowlist + per-rel emulator OWNER, both derived from the specs so they can NEVER drift. A "file"
# spec contributes its parent dir; a dir/glob/walk/folder spec contributes itself. ALLOWED_PREFIXES bounds a
# restore to the emulator dirs this map knows (a forged rel outside every emulator dir is rejected);
# _PREFIX_OWNERS maps each prefix back to the OWNING emulator's proc_guard backend, for the game-first
# per-emulator running guard (which cannot use the ES-DE system, 1:many for switch).
def _prefix_owners() -> tuple[dict, dict]:
    """(prefix_owners, exact_owners). A dir-ish spec (glob/walk/folder) contributes its dir as a PREFIX -
    any rel under it is allowed. A "file" spec contributes the EXACT file path only, NOT its parent dir: a
    config file can sit beside an emulator's launch BINARY (e.g. hypinput.ini next to hypseus.bin), and a
    parent-dir prefix would let a forged manifest overwrite that binary (code execution on restore). So a
    "file" spec allows exactly its own rel and nothing else."""
    prefixes: dict = {}
    exact: dict = {}
    for e in EMULATORS:
        for g in e["groups"]:
            for spec in g["specs"]:
                p = spec["path"].strip("/")
                if not p:
                    continue
                (exact if spec["mode"] == "file" else prefixes)[p] = e["backend"]
    return prefixes, exact


_PREFIX_OWNERS, _EXACT_OWNERS = _prefix_owners()
ALLOWED_PREFIXES = set(_PREFIX_OWNERS)   # dir-ish spec prefixes (file specs are exact-matched, see below)


def rel_allowed(rel: str) -> bool:
    """True iff `rel` ("emucfg/<path>") targets something this map knows how to back up - a rel UNDER a
    dir-ish spec, or the EXACT rel of a "file" spec. The restore path calls this so a forged/foreign manifest
    can never write outside an emulator's backable set (not merely inside its dir). `rel` must ALSO pass the
    generic traversal checks (done separately in _plan_restore_item); this only bounds the ROOT."""
    if not (isinstance(rel, str) and rel.startswith(REL_PREFIX)):
        return False
    rem = rel[len(REL_PREFIX):].strip("/")
    if not rem or any(part in ("", ".", "..") for part in rem.split("/")):
        return False
    return rem in _EXACT_OWNERS or any(rem == p or rem.startswith(p + "/") for p in _PREFIX_OWNERS)


def backend_for(emulator: str) -> str:
    """The proc_guard name for an emulator tile key (identity if not in the table)."""
    return BACKEND.get(emulator, emulator)


def owner_backend_for_rel(rel: str) -> str | None:
    """The proc_guard backend of the emulator that OWNS a per-game emucfg rel ("emucfg/<path>"), by
    LONGEST-prefix match over the spec dirs. The game-first restore's running guard uses this because the
    ES-DE `system` is 1:many for switch (ryujinx/eden/citron) - only the rel says which emulator owns the
    file. None if the rel matches no emulator dir (it would already be rejected by rel_allowed)."""
    if not (isinstance(rel, str) and rel.startswith(REL_PREFIX)):
        return None
    rem = rel[len(REL_PREFIX):].strip("/")
    if not rem:
        return None
    if rem in _EXACT_OWNERS:                 # a "file" spec's exact rel
        return _EXACT_OWNERS[rem]
    best_be = None
    best_len = -1
    for p, be in _PREFIX_OWNERS.items():
        if (rem == p or rem.startswith(p + "/")) and len(p) > best_len:
            best_len = len(p)
            best_be = be
    return best_be


def _home() -> Path:
    return Path.home()


def _excluded(rel_within: str, excludes) -> bool:
    """True iff `rel_within` is OS/VCS debris (always) OR any path component matches an exclude pattern (so
    'cache' skips a cache subdir and '*.bak*' skips this spec's backup debris by basename)."""
    parts = rel_within.split("/")
    # OS/VCS junk is NEVER a real emulator file -> always excluded, independent of the spec's own list.
    if backup_debris.is_debris_file(parts[-1]) or any(backup_debris.is_debris_dir(p) for p in parts):
        return True
    if not excludes:
        return False
    for part in parts:
        for pat in excludes:
            if fnmatch.fnmatch(part, pat):
                return True
    return False


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


# Stop walking a huge opt-in folder after this many files (a pathological-case guard, NOT a normal cap).
# Real emu texture/mod dirs are ~100k files max and stat-walk in well under 1s (the dir metadata is cached),
# so this is set high enough to report the ACCURATE size (a 39 GB / 97k-file PCSX2 textures dir walks in
# ~0.7s); only a truly absurd multi-million-file dir would hit it.
_FOLDER_SIZE_BUDGET = 500000


def _bounded_folder_size(path: str) -> int:
    """Recursive size of a folder, capped at _FOLDER_SIZE_BUDGET files (a hang guard). Returns a lower bound
    only in the pathological over-cap case; for every real emu dir it is the accurate total."""
    total = 0
    seen = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not backup_debris.is_debris_dir(d)]  # not backed up -> not counted
        for fn in files:
            if backup_debris.is_debris_file(fn):   # keep the folder-row size honest with what gets copied
                continue
            total += _size(os.path.join(root, fn))
            seen += 1
            if seen >= _FOLDER_SIZE_BUDGET:
                return total
    return total


def _rel_for(home: Path, abspath: str) -> str:
    """emucfg/<abspath relative to $HOME, POSIX>. abspath is always built by joining $HOME with a spec path,
    so the relpath is a plain forward-slash sub-path (never realpath'd - front-door symlinks stay logical)."""
    rel = os.path.relpath(abspath, str(home))
    return REL_PREFIX + rel.replace(os.sep, "/")


def _enum_spec(home: Path, spec: dict) -> list:
    """Rows [{rel, name, size, kind}] for one spec. Follows symlinks on a walk (front-door dirs) with a
    realpath `seen` guard against cycles. A 'folder' spec is a SINGLE row (not enumerated)."""
    base = os.path.join(str(home), spec["path"])
    mode = spec["mode"]
    excludes = spec.get("exclude")
    rows: list = []
    if mode == "folder":
        if os.path.isdir(base) or os.path.isfile(base):
            kind = "folder" if os.path.isdir(base) else "file"
            sz = _bounded_folder_size(base) if kind == "folder" else _size(base)
            rows.append({"rel": _rel_for(home, base), "name": os.path.basename(base.rstrip("/")),
                         "size": sz, "kind": kind})
        return rows
    if mode == "file":
        if os.path.isfile(base) and not _excluded(os.path.basename(base), excludes):
            rows.append({"rel": _rel_for(home, base), "name": os.path.basename(base),
                         "size": _size(base), "kind": "file"})
        return rows
    if mode == "glob":
        d = Path(base)
        if d.is_dir():
            for f in sorted(d.glob(spec.get("pat", "*"))):
                if f.is_file() and not _excluded(f.name, excludes):
                    rows.append({"rel": _rel_for(home, str(f)), "name": f.name,
                                 "size": _size(str(f)), "kind": "file"})
        return rows
    # mode == "walk": every file under the dir, following front-door symlinks (cycle-guarded).
    if os.path.isdir(base):
        seen: set = set()
        for dirpath, dirs, files in os.walk(base, followlinks=True):
            rp = os.path.realpath(dirpath)
            if rp in seen:
                dirs[:] = []
                continue
            seen.add(rp)
            dirs[:] = [d for d in dirs if not backup_debris.is_debris_dir(d)]  # skip .git/__pycache__/...
            for fn in sorted(files):
                src = os.path.join(dirpath, fn)
                rel_within = os.path.relpath(src, base)
                if _excluded(rel_within, excludes):
                    continue
                rows.append({"rel": _rel_for(home, src), "name": fn, "size": _size(src), "kind": "file"})
    return rows


def _group_rows(home: Path, group: dict, skip_folders: bool = False) -> list:
    """Rows for one group. skip_folders drops the "folder"-mode specs (the huge opt-in dirs), so the tile
    screen (list_emulators) never size-walks tens of GB of textures/mods/NAND even when they default ON -
    their exact size is still walked + shown in the group view (list_files)."""
    rows: list = []
    seen_rel: set = set()
    for spec in group["specs"]:
        if skip_folders and spec["mode"] == "folder":
            continue
        for r in _enum_spec(home, spec):
            if r["rel"] in seen_rel:
                continue
            seen_rel.add(r["rel"])
            rows.append(r)
    return rows


def _present(home: Path, emulator: dict) -> bool:
    return os.path.exists(os.path.join(str(home), emulator["probe"]))


def list_emulators() -> list:
    """The per-emulator tiles: [{key, label, art_key, backend, present, count, size}]. Only PRESENT
    emulators. `size`/`count` cover the DEFAULT-ON NON-folder groups only (config/controller/keys/saves/
    per-game). Every "folder"-mode group is SKIPPED here (skip_folders=True) - even default-on ones like
    textures/mods/NAND/shader/firmware/system - so the tile screen never size-walks tens of GB and stays
    fast; each folder group's exact size is walked + shown in the per-emulator drill-down (list_files). So the
    tile total UNDER-reports a default backup that includes big folder groups; that is deliberate, for speed."""
    home = _home()
    out: list = []
    for e in EMULATORS:
        if not _present(home, e):
            continue
        count = 0
        size = 0
        for g in e["groups"]:
            if not g.get("default"):
                continue
            for r in _group_rows(home, g, skip_folders=True):   # never size-walk the huge folder groups here
                count += 1
                size += r["size"]
        out.append({"key": e["key"], "label": e["label"], "art_key": e.get("art_key", ""),
                    "backend": e["backend"], "present": True, "count": count, "size": size})
    out.sort(key=lambda t: t["label"].lower())   # tiles read alphabetically by the visible label
    return out


def _emulator(key: str) -> dict | None:
    for e in EMULATORS:
        if e["key"] == key:
            return e
    return None


def list_files(emulator: str) -> list:
    """One emulator's GROUPS: [{key, label, explain, default_ticked, present, count, size,
    files:[{rel,name,size,kind}]}]. Absent groups have present False (nothing to back up). The huge opt-in
    groups are a single folder row with a bounded size."""
    home = _home()
    e = _emulator(emulator)
    if e is None:
        return []
    out: list = []
    for g in e["groups"]:
        info = _ginfo(g["key"])
        files = _group_rows(home, g)
        out.append({"key": g["key"], "label": info["label"], "explain": info["explain"],
                    "default_ticked": bool(g.get("default")), "present": bool(files),
                    "count": len(files), "size": sum(f["size"] for f in files), "files": files})
    return out
