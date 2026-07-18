"""The Steam Deck virtual pad's sdl2 GAMEPLAY map -- one small, dependency-free table.

RetroArch's sdl2 joypad driver keys the built-in Deck pad by SDL GameController SEMANTIC indices
(a=0 b=1 x=2 y=3 back=4 start=6 L3=7 R3=8 L1=9 R1=10, dpad 11-14; axes leftx=0 lefty=1 rightx=2
righty=3 trigL=4 trigR=5). These exact values are RetroArch's OWN capture (Set All Controls ->
config/fbneo_libretro.cfg) on this rig, not a guess.

This is the single source for `ra_profiles.SDL_SEMANTIC_TABLE` (re-keyed from `input_player1_<suffix>`
to the bare `<suffix>` the resolver wants). It lives in its own leaf so it carries NO heavy imports
(evdev, policy, ...) -- `ra_profiles` is on the launch hot path and must import it cheaply.

HATS ARE DEAD UNDER sdl2 (verified in sdl_joypad.c v1.22.2: the controller branch sets num_hats=0),
so the d-pad is reachable ONLY as buttons 11-14 -- that is why this table gives left_btn="13" while
the udev map gives "h0left". The two number spaces are NOT interchangeable and must never be
"simplified" into one shared table.
"""
from __future__ import annotations

_GAMEPAD: dict[str, str] = {
    "input_player1_a_btn": "0", "input_player1_b_btn": "1",
    "input_player1_x_btn": "2", "input_player1_y_btn": "3",
    "input_player1_select_btn": "4", "input_player1_start_btn": "6",
    "input_player1_l3_btn": "7", "input_player1_r3_btn": "8",
    "input_player1_l_btn": "9", "input_player1_r_btn": "10",
    "input_player1_up_btn": "11", "input_player1_down_btn": "12",
    "input_player1_left_btn": "13", "input_player1_right_btn": "14",
    "input_player1_l2_axis": "+4", "input_player1_r2_axis": "+5",
    "input_player1_l_x_plus_axis": "+0", "input_player1_l_x_minus_axis": "-0",
    "input_player1_l_y_plus_axis": "+1", "input_player1_l_y_minus_axis": "-1",
    "input_player1_r_x_plus_axis": "+2", "input_player1_r_x_minus_axis": "-2",
    "input_player1_r_y_plus_axis": "+3", "input_player1_r_y_minus_axis": "-3",
}
