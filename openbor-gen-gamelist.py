#!/usr/bin/env python3
"""Generate ES-DE gamelist.xml for the OpenBOR system.

Writes ~/ES-DE/gamelists/openbor/gamelist.xml with one <game> per .openbor
manifest found in the ROM dir, so a newly added game shows up in ES-DE.

Metadata precedence, highest first:
  1. what the gamelist ALREADY says (a scraper's work, or your own edits)
  2. the enrichment JSON, openbor-metadata.json
  3. the hand-curated CURATED table below
Pass --refresh-metadata to drop rule 1 and re-impose 2 and 3 over the existing
entries. ES-DE's own fields (playcount/playtime/lastplayed/favorite/...) are
always carried across untouched.

Re-runnable and non-destructive: with no arguments, running this twice in a row
produces a byte-identical file.
"""
import json
import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.proc_guard import abort_if_esde_running  # noqa: E402
from lib import fsutil, esde_settings  # noqa: E402

HOME = os.path.expanduser("~")
ROM_DIR = "/run/media/deck/1tbDeck/ROMs/openbor"
# esde_settings.APPDATA honors $ESDE_APPDATA_DIR (default ~/ES-DE) so a relocated
# ES-DE install writes the gamelist where ES-DE actually reads it.
OUT = str(esde_settings.APPDATA / "gamelists" / "openbor" / "gamelist.xml")
ENRICH = f"{HOME}/Emulation/tools/launchers/openbor-metadata.json"

# Hand-curated baseline. year="" means "unknown — let enrichment fill it".
CURATED = {
    "AvengersUnitedBattleForce": ("Avengers: United Battle Force", "Beat 'em up", "1-4", "Unknown", "",
        "OpenBOR beat 'em up starring Marvel's Avengers, brawling through waves of villains. Community-made fan game built on the OpenBOR engine."),
    "BDD_The_Revenge_v.9": ("Battletoads & Double Dragon: The Revenge", "Beat 'em up", "1-2", "Unknown", "",
        "Fan-made OpenBOR sequel that crosses over the Battletoads and Double Dragon casts in side-scrolling combat."),
    "Contrav2": ("Contra v2", "Run and gun", "1-2", "Unknown", "",
        "OpenBOR run-and-gun based on Konami's Contra series, with run-and-shoot action across themed stages."),
    "DD_FINAL": ("Double Dragon: Final", "Beat 'em up", "1-2", "Unknown", "",
        "OpenBOR fan game in the Double Dragon series — the Lee brothers fight through street-brawling stages."),
    "DD_III": ("Double Dragon III", "Beat 'em up", "1-2", "Unknown", "",
        "OpenBOR remake/reimagining of Double Dragon III, a classic side-scrolling beat 'em up."),
    "DD_Reloaded_Alternate_5.1.1": ("Double Dragon Reloaded (Alternate) v5.1.1", "Beat 'em up", "1-2", "Unknown", "",
        "An alternate build of the long-running Double Dragon Reloaded OpenBOR fan game."),
    "DD_Remix": ("Double Dragon: Remix", "Beat 'em up", "1-2", "Unknown", "",
        "OpenBOR remix of Double Dragon, blending classic moves and stages with fan-made additions."),
    "Dungeons_and_Dragons_-_Animated_Series": ("Dungeons & Dragons: The Animated Series", "Beat 'em up", "1-4", "Unknown", "",
        "OpenBOR brawler inspired by Capcom's D&D arcade games and the 1980s Dungeons & Dragons cartoon."),
    "evildead": ("Evil Dead", "Beat 'em up", "1-2", "Unknown", "",
        "OpenBOR beat 'em up based on the Evil Dead films — fight the Deadites as Ash."),
    "GHDC": ("Guardian Heroes: Director's Cut", "Beat 'em up", "1-2", "Unknown", "",
        "OpenBOR tribute to Treasure's Sega Saturn classic Guardian Heroes, a side-scrolling beat 'em up with RPG elements."),
    "Golden_Axe_Genesis_v3.0_Build_4086": ("Golden Axe Genesis", "Beat 'em up", "1-2", "Unknown", "",
        "OpenBOR fan game in the Golden Axe vein — hack-and-slash fantasy beat 'em up."),
    "Golden_Axe_Myth": ("Golden Axe: Myth", "Beat 'em up", "1-2", "Unknown", "",
        "Acclaimed OpenBOR fan game that serves as a prequel to Sega's Golden Axe, with expanded fantasy stages and bosses."),
    "Golden_Axe_Returns": ("Golden Axe: Returns", "Beat 'em up", "1-2", "Unknown", "",
        "OpenBOR continuation of the Golden Axe fantasy beat 'em up series."),
    "GUG": ("Godzilla, Ultraman & Gamera", "Beat 'em up", "1-2", "Unknown", "",
        "Kaiju-themed OpenBOR brawler featuring Godzilla, Ultraman and Gamera battling through monster-movie stages."),
    "he-man-pc": ("He-Man: Masters of the Universe", "Beat 'em up", "1-2", "Unknown", "",
        "OpenBOR beat 'em up based on He-Man and the Masters of the Universe."),
    "Jennifer_By_MasterDerico": ("Jennifer", "Beat 'em up", "1-2", "MasterDerico", "",
        "Original OpenBOR beat 'em up by community author MasterDerico."),
    "jll": ("Justice League Legacy", "Beat 'em up", "1-4", "Unknown", "",
        "OpenBOR brawler featuring DC Comics' Justice League heroes against a roster of villains."),
    "Justice_League_United": ("Justice League United", "Beat 'em up", "1-4", "Unknown", "",
        "OpenBOR beat 'em up starring the DC Justice League."),
    "killbill": ("Kill Bill", "Beat 'em up", "1-2", "Unknown", "",
        "OpenBOR beat 'em up based on Tarantino's Kill Bill — slice through the Crazy 88 as the Bride."),
    "Maximun_Carnage_Returns": ("Maximum Carnage Returns", "Beat 'em up", "1-2", "Unknown", "",
        "Spider-Man OpenBOR brawler inspired by the Maximum Carnage storyline; team up to take down Carnage and his symbiotes."),
    "MFA2": ("Marvel: First Alliance 2", "Beat 'em up", "1-4", "Unknown", "",
        "Sequel OpenBOR brawler with a large roster of Marvel heroes fighting through comic-book stages."),
    "MIWv100": ("Marvel: Infinity War", "Beat 'em up", "1-4", "Unknown", "",
        "OpenBOR beat 'em up pitting Marvel heroes against Thanos and his forces."),
    "Neon_Lightning_Force_1.5_demo": ("Neon Lightning Force (Demo)", "Beat 'em up", "1-2", "Unknown", "",
        "Demo build of an original OpenBOR beat 'em up, Neon Lightning Force."),
    "Silver_Nights_Crusaders": ("Silver Nights Crusaders", "Beat 'em up", "1-2", "Unknown", "",
        "Original OpenBOR side-scrolling beat 'em up."),
    "simpsons": ("The Simpsons", "Beat 'em up", "1-4", "Thatcher Productions", "",
        "OpenBOR remake/tribute of Konami's classic The Simpsons arcade beat 'em up, by Thatcher Productions."),
    "TMNT_Recolored_and_Extended": ("TMNT: Recolored and Extended", "Beat 'em up", "1-4", "Unknown", "",
        "An expanded, recolored OpenBOR Teenage Mutant Ninja Turtles brawler in the Turtles in Time tradition."),
    "TMNT_RP_1_1_5": ("TMNT: Rescue-Palooza!", "Beat 'em up", "1-4", "Merso13", "",
        "A huge, content-packed OpenBOR Teenage Mutant Ninja Turtles fan game with a massive playable roster."),
    "UDD_ver3.0": ("Ultimate Double Dragon", "Beat 'em up", "1-2", "Unknown", "",
        "OpenBOR fan game compiling and expanding the Double Dragon series into one ultimate brawler."),
    "vsr_kottono_edition": ("Vendetta: Super Recargado (Kottono Edition)", "Beat 'em up", "1-2", "Unknown", "",
        "OpenBOR brawler based on Konami's Vendetta / Crime Fighters, in an enhanced Kottono edition."),
    "wargems": ("Marvel Super Heroes: War of the Gems", "Beat 'em up", "1-2", "Unknown", "",
        "OpenBOR beat 'em up inspired by Marvel Super Heroes: War of the Gems, collecting the Infinity Gems across stages."),
}


def iso(year):
    y = (year or "").strip()
    if len(y) == 4 and y.isdigit():
        return f"{y}0101T000000"
    return None


# Fields ES-DE owns and writes back itself. This generator rebuilds the gamelist
# from scratch every run, so without carrying these across a regen would silently
# wipe the user's play history and favourites (11 of 36 OpenBOR games had play
# stats when this was added). ES-DE only ever writes them on exit, and the
# abort_if_esde_running guard above means it is not running, so the on-disk
# gamelist is the authoritative copy to read them back from.
ESDE_OWNED = ("playcount", "playtime", "lastplayed", "favorite", "completed",
              "broken", "hidden", "kidgame", "nogamecount", "rating")

# Descriptive fields. These are NOT ours to overwrite once something else has filled
# them in: a scraper enriched 12 of these games on 2026-08-01 (exact release dates,
# publisher, corrected player counts and developers - better than our curated table),
# and a plain regen wiped all of it to "fix" one game. So an existing entry's own
# values win by default, and --refresh-metadata is required to re-impose the curated
# table. `publisher` is here because scrapers set it and this generator never does.
DESCRIPTIVE = ("name", "desc", "developer", "publisher", "genre", "players",
               "releasedate")


def existing_entries(path):
    """stem -> {tag: text} for everything worth preserving in an existing gamelist."""
    out = {}
    if not os.path.isfile(path):
        return out
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        print(f"WARN: existing {path} is not parseable ({e}); nothing preserved")
        return out
    for g in root.findall("game"):
        stem = os.path.basename((g.findtext("path") or "").strip())
        if not stem:
            continue
        keep = {t: g.findtext(t) for t in ESDE_OWNED + DESCRIPTIVE
                if g.findtext(t) is not None and (g.findtext(t) or "").strip()}
        if keep:
            out[stem] = keep
    return out


def main():
    refresh = "--refresh-metadata" in sys.argv[1:]
    unknown = [a for a in sys.argv[1:] if a != "--refresh-metadata"]
    if unknown:
        print(f"usage: {os.path.basename(sys.argv[0])} [--refresh-metadata]")
        sys.exit(2)
    if abort_if_esde_running("regenerate the OpenBOR gamelist"):
        sys.exit(1)
    enrich = {}
    if os.path.isfile(ENRICH):
        try:
            data = json.load(open(ENRICH))
            for row in data:
                if row.get("folder"):
                    enrich[row["folder"]] = row
            print(f"Loaded enrichment for {len(enrich)} games from {ENRICH}")
        except Exception as e:
            print(f"WARN: could not read {ENRICH}: {e}")

    if not os.path.isdir(ROM_DIR):
        print(f"ERROR: ROM dir {ROM_DIR} not found (SD not mounted?); refusing to "
              f"overwrite {OUT} with an empty gamelist.")
        sys.exit(1)
    manifests = sorted(f for f in os.listdir(ROM_DIR) if f.endswith(".openbor"))
    if not manifests:
        print(f"ERROR: no .openbor manifests in {ROM_DIR}; refusing to overwrite "
              f"{OUT} with an empty gamelist.")
        sys.exit(1)
    keep = existing_entries(OUT)
    if keep:
        stats = sum(1 for v in keep.values() if "playcount" in v)
        print(f"Read {len(keep)} existing entries ({stats} with play stats)")
        if not refresh:
            print("Existing name/desc/developer/publisher/genre/players/releasedate are "
                  "KEPT (pass --refresh-metadata to re-impose the curated table)")
    root = ET.Element("gameList")
    reused = 0
    for man in manifests:
        folder = man[:-len(".openbor")]
        name, genre, players, dev, year, desc = CURATED.get(
            folder, (folder, "Beat 'em up", "1-2", "Unknown", "",
                     "OpenBOR beat 'em up fan game."))
        e = enrich.get(folder, {})
        if e.get("name"):
            name = e["name"]
        if e.get("genre"):
            genre = e["genre"]
        if e.get("players"):
            players = e["players"]
        if e.get("developer") and e["developer"].lower() != "unknown":
            dev = e["developer"]
        if e.get("releaseyear"):
            year = e["releaseyear"]
        if e.get("desc"):
            desc = e["desc"]
        rd = iso(year)

        prev = keep.get(man, {})
        # Whatever is already in the gamelist wins unless explicitly refreshing, so a
        # regen to pick up a NEW game cannot quietly undo scraped metadata.
        if prev and not refresh:
            if prev.get("name"):        name = prev["name"]
            if prev.get("desc"):        desc = prev["desc"]
            if prev.get("developer"):   dev = prev["developer"]
            if prev.get("genre"):       genre = prev["genre"]
            if prev.get("players"):     players = prev["players"]
            if prev.get("releasedate"): rd = prev["releasedate"]
            reused += 1

        g = ET.SubElement(root, "game")
        ET.SubElement(g, "path").text = f"./{man}"
        ET.SubElement(g, "name").text = name
        ET.SubElement(g, "desc").text = desc
        if dev and dev.lower() != "unknown":
            ET.SubElement(g, "developer").text = dev
        if prev.get("publisher"):        # scrapers set this; we never generate one
            ET.SubElement(g, "publisher").text = prev["publisher"]
        ET.SubElement(g, "genre").text = genre
        ET.SubElement(g, "players").text = players
        if rd:
            ET.SubElement(g, "releasedate").text = rd
        for tag in ESDE_OWNED:
            if prev.get(tag) is not None:
                ET.SubElement(g, tag).text = prev[tag]
    if reused:
        print(f"Kept existing metadata for {reused} games; "
              f"{len(manifests) - reused} written from the curated table")

    # This regenerates the whole gamelist from scratch. ES-DE-owned fields
    # (favourites/playcount/...) are carried across above; back the old one up
    # anyway so any hand edit is recoverable.
    if os.path.isfile(OUT):
        ts = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy(OUT, f"{OUT}.bak-{ts}")
        print(f"Backed up existing gamelist → {OUT}.bak-{ts}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    # ET.indent, NOT minidom. minidom's toprettyxml needs a follow-up pass to strip its
    # blank filler lines, and that pass cannot tell a filler line from a BLANK LINE
    # INSIDE A DESCRIPTION - it silently welded the paragraphs of two scraped
    # descriptions together (2026-08-01). ET.indent only touches whitespace around
    # elements that have children, so leaf text is preserved byte-for-byte.
    ET.indent(root, space="    ")
    xml = ET.tostring(root, encoding="unicode", xml_declaration=True)
    fsutil.atomic_write(OUT, xml + "\n")
    print(f"Wrote {len(manifests)} games to {OUT}")


if __name__ == "__main__":
    main()
