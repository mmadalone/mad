# Ryubing (Ryujinx) config format, enums, content stores, input routing

Source: Ryubing (GreemDev) source, current `canary` branch, verified against the LIVE
config on this Deck (Ryujinx Canary 1.3.328, `Config.json` version 73).
Mirrors used (canonical git.ryujinx.app is Anubis/PoW-walled to WebFetch):
`github.com/Stella-sea/ryujinx-admin` (canary, CurrentVersion=73, matches live),
`gitea.com/Goodfeat/Ryubing-greemdev` (enums), `codeberg.org/smj2k/Ryujinx` (stores).
Captured 2026-07-04. Keep credentials out; this file is doc cache only.

Plain ASCII only (no em/en dashes, no unicode arrows) per house rule.

## File locations

- Data root (RyujinxDataDir): `~/.config/Ryujinx/`
- Global config: `~/.config/Ryujinx/Config.json` (single flat JSON, ALL keys top-level,
  NO [Section] headers, NO `key\default` twins. Structurally the opposite of Yuzu/Citron ini.)
- Per-game config: `~/.config/Ryujinx/games/<TitleId lowercase x16>/Config.json`
  (on this Deck `games` is a symlink to `/home/deck/Emulation/storage/ryujinx/games/`).
  A per-game file is a COMPLETE Config.json clone, not a delta; it wholly replaces global
  when present. Ryujinx auto-selects it by title id at launch (Program.cs GetDirGameUserConfig).
  DIR CASING IS LOWERCASE ONLY (`titleId.ToString("x16")`). An UPPERCASE games/<TID> dir is
  dead/ignored (a stale one exists here for TOTK from an old MAD write; MAD must write lowercase).
### Per-game MAD model (DIRECT read/write, 2026-07-04)

MAD edits `games/<tid>/Config.json` IN PLACE, exactly like Ryujinx's own per-game config -- NOT via
a sidecar pin-map + launch regen (that older model clobbered any value the user set in Ryujinx that
MAD did not track, e.g. an unpinned `audio_backend`; it also imported a Ryujinx-authored file only
once). Consequences:
- A per-game file is an independent FROZEN snapshot: it does NOT track later global changes (this IS
  Ryujinx's own native behavior). Full interop -- values set in Ryujinx are read + preserved.
- GET renders inherit-aware ("Inherit global" at index 0) by LIVE-diffing the file vs global: a
  managed key present AND != global is an override; else it inherits. No pin-map.
- SET writes the one key into the file (created as a COMPLETE global clone on first use; an
  incomplete old file is topped up with any keys missing vs global so Ryujinx does not reset them to
  compiled defaults). "Inherit global" copies the CURRENT global value in (frozen). When no managed
  override remains a pure clone is removed for a clean inherit, but a file that also holds
  Ryujinx-authored content or a per-game input_config is KEPT (house rule #5, `_clear_state`).
- There is NO launch-time regen; `switch_bind._target` just uses the game's own file if present.
- There is NO per-game INPUT page (removed 2026-07-04). A Ryujinx profile is a device+mapping PIN
  (its `id` is a specific device GUID). MAD's bake copied ONLY the mapping and kept the slot's cloned
  device, and `assign_devices` reassigns devices by the global pads->players order anyway -- so
  picking a profile per player never bound the chosen device. Device -> player is owned by the global
  Controllers -> pads -> players routing. (The GLOBAL profile picker, ryujinx.selector_set key=profile,
  remains; it has the same mapping-only-bake limitation, flagged for review.)
- Citron/Eden KEEP their pin/inherit model: their Yuzu ini has native per-key inherit (`\default`
  twin), so it fits their format. Ryujinx is the odd one where inherit had to be synthetic.

- Named input profiles: `~/.config/Ryujinx/profiles/controller/<name>.json` and
  `profiles/keyboard/<name>.json`.
- Mods: `~/.config/Ryujinx/mods/{contents,exefs_patches,nro_patches}` plus SD path
  `~/.config/Ryujinx/sdcard/atmosphere/...`. Per-title mod dir `mods/contents/<TITLEID UPPERCASE>/`
  (ModLoader matches case-insensitively but the shipped folders are uppercase).
- Keys: `~/.config/Ryujinx/system/{prod.keys,title.keys}`. Switch user accounts: `system/Profiles.json`.

## Config version field

Single top-level int `version` (live global = 73). A per-game games/<tid>/Config.json is an
independent frozen snapshot and can sit at an older version than global; they migrate
independently. (This file previously cited a live per-game copy at version 70 as an example --
checked again 2026-08-13 and there is no per-game Config.json on this device any more;
~/.config/Ryujinx/games/<tid>/ currently holds only gui/, updates.json, and per-game dlc.json/cache
where applicable, no Config.json. Treat the version-70 figure as historical, not current.) A
too-low version migrates UP and is rewritten; an absent/garbage version RESETS to defaults. When
writing, copy the LIVE global's version, never hardcode.

## Settings enums (locked from source, cross-checked live)

Serialization rule: each string enum has `[JsonConverter(TypedStringEnumConverter<T>)]` and
serializes as the EXACT enum member NAME (case-sensitive; a bad token silently reverts to the
index-0 member, NOT an error). Enums WITHOUT that attribute (VSyncMode, MemoryConfiguration)
and plain int fields serialize as the INTEGER. There is no global JsonStringEnumConverter.
For MAD: NAME enums use write_mode "option" (store the token verbatim, exact casing);
INT enums store the integer index.

| key | json | ordered tokens / mapping |
|-----|------|--------------------------|
| graphics_backend | name | Vulkan(0), OpenGl(1)  [casing OpenGl not OpenGL] |
| preferred_gpu | string | "0x<vendor>_0x<device>" PCI id |
| res_scale | int | -1=Custom (uses res_scale_custom float), 1=Native, 2, 3, 4 |
| aspect_ratio | name | Fixed4x3, Fixed16x9, Fixed16x10, Fixed21x9, Fixed32x9, Stretched |
| anti_aliasing | name | None, Fxaa, SmaaLow, SmaaMedium, SmaaHigh, SmaaUltra |
| scaling_filter | name | Bilinear, Nearest, Fsr, Area  (Area is newer) |
| scaling_filter_level | int 0-100 | FSR sharpening (only meaningful when scaling_filter=Fsr) |
| max_anisotropy | float | -1=Auto, else 1/2/4/8/16 |
| backend_threading | name | Auto, Off, On |
| vsync_mode | int | Switch=0, Unbounded=1, Custom=2  (authoritative over legacy enable_vsync bool) |
| enable_custom_vsync_interval | bool | ; custom_vsync_interval int fps (when vsync_mode=Custom) |
| enable_shader_cache | bool | |
| enable_texture_recompression | bool | |
| enable_macro_hle | bool | |
| enable_color_space_passthrough | bool | |
| memory_manager_mode | name | SoftwarePageTable, HostMapped, HostMappedUnsafe |
| enable_ptc | bool | Profiled Persistent Translation Cache |
| enable_low_power_ptc | bool | |
| use_hypervisor | bool | macOS only; false on Linux (JIT is default when off) |
| dram_size | int | MemoryConfiguration: 0=4GiB, 1=6GiB, 2=8GiB, 3=12GiB (4..6 are dev variants) |
| audio_backend | name | Dummy, OpenAl, SoundIo, SDL2  [no SDL3; casing OpenAl, SoundIo] |
| audio_volume | float 0.0-1.0 | |
| system_language | name | Japanese, AmericanEnglish, French, German, Italian, Spanish, Chinese, Korean, Dutch, Portuguese, Russian, Taiwanese, BritishEnglish, CanadianFrench, LatinAmericanSpanish, SimplifiedChinese, TraditionalChinese, BrazilianPortuguese |
| system_region | name | Japan, USA, Europe, Australia, China, Korea, Taiwan |
| system_time_zone | string | IANA tz id (e.g. UTC) |
| system_time_offset | long | seconds added to guest clock |
| match_system_time | bool | |
| docked_mode | bool | THE handheld/docked key. true=docked, false=handheld. Top-level bool, no twin. |
| ignore_missing_services / ignore_applet / skip_user_profiles | bool | |
| enable_internet_access / enable_fs_integrity_checks | bool | |
| check_updates_on_start | bool | |
| update_checker_type | name | Off, PromptAtStartup, CheckInBackground |
| focus_lost_action_type | name | DoNothing, BlockInput, MuteAudio, BlockInputAndMuteAudio, PauseEmulation |
| show_confirm_exit / enable_discord_integration | bool | |
| enable_hardware_acceleration | bool | (System section) |
| controller_type | name | [Flags] None=0, ProController=1, Handheld=2, JoyconPair=4, JoyconLeft=8, JoyconRight=16, ... (per-player, serializes as member name) |

Plenty of GUI-only top-level keys exist (gui_columns, game_dirs, window_startup, etc.);
a writer must PRESERVE them, never set them. Ryujinx rewrites Config.json on exit, so a
full JSON parse -> modify -> dump round-trip is safe (MAD does exactly this via ryujinx_json.py);
take a one-time backup before first write.

## Input model (the important part)

`input_config` is a JSON array of full per-device objects. Live Player1 entry shape:
```
{ left_joycon_stick:{joystick:"Left",invert_stick_x,invert_stick_y,rotate90_cw,stick_button:"LeftStick"},
  right_joycon_stick:{joystick:"Right",...,stick_button:"RightStick"},
  deadzone_left, deadzone_right, range_left, range_right, trigger_threshold,
  motion:{motion_backend:"GamepadDriver",sensitivity,gyro_deadzone,enable_motion},
  rumble:{strong_rumble,weak_rumble,enable_rumble,use_hdrumble},
  led:{enable_led,turn_off_led,use_rainbow,led_color},
  left_joycon:{button_minus,button_l,button_zl,button_sl,button_sr,dpad_up,dpad_down,dpad_left,dpad_right},
  right_joycon:{button_plus,button_r,button_zr,button_sl,button_sr,button_x,button_b,button_y,button_a},
  version:1, backend:"GamepadSDL3", id:"0-00000003-28de-0000-0512-000000026800",
  name:"Steam Deck Controller (0)", controller_type:"ProController",
  player_index:"Player1", enable_dynamic_gamepad_swap:false }
```
Button tokens = GamepadInputId names (A,B,X,Y,LeftShoulder,RightShoulder,LeftTrigger,
RightTrigger,DpadUp/Down/Left/Right,Minus,Plus,Back,Start,LeftStick,RightStick,...,
SingleLeftTrigger0/1,SingleRightTrigger0/1,Unbound). Stick tokens = StickInputId (Left,Right,Unbound).
player_index serializes as a NAME string: Player1..Player8, Handheld, Unknown, Auto.

### Device -> slot binding authority (SOURCE-VERIFIED, current canary)

1. `input_config[].id` WINS. NpadManager.ReloadConfiguration iterates input_config; each
   NpadController opens the pad from `config.Id` UNLESS that player's
   `enable_dynamic_input_swap == true`, in which case it uses
   `player_input_assignments[].devices[].id`. Live config has swap=false everywhere,
   so input_config[].id governs. player_input_assignments is a per-player alternate layer.

2. `use_input_global_config` (System section bool): only matters when a per-game Config.json
   is loaded. false => the game uses the PER-GAME file's own input_config. true => it borrows
   the GLOBAL input_config + player_input_assignments. MAD clones global (which is false) into
   the per-game file, so per-game input is honored. Do NOT flip it true.

3. Backend token: SDL2 driver is gone (only Ryujinx.Input.SDL3 exists, DriverName "SDL3"),
   but `GamepadSDL2` is still an accepted config token (explicit //backcompat); both SDL2 and
   SDL3 deserialize to the same StandardControllerInputConfig, and the pad is matched purely by
   `id` string, not by the backend token. An UNKNOWN backend string throws. So SDL2 still routes,
   but `GamepadSDL3` is the current/future-proof token to write.

4. GamepadId / id format: `"<leadingNumber>-<32hex GUID>"`. SDL3 GenerateGamepadId does
   `"0000" + guid.ToString()[4..]` = zeroes the first 4 hex chars (the SDL name-CRC, bytes 2-3).
   MAD's b[2]=b[3]=0 zeroing produces the SAME guid portion. BUT the leading number CHANGED
   MEANING between SDL2 and SDL3:
     SDL2: leading number = the SDL joystick ENUMERATION INDEX.
     SDL3: leading number = a PER-GUID DUPLICATE COUNTER. First device of a distinct GUID = 0;
           a second device with the identical GUID = 1; etc. (assigned in HandleJoyStickConnected).
   GetGamepad(id) is a pure whole-string lookup; a miss => no pad for that slot.

### MAD routing (FIXED 2026-07-04, Phase C - was partly broken on current canary)

FIXED: `lib/madsrv/ryujinx_cfg.py assign_devices` now writes the per-GUID DUPLICATE RANK as the id's
leading number (not the SDL enum index), PRESERVES the existing backend (both SDL2/SDL3 route by id),
and keeps `player_input_assignments` in lockstep; `switch_bind` snapshots/reverts PIA + input_config.
The original bug (kept here for context): the old code rewrote only input_config[].id, hardcoded
`backend="GamepadSDL2"`, and touched NEITHER player_input_assignments NOR the leading-index
semantics. Result on Canary 1.3.x (pre-fix):
- Single unique controller -> Player1 (enum index 0 == per-GUID rank 0): WORKS.
- A 2nd distinct-model pad, or a Player1 that is not enumeration-0 (multiple pads attached):
  MAD writes leading index >= 1 while SDL3 assigns that unique GUID rank 0 -> STRING MISMATCH
  -> that slot gets no pad. Real regression vs the old SDL2 driver.

To be correct, a writer must:
  1. Set the leading number to a PER-GUID DUPLICATE RANK (0 for the first device of each distinct
     CRC-zeroed GUID, incrementing only for identical-GUID duplicates) instead of the SDL enum index.
     Keep the CRC zeroing (already correct).
  2. Write `backend:"GamepadSDL3"` (SDL2 accepted but flagged backcompat).
  3. Write `player_input_assignments` in lockstep: per player
     `{player_index, enable_dynamic_input_swap:false, devices:[{type:"Controller", id:<same id>, profile_name:null}]}`.
     (Handheld exists in input_config but NOT in player_input_assignments.) Upstream helper:
     PlayerInputAssignmentHelper.CreatePrimaryDevice(InputConfig).
  4. Keep per-game files at `use_input_global_config:false`.

### Named input profiles (the "profile picker")

Profiles are FULL InputConfig objects saved by the GUI (InputViewModel.SaveProfile) to
profiles/controller/<name>.json (or keyboard/). They INCLUDE the identity fields
version/backend/id/name/controller_type/player_index, NOT just the mapping. Profile list in the
picker = file stems + a synthetic leading "Default" (which clears/resets the slot).
A profile picker in MAD must copy ONLY the mapping subtree
(left_joycon_stick, right_joycon_stick, deadzone_left/right, range_left/right, trigger_threshold,
motion, rumble, led, left_joycon, right_joycon) into the target input_config[P], and PRESERVE that
slot's own id/backend/player_index/version (and controller_type unless the profile changes it).
`version` is deliberately NOT copied even though the profile file carries one (see the identity
fields listed above): a saved profile is a frozen snapshot on whatever schema version was current
when it was authored, while the live Config.json slot carries whatever version Ryujinx itself last
migrated it to. Baking the frozen number in would silently re-arm Ryujinx's own input migration
against already-migrated data on every launch after an upgrade, because Ryujinx rewrites Config.json
on exit so the stale number just round-trips forever (lib/ryujinx_profiles.MAP_KEYS implements this
exclusion; phase 5 of the 2026-08-12 audit stopped copying it after this file used to list it as an
optional "[+ version]"). This mirrors Ryujinx's own LoadProfile (load mapping) + LoadDevice
(re-match device by saved id).
`profile_name` also appears in player_input_assignments[].devices[]; whether a non-null profile_name
is RESOLVED at boot or is just a UI label is UNVERIFIED (the deciding code is only in the walled
1.3.x tree). Baking the mapping inline into input_config is correct under either interpretation
(optionally also set profile_name for provenance since input_config already equals the profile).
Cheap on-device test if certainty needed: set one slot's profile_name to an existing profile, blank
that slot's input_config mapping, launch, observe whether the mapping comes from the profile.

## Per-title content stores (all persistable; SOURCE-VERIFIED)

- Mods: `games/<tid-lower>/mods.json` = `{"mods":[{"name","path","enabled":bool}]}`. `enabled` is
  the per-mod toggle (multi). `path` embeds the UPPERCASE title id. Writer:
  ModManagerViewModel.Save (Enabled = SelectedMods.Contains(mod)). Loader type ModLoader Mod{Name,Path,Enabled}.
- Updates: `games/<tid-lower>/updates.json` = `{"selected":"<nsp path or empty>","paths":[...]}`.
  SINGLE-select: selected = the applied update ("" = base game), paths = the known-available list.
  Source TitleUpdateMetadata / TitleUpdateViewModel.Save.
- DLC: `games/<tid-lower>/dlc.json` = JSON ARRAY of containers:
  `[{"path":<container nsp/xci>, "dlc_nca_list":[{"path":<nca>, "title_id":<ulong decimal>, "is_enabled":bool}]}]`.
  MULTI-toggle (per-NCA is_enabled). Source DownloadableContentManagerViewModel /
  DownloadableContentContainer + DownloadableContentNca. Toggling an EXISTING dlc.json is trivial;
  AUTHORING one from scratch needs NCA parsing (LibHac) so only expose toggles when dlc.json exists.
  (title_id likely serializes as a decimal ulong; confirm against a real file before writing.)
- Cheats: PERSISTED as a whitelist at `mods/contents/<TITLEID UPPER>/cheats/enabled.txt` (PLAIN TEXT,
  one entry per line, NOT JSON). Line format `"<BUILDID>-<CheatName>"` where BUILDID = the cheat
  file's stem UPPERCASED (16-hex Atmosphere build id) and CheatName = the `[Section]` header inside.
  Reader: ModLoader.EnableCheats (if enabled.txt absent, every cheat is loaded but INERT).
  Writer: CheatWindow.Save (File.WriteAllLines of the ticked cheats' BuildIdKey).
  Match key in TamperMachine = exactly "{buildId}-{name}". There is NO json index of cheats;
  to build the picklist, enumerate `mods/contents/<TID>/cheats/*.txt`, uppercase each stem as BUILDID,
  parse `[Section]` headers as names. Buildable, pure text. Do NOT drop the Cheats page.

## Existing MAD Ryujinx code (reuse, do not rebuild)

- `lib/madsrv/ryujinx_json.py` - JSON round-trip (atomic write bumps staterev "config"); CONFIG path.
- `lib/madsrv/ryujinx_cfg.py` - assign_devices (device id rewrite). NEEDS the input fixes above
  (per-GUID rank, GamepadSDL3, player_input_assignments, stop forcing SDL2).
- `lib/madsrv/ryujinx_cmds.py` - GROUPS/_GLOBAL settings registry + per-game DIRECT read/write
  (edits games/<tid>/Config.json in place; see "Per-game MAD model" below) + ryujinx.games.
- `lib/madsrv/ryujinx_input_cmds.py` - per-button map, sticks/controller_type selectors, _PLAYERS = Player1-8 + Handheld.
- `lib/switch_bind.py` - launch bind/restore (_target, _snapshot, restore) ryujinx branch; TRANSIENT.
  For v73 correctness the snapshot/restore must also cover player_input_assignments (currently input_config only).
- Launch is via the ES-DE bundled find rule `%EMULATOR_RYUJINX%` (NOT a custom es_find_rules entry);
  never hardcode an AppImage path. Install detection = _RYUJINX_BINARY_GLOBS + _RYUJINX_PATH_NAMES.
