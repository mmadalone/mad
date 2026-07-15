# MUGEN / Ikemen GO symlinks — Rule #4 exception (OK'd)

Source: user sign-off 2026-07-15 (review finding #22). This file IS the "explicitly
OK'd" record that Rule #4 requires ("No symlinks in the game/data layout ... unless
explicitly OK'd").

## What symlinks and why

`mugen.sh` (ikemen launch mode) `cd`s into the game folder under
`~/ROMs/mugen/<game>/` and symlinks in the assets Ikemen GO needs but a Mugen 1.0
game folder does not ship:

- `external/`  -> `$ikemen_home/external`  (Ikemen GO's Lua scripts)
- `data/*`, `font/*` -> `$ikemen_home/{data,font}/*`  (per-file, only what the game lacks)
- `font/<win>.ttf` -> system DejaVuSans.ttf  (substitute for Windows TTFs a motif
  references; Ikemen GO panics if the file is missing)

Each is guarded by `[[ ! -e ... ]]` so a REAL game file is never overwritten — only
absent (or dangling) entries are (re)linked.

## Why symlinks and not real copies

The Ikemen GO engine assets + fonts are SHARED across every mugen game. Copying them
into each `~/ROMs/mugen/<game>/` would duplicate the same engine tree (and the DejaVu
fonts) per game — many extra copies of identical data, and a maintenance trap when the
engine updates. Symlinks keep one source of truth. This is a deliberate,
sign-off-backed exception to Rule #4, scoped to the mugen/ikemen game folders only.

## Hardening (review #22)

`ln -s` -> `ln -sf` at all three sites: if Ikemen GO or the fonts move, the leftover
link becomes dangling; `[[ ! -e ]]` (which dereferences) then sees it as absent and
tries to relink, but plain `ln -s` FAILS on the existing link file and never repairs.
`ln -sf` force-replaces the dangling link. Real game files stay protected by the
`[[ ! -e ]]` guard (a present real file short-circuits before `ln` runs).

## Caveat (unchanged)

`deck-backup.sh` archives the ROM tree, so a restore to a Deck where Ikemen / the
fonts live elsewhere yields dangling links until the next mugen launch regenerates
them (now self-healing thanks to `ln -sf`). Acceptable: launch is idempotent.
