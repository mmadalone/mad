#!/usr/bin/env bash
# game-start: family x context controller seating for Wii U (Cemu). lib/cemu_seat seats each pad's
# assigned profile ([backends.cemu.profile_map.<context>]) into its controllerN.xml, re-pinned, and
# reverts on exit (game-end/09-cemu-input-restore.sh). With seating_enabled=false it delegates to the
# legacy single-slot handheld swap (today's behaviour). apply() heals any orphaned seat first.
# $1=ROM $2=name $3=system $4=fullname
# The ROM selects this game's per-game family overrides ([backends.cemu.pergame.<titleid>.<context>]).
# It is passed RAW: ES-DE backslash-escapes it, and cemu_games.titleid_for_rom unescapes centrally
# (the same place handheld_res.apply does) -- do NOT add a `${1//\\/}` strip here, that duplication
# is exactly what left 08-cemu-res.sh broken for every spaced filename. An unknown rom is harmless:
# per-game degrades to the global map.
case "$3" in wiiu) ;; *) exit 0 ;; esac
RT="$HOME/Emulation/tools/launchers"
python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import cemu_seat; print('mad-cemu:', cemu_seat.apply(sys.argv[1] if len(sys.argv) > 1 else None))" "$1" 2>/dev/null
exit 0
