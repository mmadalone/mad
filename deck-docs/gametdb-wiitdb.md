# GameTDB wiitdb.xml - format facts for the Wii input-style detection

Source: https://www.gametdb.com/wiitdb.zip?LANG=EN (downloaded + parsed 2026-08-04 by
`data/gametdb/generate.py`, which regenerates `data/gametdb/cc_ids.json` and matches the
curated `sideways_ids.json` name list to GameIDs).

- Game element: `<game name="Title (USA) (EN)">` with children `<id>` (6-char GameID),
  `<type/>` (empty for Wii discs; `WiiWare`, `VC-NES`, `GameCube`, `Channel`, `CUSTOM` ...),
  `<region>`, `<locale lang="EN"><title>` (the CLEAN title - the `name` attribute carries
  `(Region) (Langs)` qualifier suffixes; strip trailing parenthesized groups to match).
- Input model: `<input players="N"><control type="..." required="true|false"/></input>`.
  Control types seen: `wiimote`, `nunchuk`, `classiccontroller`, `gamecube`, `motionplus`,
  plus peripherals (`wheel`, `zapper`, `balanceboard`, `guitar`, `drums`, `microphone`,
  `udraw`, `keyboard`, ...). There is NO orientation field - "sideways Wiimote" cannot be
  derived; hence the hand-curated `sideways_ids.json` (community-list seeded; the per-game
  profile picker is the escape hatch).
- 2026-08-04 parse counts: 835 classiccontroller ids, 4049 motion/pointer-only ids,
  4690 nunchuk ids. GameTDB lists an (optional) nunchuk on many sideways-primary games
  (e.g. NSMB Wii SMNE01) - the launch style ladder checks the curated sideways list BEFORE
  the derived nunchuk fact for exactly this reason.
- The EN dump contains DISC titles only for our matching purposes: WiiWare digital releases
  (Mega Man 9/10, the ReBirth trio, Bit.Trip Runner, Fluidity, Art Style: light trax,
  Excitebike: World Rally) are ABSENT - add their 6-char ids to `sideways_ids.json` `ids`
  by hand if those .wads ever land in the library.
- Hack/GameID semantics (established for cc, reused for nunchuk/sideways): a hack keeps the
  retail prefix (first 4 chars) with a custom maker code; membership checks use exact id
  OR `<prefix>01`-retail-sibling rescue.
