"""Read-only slot/profile preview for hands-off standalone backends.

Moved VERBATIM from router-config-gui.py App._standalone_profile_preview +
App._short_dev (MAD native-panel phase 0, R4) — pure config-file reads, no Tk.
Used by the Tk Preview page (via re-import) and the mad-backend daemon.
"""
from __future__ import annotations

import os
import re

from .mad_config import KNOWN_PADS


def short_dev(name):
    """Short label for a Cemu <display_name> (raw evdev names are long → blob)."""
    n = (name or "").lower()
    if "wii" in n and "pro" in n:
        return "Wii U Pro"
    if "dualsense" in n:
        return "DualSense"
    if "dualshock" in n or "ps4" in n:
        return "DualShock 4"
    if "360" in n or "xbox" in n:
        return "Xbox 360"
    if "steam deck" in n:
        return "Steam Deck"
    return name[:16] if name else ""


def standalone_profile_preview(be, merged, devs=None):
    """Read-only Preview for hands-off standalone backends (cemu/eden/rpcs3): the profile loaded on
    each player slot + its device, read from the ACTIVE config files. Profile name (if
    chosen) comes from [backends.<be>].slot_profiles; the device is read live from the
    slot file so it can't lie. MAD never reads/writes the named profile files here."""
    bcfg = merged.get("backends", {}).get(be, {})
    sp = bcfg.get("slot_profiles", {}) or {}
    rows = []   # (slot label, display text, icon-device name) → rendered with a pad icon
    if be == "cemu":
        cdir = os.path.expanduser(bcfg.get("config_dir", "~/.config/Cemu/controllerProfiles"))
        for s in range(8):
            dev = ""
            try:
                txt = open(os.path.join(cdir, f"controller{s}.xml"),
                           encoding="utf-8", errors="replace").read()
                md = re.search(r"<display_name>([^<]*)</display_name>", txt)
                dev = md.group(1).strip() if md else ""
            except OSError:
                pass
            prof = sp.get(str(s))
            if not (dev or prof):
                continue
            short = short_dev(dev)
            rows.append((f"C{s + 1}", prof or short or "(empty)", short or "genericgamepad"))
    elif be == "eden":
        try:
            body = open(os.path.expanduser(bcfg.get("config_file", "~/.config/eden/qt-config.ini")),
                        encoding="utf-8", errors="replace").read()
        except OSError:
            body = ""
        for p in range(8):
            conn = re.search(rf"player_{p}_connected=(\w+)", body)
            connected = bool(conn and conn.group(1) == "true")
            prof = sp.get(str(p))
            if not (connected or prof):
                continue
            dev = ""
            mg = re.search(rf'player_{p}_button_a="[^"]*guid:([0-9a-fA-F]{{32}})', body)
            if mg:
                g = mg.group(1)
                try:
                    vid = int(g[10:12] + g[8:10], 16)
                    pid = int(g[18:20] + g[16:18], 16)
                    dev = KNOWN_PADS.get(f"{vid:04x}:{pid:04x}", f"{vid:04x}:{pid:04x}")
                except ValueError:
                    dev = ""
            rows.append((f"P{p + 1}", prof or dev or ("on" if connected else "off"),
                         dev or "genericgamepad"))
    elif be == "rpcs3":
        # PS3 — read RPCS3's global input yml; show every non-Null player + its device.
        try:
            body = open(os.path.expanduser(bcfg.get(
                "config_file", "~/.config/rpcs3/input_configs/global/Default.yml")),
                encoding="utf-8", errors="replace").read()
        except OSError:
            body = ""
        for p in range(1, 8):
            blk = re.search(rf"Player {p} Input:\n(.*?)(?=\nPlayer \d+ Input:|\Z)", body, re.S)
            if not blk:
                continue
            mh = re.search(r'Handler:\s*"?([^"\n]+?)"?\s*$', blk.group(1), re.M)
            md = re.search(r'Device:\s*"?([^"\n]*?)"?\s*$', blk.group(1), re.M)
            # RPCS3 serialises these single-quoted (Handler: 'Null', Device: 'DualSense Pad #1');
            # strip both quote styles so the "Null" skip below matches and labels render clean.
            handler = mh.group(1).strip().strip("'\"") if mh else ""
            dev = md.group(1).strip().strip("'\"") if md else ""
            if not handler or handler == "Null":
                continue
            rows.append((f"P{p}", dev or handler, short_dev(handler)))
    # NOTE: pcsx2 (PS2) is intentionally NOT handled here. PCSX2 binds by SDL *index* with no
    # stable device identity, and the index stored in PCSX2.ini is PCSX2's own emulog-calibrated
    # numbering, which does not match MAD's live SDL enumeration — so resolving it here always
    # failed ("no PlayStation pad"). preview_cmds._route_one previews PS2 from the router's real
    # would-bind pads (switch_bind._resolve_pads) instead.
    if not rows:
        return ("text", "hands-off — uses the emulator's own config")
    return ("pads", rows)
