# GameTDB (wiitdb) — Wii game database facts

Verified empirically 2026-08-04 (downloads + title-page probes from this Deck).

## Access
- Site + downloads 403 a non-browser client; a `Mozilla/5.0` User-Agent is enough
  (our `lib/dolphin_wii_tdb.refresh()` already sends one).
  Source: https://www.gametdb.com/Wii/Downloads (fetched 2026-08-04).
- Download URL: `https://www.gametdb.com/wiitdb.zip?LANG=XX`. `LANG` is a LANGUAGE filter
  only (EN/JA/FR/DE/ES/IT/NL/PT/RU/KO/ZHCN/ZHTW; `wiitdb.txt` also has `LANG=ORIG`).
  There is NO parameter for including/excluding WiiWare / VC / channel titles.

## WiiWare coverage: effectively ZERO (not a LANG artifact)
- Full `wiitdb.zip` with NO LANG filter, fetched 2026-08-04: 6,715 six-char ids,
  only **6 are W-prefixed (WiiWare)**. The ~900-title WiiWare catalog was never entered.
- Per-title pages for missing ids 404 (checked `Wii/WKFPST` Kung Fu Funk,
  `Wii/WMMEAF` Muscle March) — no record at all, so no per-title fetch fallback either.
- Consequence: WiiWare input-style detection CANNOT be dynamic; curated id overlays are
  the only option (same as the sideways list — GameTDB has no orientation field at all).
- Dolphin wiki (the only other per-game controls source) is behind Anubis proof-of-work
  anti-bot (checked 2026-08-04) — intentionally blocked for programs; do not build on it.

## Our derived cache (`lib/dolphin_wii_tdb`)
- `data/gametdb/cc_ids.json` (bundled) / `~/.local/share/mad/gametdb/cc_ids.json` (user) is
  REGENERATED WHOLESALE by `refresh()` — never hand-edit it; curated additions must live in
  separate overlay files (e.g. `data/gametdb/sideways_ids.json`).
- 2026-07-31 snapshot: 835 classiccontroller ids, 4,049 motion-only, 4,690 nunchuk.
- `<input><control type=…>` types seen: `wiimote`, `nunchuk`, `motionplus`,
  `classiccontroller`, `gamecube`. `nunchuk` is listed for OPTIONAL nunchuk use too
  (any `required` value) — e.g. lightgun games list it.
