#!/usr/bin/env python3
"""Generate ES-DE gamelist.xml for the OpenBOR system.

Writes ~/ES-DE/gamelists/openbor/gamelist.xml with one <game> per .openbor
manifest found in the ROM dir. Display metadata comes from the CURATED table
below; if an enrichment JSON exists (produced from the metadata workflow), its
fields are merged on top (workflow wins for developer/releaseyear/desc when
present and non-empty).

Re-runnable: regenerates the whole gamelist from scratch each time.
"""
import json
import os
import xml.etree.ElementTree as ET
from xml.dom import minidom

HOME = os.path.expanduser("~")
ROM_DIR = "/run/media/deck/1tbDeck/ROMs/openbor"
OUT = f"{HOME}/ES-DE/gamelists/openbor/gamelist.xml"
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


def main():
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

    manifests = sorted(f for f in os.listdir(ROM_DIR) if f.endswith(".openbor"))
    root = ET.Element("gameList")
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

        g = ET.SubElement(root, "game")
        ET.SubElement(g, "path").text = f"./{man}"
        ET.SubElement(g, "name").text = name
        ET.SubElement(g, "desc").text = desc
        if dev and dev.lower() != "unknown":
            ET.SubElement(g, "developer").text = dev
        ET.SubElement(g, "genre").text = genre
        ET.SubElement(g, "players").text = players
        rd = iso(year)
        if rd:
            ET.SubElement(g, "releasedate").text = rd

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    xml = minidom.parseString(ET.tostring(root, "utf-8")).toprettyxml(indent="    ")
    # drop minidom's blank lines
    xml = "\n".join(l for l in xml.splitlines() if l.strip())
    with open(OUT, "w") as f:
        f.write(xml + "\n")
    print(f"Wrote {len(manifests)} games to {OUT}")


if __name__ == "__main__":
    main()
