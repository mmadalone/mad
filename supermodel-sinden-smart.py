#!/usr/bin/env python3
"""
Smart launcher for the DirtBagXon Supermodel (Sinden/ManyMouse) fork.

Each launch:
  1. Walks sysfs to find each Sinden by USB PID (0x0f38 = firmware Player 1,
     0x0f39 = firmware Player 2) and its current USB path.
  2. Runs `./supermodel -print-inputs` to capture the live ManyMouse
     enumeration order (Sindens come in pairs; other mice may interleave).
  3. Pairs each physical Sinden with its MOUSE# pair using USB-path order
     (lower path = earlier ManyMouse pair; empirically verified).
  4. Within each pair, the LOWER MOUSE# is the gun's Mouse interface
     (the one with BTN_LEFT — the trigger button).
  5. Detects X-Arcade joystick indices (Xbox 360 Wireless Receiver entries)
     so Start/Coin bindings track the current JOY# slot.
  6. Rewrites only the input-binding lines of Supermodel.ini in-place.
  7. Execs the real Supermodel.

Pass --info to just print detection results without launching.
"""
import glob
import os
import re
import subprocess
import sys

SM_DIR = "/home/deck/Emulation/emulators/supermodel-linux-sinden"
INI = f"{SM_DIR}/Config/Supermodel.ini"

# Sinden USB PIDs as configured in firmware (last two hex chars of 0x16C0:0F3X)
PID_FIRMWARE_P1 = "38"
PID_FIRMWARE_P2 = "39"

# X-Arcade Tankstick in Xbox mode shows up as "Xbox 360 Wireless Receiver"
# in evdev but as "Xbox 360 Wireless Controller" in SDL2. Match on "Xbox 360".
XARCADE_PATTERN = "Xbox 360"


def find_sinden_paths():
    """Map firmware PID -> USB device path (e.g. '3-1.2.4.4.2')."""
    paths = {}
    for evt_dev in glob.glob('/sys/class/input/event*/device'):
        try:
            real = os.path.realpath(evt_dev)
        except OSError:
            continue
        m = re.search(r'16C0:0F([0-9A-F]{2})', real, re.IGNORECASE)
        if not m:
            continue
        pid = m.group(1).upper()
        # Walk path components from the deepest, take the last segment
        # matching `\d+-[0-9.]+` (no interface suffix)
        for part in reversed(real.split('/')):
            if re.fullmatch(r'\d+-[0-9.]+', part):
                paths.setdefault(pid, part)
                break
    return paths


def run_print_inputs():
    """Run supermodel -print-inputs and return its stdout+stderr."""
    env = {**os.environ,
           "LD_LIBRARY_PATH": f"{SM_DIR}/libs:" + os.environ.get("LD_LIBRARY_PATH", "")}
    try:
        return subprocess.check_output(
            [f"{SM_DIR}/supermodel", "-print-inputs"],
            cwd=SM_DIR, env=env, stderr=subprocess.STDOUT, timeout=20, text=True
        )
    except subprocess.CalledProcessError as e:
        return e.output or ""
    except subprocess.TimeoutExpired:
        return ""


def parse_mouse_enum(text):
    """Return list of (MOUSE# (1-based), name)."""
    mice = []
    for line in text.splitlines():
        m = re.match(r'^#(\d+):\s*(.+)$', line)
        if m:
            mice.append((int(m.group(1)), m.group(2).strip()))
    return mice


def enumerate_sdl_joysticks():
    """Use SDL2 to enumerate joysticks. Returns list of (JOY#, name) 1-based.
    SDL enumerates differently from /dev/input/jsN (filters motion sensors,
    reorders by HID class), so this is the only correct source."""
    try:
        import ctypes
        sdl = ctypes.CDLL("libSDL2-2.0.so.0")
        sdl.SDL_Init.restype = ctypes.c_int
        sdl.SDL_NumJoysticks.restype = ctypes.c_int
        sdl.SDL_JoystickNameForIndex.restype = ctypes.c_char_p
        sdl.SDL_JoystickNameForIndex.argtypes = [ctypes.c_int]

        if sdl.SDL_Init(0x00000200) != 0:  # SDL_INIT_JOYSTICK
            return []
        try:
            joys = []
            for i in range(sdl.SDL_NumJoysticks()):
                nb = sdl.SDL_JoystickNameForIndex(i)
                name = nb.decode('utf-8', 'replace') if nb else ''
                joys.append((i + 1, name))
            return joys
        finally:
            sdl.SDL_Quit()
    except Exception as e:
        print(f"[smart-launcher] SDL enum failed: {e}", file=sys.stderr)
        return []


def find_xarcade_joys(sdl_joys):
    """Filter SDL joystick list for X-Arcade Tankstick (Xbox 360 alias)."""
    return [n for n, name in sdl_joys if XARCADE_PATTERN in name]


def patch_ini(p1_mouse, p2_mouse, xarcade_joys, dualsense_joys):
    """Rewrite only specific InputXXX lines in Supermodel.ini, preserve rest."""
    new = {}
    if p1_mouse is not None and p2_mouse is not None:
        # Lost World — gun1/gun2
        new['InputGunX']            = f'"MOUSE{p1_mouse}_XAXIS"'
        new['InputGunY']            = f'"MOUSE{p1_mouse}_YAXIS"'
        new['InputTrigger']         = f'"MOUSE{p1_mouse}_LEFT_BUTTON"'
        new['InputOffscreen']       = f'"MOUSE{p1_mouse}_RIGHT_BUTTON"'
        new['InputGunX2']           = f'"MOUSE{p2_mouse}_XAXIS"'
        new['InputGunY2']           = f'"MOUSE{p2_mouse}_YAXIS"'
        new['InputTrigger2']        = f'"MOUSE{p2_mouse}_LEFT_BUTTON"'
        new['InputOffscreen2']      = f'"MOUSE{p2_mouse}_RIGHT_BUTTON"'
        # Ocean Hunter / LA Machineguns — analog_gun1/analog_gun2
        new['InputAnalogGunX']          = f'"MOUSE{p1_mouse}_XAXIS"'
        new['InputAnalogGunY']          = f'"MOUSE{p1_mouse}_YAXIS"'
        new['InputAnalogTriggerLeft']   = f'"MOUSE{p1_mouse}_LEFT_BUTTON"'
        new['InputAnalogTriggerRight']  = f'"MOUSE{p1_mouse}_RIGHT_BUTTON"'
        new['InputAnalogGunX2']         = f'"MOUSE{p2_mouse}_XAXIS"'
        new['InputAnalogGunY2']         = f'"MOUSE{p2_mouse}_YAXIS"'
        new['InputAnalogTriggerLeft2']  = f'"MOUSE{p2_mouse}_LEFT_BUTTON"'
        new['InputAnalogTriggerRight2'] = f'"MOUSE{p2_mouse}_RIGHT_BUTTON"'

    # Build per-player Start/Coin bindings. Different devices have different
    # button-number conventions, so we bind per device (NOT multi-bind by
    # button# across all JOYs):
    #   X-Arcade Tankstick (Xbox 360 mode): BUTTON8=Start, BUTTON7=Coin
    #   DualSense (hid-playstation):        BUTTON7=Options ("Start"),
    #                                       BUTTON5=Share ("Select"/Coin)
    # Sinden joysticks and Steam Deck are intentionally left unbound to
    # avoid confusion with the gun's trigger/pump events.
    starts1, starts2 = [], []
    coins1, coins2 = [], []
    if xarcade_joys:
        if len(xarcade_joys) >= 1:
            starts1.append(f"JOY{xarcade_joys[0]}_BUTTON8")
            coins1.append(f"JOY{xarcade_joys[0]}_BUTTON7")
        if len(xarcade_joys) >= 2:
            starts2.append(f"JOY{xarcade_joys[1]}_BUTTON8")
            coins2.append(f"JOY{xarcade_joys[1]}_BUTTON7")
    if dualsense_joys:
        if len(dualsense_joys) >= 1:
            starts1.append(f"JOY{dualsense_joys[0]}_BUTTON7")
            coins1.append(f"JOY{dualsense_joys[0]}_BUTTON5")
        if len(dualsense_joys) >= 2:
            starts2.append(f"JOY{dualsense_joys[1]}_BUTTON7")
            coins2.append(f"JOY{dualsense_joys[1]}_BUTTON5")

    def join_bindings(key_fallback, joy_list):
        parts = [key_fallback] + joy_list
        return '"' + ','.join(parts) + '"'

    new['InputStart1'] = join_bindings('KEY_1', starts1)
    new['InputStart2'] = join_bindings('KEY_2', starts2)
    new['InputCoin1']  = join_bindings('KEY_5', coins1)
    new['InputCoin2']  = join_bindings('KEY_6', coins2)

    if not new:
        return

    with open(INI) as f:
        lines = f.readlines()

    out = []
    for line in lines:
        m = re.match(r'^(\w+)\s*=', line)
        if m and m.group(1) in new:
            key = m.group(1)
            tail = re.search(r'(\s*;.*)$', line)
            comment = tail.group(1) if tail else ''
            out.append(f'{key} = {new[key]}{comment}\n')
        else:
            out.append(line)

    with open(INI, 'w') as f:
        f.writelines(out)


def detect():
    """Run detection. Returns (p1_mouse, p2_mouse, xarcade_joys, info_lines)."""
    info = []
    paths = find_sinden_paths()
    info.append(f"Sinden USB paths: {paths}")

    p1_mouse = p2_mouse = None
    if len(paths) == 2:
        text = run_print_inputs()
        mice = parse_mouse_enum(text)
        sinden_idxs = sorted([n for n, name in mice if 'SindenLightgun' in name])
        info.append(f"ManyMouse enum has {len(mice)} mice; Sindens at {sinden_idxs}")

        if len(sinden_idxs) == 4:
            first_pair = sinden_idxs[:2]
            second_pair = sinden_idxs[2:]
            # Lower USB path -> first ManyMouse pair
            pids_sorted = sorted(paths.keys(), key=lambda p: paths[p])
            pid_to_mouse = {
                pids_sorted[0]: first_pair[0],   # lower of pair = Mouse interface (has BTN_LEFT)
                pids_sorted[1]: second_pair[0],
            }
            info.append(f"PID->MOUSE# mapping: {pid_to_mouse}")
            p1_mouse = pid_to_mouse.get(PID_FIRMWARE_P1)
            p2_mouse = pid_to_mouse.get(PID_FIRMWARE_P2)
    else:
        info.append(f"Skipping ManyMouse detection (expected 2 Sindens, found {len(paths)})")

    sdl_joys = enumerate_sdl_joysticks()
    info.append(f"SDL joysticks: {sdl_joys}")
    xarcade = find_xarcade_joys(sdl_joys)
    dualsense = [n for n, name in sdl_joys if 'DualSense' in name]
    info.append(f"X-Arcade JOY# slots: {xarcade}")
    info.append(f"DualSense JOY# slots: {dualsense}")

    return p1_mouse, p2_mouse, xarcade, dualsense, info


def main():
    args = sys.argv[1:]

    info_mode = '--info' in args
    if info_mode:
        args.remove('--info')

    p1_mouse, p2_mouse, xarcade_joys, dualsense_joys, info_lines = detect()
    for ln in info_lines:
        print(f"[smart-launcher] {ln}", file=sys.stderr)
    print(f"[smart-launcher] P1 firmware (PID {PID_FIRMWARE_P1}) -> MOUSE{p1_mouse}", file=sys.stderr)
    print(f"[smart-launcher] P2 firmware (PID {PID_FIRMWARE_P2}) -> MOUSE{p2_mouse}", file=sys.stderr)

    if info_mode:
        return

    patch_ini(p1_mouse, p2_mouse, xarcade_joys, dualsense_joys)
    print("[smart-launcher] Supermodel.ini input bindings updated", file=sys.stderr)

    env = {**os.environ,
           "LD_LIBRARY_PATH": f"{SM_DIR}/libs:" + os.environ.get("LD_LIBRARY_PATH", "")}
    os.chdir(SM_DIR)
    os.execvpe(f"{SM_DIR}/supermodel", [f"{SM_DIR}/supermodel"] + args, env)


if __name__ == "__main__":
    main()
