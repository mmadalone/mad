# mad-backend protocol — CANONICAL SPEC (proto 1)

Source of truth for the NDJSON protocol between the ES-DE-native MAD panel
(`es-app/src/guis/mad/` in the `deck-patches` fork) and `mad-backend.py` (this
repo). Keep this file in lock-step with code changes; bump `PROTO` in
`mad-backend.py` AND `MAD_PROTO_EXPECTED` in the fork only on BREAKING wire
changes. Written 2026-06-11 (native-panel phase 0).

## Transport & lifecycle

- The panel spawns `python3 ~/Emulation/tools/launchers/mad-backend.py` with
  pipes on stdin/stdout; stderr is pointed at
  `~/Emulation/storage/controller-router/mad-backend.log`.
- One JSON object per `\n`-terminated UTF-8 line, no embedded newlines.
- **Teardown invariant:** stdin EOF or SIGTERM ⇒ every stream stopped (evdev
  ungrabbed, children killed, paused drivers restored), exit 0. Verified
  ~120 ms. Belt-and-braces: `PR_SET_PDEATHSIG(SIGTERM)`.
- **Single instance:** exclusive flock on
  `~/Emulation/storage/controller-router/mad-backend.lock`; a second instance
  emits `{"event":"fatal","data":{"code":"EBUSY",...}}` and exits 4.
- Missing python-evdev ⇒ `{"event":"fatal","data":{"code":"ENODEPS",...}}`,
  exit 3 (panel shows "run deck-post-update.sh").
- `mad-backend.py --selfcheck` (used by deck-post-update.sh): imports
  everything, asserts tkinter-free, prints OK, exit 0.

## Message shapes

```jsonc
{"id": 7, "method": "policy.merged", "params": {}}        // panel → daemon
{"id": 7, "ok": true,  "result": {...}}                    // daemon → panel (1 per id)
{"id": 7, "ok": false, "error": {"code": "EINVAL", "message": "..."}}
{"event": "hello", "data": {...}}                          // server push (no id)
{"event": "stream", "stream": "s3", "data": {...}}         // stream push
```

- `id`: panel-side monotonically increasing int. Responses may arrive OUT OF
  ORDER (`slow` methods run on a worker pool); correlate by id.
- Streams: opened by a method returning `{"stream": "<token>"}`; pushes arrive
  tagged with that token; `{"closed": true}` is the final push (sent on any
  stop path).
- Error codes: `ENOMETHOD` (unknown method — panel degrades that feature, it's
  the forward-compat path), `EINVAL`, `EINTERNAL` (traceback in the log),
  `ETIMEOUT` (synthesized panel-side).

## Handshake

Daemon speaks first:

```jsonc
{"event": "hello", "data": {"proto": 1, "backend_version": "0.1.0",
                            "python": "3.13.x", "caps": ["evdev","hidraw","v4l2","sdl"],
                            "pid": 12345}}
```

Panel replies `{"id":0,"method":"hello.ack","params":{"proto":1}}`. Panel
policy: refuse to operate if `data.proto != MAD_PROTO_EXPECTED` → static
error screen naming both versions + "update via deck-fetch-esde.sh / git pull".
`backend_version` = the repo `VERSION` file.

## Methods — phase 0

`slow=true` ⇒ worker pool (response may interleave). All policy/splash writes
are SYNCHRONOUS + atomic (`localpolicy.dump`): the ack means on-disk; every
write returns `{"merged": <fresh merged policy>}` so the UI re-renders truth.

### core
| method | params | result |
|---|---|---|
| `ping` | — | `{pong:true}` |
| `hello.ack` | `{proto}` | `{proto}` |
| `shutdown` | — | clean teardown + exit (same path as EOF) |

### policy.* (ports of the Tk save-handlers — semantics preserved exactly)
| method | params | notes |
|---|---|---|
| `policy.merged` | — | `{merged}` — base TOML deep-merged with local |
| `policy.local` | — | `{local}` — for ● locally-overridden markers |
| `policy.set_system_flag` | `{system, flag, value}` | revert-to-base drops key + empty husk (`_set_sys`); display defaults: `warn_*`=ON, others=OFF |
| `policy.set_ports` | `{name, kind:"system"\|"collection", order:[fam...], nports, require_sinden?}` | writes `ports=[order]×nports`; collections may carry `require_sinden` |
| `policy.clear_ports` | `{name, kind}` | drops `ports` (+`require_sinden` for collections) + empty husk |
| `policy.set_pins` | `{scope:null\|system, pins:{"1":"uniq:..."}}` | empty table deletes key + husk |
| `policy.set_quit_combo` | `{scope:null\|system, buttons:[314,315], hold_sec?}` | global carries hold_sec; per-system buttons only |
| `policy.clear_quit_combo` | `{system}` | |
| `policy.set_backend_key` | `{backend, key, value}` | scalar knob |
| `policy.set_backend_list_member` | `{backend, key, member, present, is_int?}` | pad_classes / slot lists |
| `policy.set_backend_template` | `{backend, cls, profile}` | cemu per-family profile |
| `policy.set_hardware` / `policy.clear_hardware` | `{key[, value]}` | e.g. `xarcade_port` |
| `policy.reset_local` | — | deletes local.toml (`{message}`) |
| `policy.gui_flags` / `policy.set_gui_flag` | `{key, value}` | `[gui]` table (debug etc.) |

### splash.*
| method | params | result |
|---|---|---|
| `splash.get` | — | `{splash, modes, fits, picker_cap}` |
| `splash.set` | `{key, value}` | `{splash}` |
| `splash.images` | — | `{images:[names]}` from `~/ES-DE/splashscreens` |
| `splash.toggle_image` | `{name, on}` | `{splash}` |

### devices.*
| method | params | result |
|---|---|---|
| `devices.scan` | — | `{devices:[Device...]}` (fast — identity cache) |
| `devices.sdl` *(slow)* | — | `{sdl:[{index,name,vidpid,guid}], evdev_to_sdl:{path:idx}}` |
| `devices.battery` | `{macs:[...]}` | `{battery:{mac:{pct,status}}}` |
| `devices.wiimotes` *(slow)* | `{force?}` | `{present, slots, count}` — ACTIVE hidraw probe, 20 s TTL cache |
| `devices.watch` | — | `{stream}` — pushes `{changed:true, devices:[...]}` on /dev/input set change (2 s poll) |
| `devices.unwatch` | — | `{stopped}` |

`Device` = `{name, path, vid, pid, vidpid, uniq, phys, port, js_index,
mouse_index, is_joypad, is_mouse, is_keyboard, is_sinden, is_steam_virtual,
has_face_btn, pin_id, pin_kind, label, battery?:{pct,status}}`.
`label` is port-aware (the 045e pad at `[hardware].xarcade_port` = "X-Arcade").

### preview.* (read-only; the router's REAL pipeline via lib/routing)
| method | params | result |
|---|---|---|
| `preview.route` *(slow)* | `{key, kind}` | `{route}` |
| `preview.all` *(slow)* | `{force?}` | `{xport, controllers:[...], wiimotes, routes:[{key,label,art,kind,route}]}` |

`route` = `{kind:"text", text}` or `{kind:"pads", rows:[{slot,"text",icon?,
pinned?,reserve?}]}`. RetroArch systems/collections resolve through
resolve_pins → resolve_ports (pins + fallback rescue + X-Arcade port identity —
fixes the old Tk preview divergence which ignored all three); cemu/eden/rpcs3/
pcsx2 use the config-file slot preview; dolphin reports DolphinBar status.

### systems.* / esde.* / art.* (Systems page; backend owns art resolution)
| method | params | result |
|---|---|---|
| `esde.systems` | — | `{systems:[names]}` — gamelist-backed systems |
| `systems.list` | — | `{systems:[{name, sub, configured, art}]}` — tools excluded; `sub`=backend label or "hands-off"; `configured`=● state; `art`=abs console.png or null |
| `systems.get` *(slow)* | `{system}` | `{system, backend_label, managed, art, toggles:[{key,label,value}]}` — exactly the Tk detail page's toggle set (router_skip if managed; require_* if present-or-wii; the ONE category warn flag) |
| `art.resolve` | `{names:[rel...]}` or `{names:{logical:[rel...]}}` | `{path}` / `{paths:{logical: abs\|null}}` — chain: theme `router-config/` → launchers `art/` → `~/esde-build/art` |

### capture.* (press-to-identify / press-a-combo; phase 1)
| method | params | result |
|---|---|---|
| `capture.button` | `{mode:"identify"\|"combo", timeout_s=15}` | `{stream}` — starting a new capture cancels the previous |
| `capture.cancel` | — | `{cancelled}` |

Stream lifecycle (semantics = the Tk GamepadNav capture, face buttons
0x130-0x13F only, devices opened WITHOUT grabbing):
1. `{"event":"input.lock","data":{"locked":true,"stream":tok}}` — the panel must
   swallow its own input from here (the press also reaches SDL).
2. `{"ready":true}` stream event once the evdev nodes are OPEN (~0.5 s — a press
   before this is missed; arm the modal prompt on ready, not on lock).
3. Result: `{"held":[codes], "names":["START","SELECT"], "device":Device|null}`
   fired on the FIRST release with a non-empty held set, then the stream closes.
   OR `{"timeout":true}` / `{"error":msg}`.
4. `input.lock locked:false` + `{"closed":true}` on every exit path (result,
   timeout, cancel, daemon teardown).

evdev gotcha baked into the implementation: `read()` generators END a drained
burst by raising BlockingIOError — collect events incrementally (a `list()`
call discards the burst and looks like an unplug).

### backends.* / profiles.* (Backends page; phase 2)
| method | params | result |
|---|---|---|
| `backends.list` | — | `{backends:[{name, summary, no_players, art:[abs…]}], hidden:[names]}` — [backends.*] tables whose system has ES-DE games (all when gamelists unavailable); `summary`=first 4 non-advanced keys (config_dir/config_file excluded — paths are detail-page info, 2026-06-12); `no_players`=SDL whitelist empty (uses pad_classes/handheld_class but both empty); `art`=console.png per driven system (BE_SYS map) |
| `backends.describe` | `{backend}` | `{backend, warn_empty, knobs:[…], advanced:[keys]}` — the ORDERED typed knob list, 1:1 mirror of the Tk _backend_page (same knobs/order/conditionals; EINVAL on unknown backend) |
| `profiles.apply_slot` | `{backend:"cemu"\|"eden", slot:0-7, profile}` | `{message, merged}` — lib.mad_backup.apply_slot_profile: applies the named profile to the ACTIVE slot file (named profiles read-only, .router-backup safety net) and persists `slot_profiles[slot]`; empty profile clears the choice. `message` is the footer text (⚠-prefixed on failure — apply failures return ok:true with the warning message). **Deliberately FAST: every local.toml writer runs on the stdin thread (single-writer invariant) — a worker-pool writer would race inline policy.* read-modify-writes** |

`knob` kinds (writes go through the existing policy.set_backend_* methods):
- `bool` `{key, label, toggle_label, help, value}` → policy.set_backend_key
- `class_set` `{key, label, help, candidates:[{value, label, on}]}` → policy.set_backend_list_member {member:value, present}
- `int` `{key, label, help, value, lo, hi, step}` → policy.set_backend_key
- `slot_set` `{key, label, help, slots:[{slot, label, on}]}` → policy.set_backend_list_member {member:slot, present, is_int:true}
- `choice` `{key, label, help, value, value_label, options:[{value, label}]}` → policy.set_backend_key ("" = none; config paths get "✓ "/"· " exists markers in labels)
- `slot_profiles` `{key, label, help, slot_label:"Controller"\|"Player", profiles:[names], profiles_dir, slots:[{slot, profile}]}` → profiles.apply_slot (cemu/eden only; the Tk page's live-input tester button is phase 4)

### priority.* (Priority page; phase 2 — writes via policy.set_ports/clear_ports)
| method | params | result |
|---|---|---|
| `priority.list` *(slow)* | — | `{systems:[{name,p1,art}], collections:[{name,p1,lightgun,art}], available_systems:[{name,art}], available_collections:[{name,art}]}` — configured = RetroArch systems / enabled collections with `ports`; available = the pickers' lists; collection art falls back to controllers/lightgun icons |
| `priority.get` | `{name, kind}` | `{name, kind, order:[fams], nports, configured, require_sinden}` — order = existing P1 order filtered to known families + remaining families appended (the Tk editor composition); nports = len(existing ports) or 2 |

### sinden.* / camera.* (Lightgun section; phase 3)
| method | params | result |
|---|---|---|
| `sinden.health` | — | `{driver, mono, config}` — installation state (file stats + PATH lookup); the Lightgun page shows an INSTALL banner when driver/mono are missing |
| `sinden.install` | — | `{stream}` — runs sinden-install.sh (downloads the OFFICIAL ~25 MB bundle from sindenlightgun.com — never redistributed), streams `{line}` + `{done, rc}`; EBUSY while another install/backup job runs (shared _RUN_ACTIVE single-flight) |
| `sinden.status` *(slow)* | — | `{driver_running, smoother:{alpha,deadzone,snap,enabled}, led_enabled, cams:{"1":dev,"2":dev}}` — enabled = no `.smoothing-off` marker |
| `sinden.driver` *(slow)* | `{action:"start"\|"stop"\|"restart"\|"calibrate"\|"test"}` | `{message}` — detached sinden-*.sh scripts, logged to control-panel/ |
| `sinden.apply` *(slow)* | — | `{message, restarted}` — restart ONLY if running (Tk _sinden_apply) |
| `sinden.smoother_set` | `{alpha, deadzone, snap}` | `{message}` — sinden-smoother-preset.sh (live SIGHUP) |
| `sinden.smoother_toggle` | — | `{message}` — the canonical Toggle script; re-read status for truth |
| `sinden.led_set` | `{enabled}` | `{message}` — edits SINDEN_LED_ENABLED in sinden.conf (EINVAL if line missing) |
| `sinden.buttons` *(slow)* | `{player}` | `{player, driver_running, rows:[{base,label,key,code,code_label,off_key,off_code,off_label,mod_key,mod,mod_label}], groups:[{name,options}], modifiers}` |
| `sinden.set_keys` | `{pairs:{key:value}}` | `{message}` — backup_once + atomic set_many (SerialPort*/JoystickMode* refused by sinden_cfg) |
| `sinden.behavior` | `{player}` | `{recoil, strength, auto_recoil, auto_strength, auto_speed, handedness, handedness_label, offscreen_reload, suffix}` |
| `camera.get` | — | `{cams, vals:{"1":{Brightness,Contrast,auto,Exposure},"2":…}}` — seeds the daemon's slider state from the config |
| `camera.preview` *(slow)* | `{player}` | `{stream, path}` or `{stopped:true}` (second press on the live gun) — pauses the driver, spawns ffmpeg `-update 1` → `/tmp/mad-cam.ppm` (640×480 P6 RGB24); **the panel POLLS the file** (~15 Hz, mtime-gated) — no frame events. Stream pushes: `{ready,path}` once, `{error}`, `{status}` on driver restore, `{closed:true}`. Cleanup (any exit path incl. daemon teardown) kills ffmpeg + restores the pre-preview driver/LED state |
| `camera.preview_stop` | — | `{stopped}` |
| `camera.set` | `{player, ctrl:"Brightness"\|"Contrast"\|"auto"\|"Exposure", value}` | `{}` — remembered + applied live via v4l2-ctl iff previewing that gun |
| `camera.save` *(slow)* | — | `{message}` — persists Camera* keys, stops any preview (restoring the driver), else Tk _cam_save restore semantics |

Button-map live-press dots are panel-side: the driver synthesizes key/mouse
events at the display server, which reach ES-DE as SDL input — the page maps
sinden codes ↔ SDL keycodes itself (8-17→'0-9', 18-43→A-Z(+Shift), 44-69→a-z,
70-80 specials, 82-93 F-keys; mouse 1/2/3 via mouse events where available).

### daphne.* (Daphne/Hypseus section; phase 3)
The daemon holds the EDITING BUFFER (HypInput) like the Tk page's _dp_hi:
load → edit in memory → save writes (.bak via lib.hypinput). Re-entering the
page reloads from disk (unsaved edits dropped — Tk parity).
| method | params | result |
|---|---|---|
| `daphne.load` *(slow)* | `{scope:"global"}` or `{scope:"game", gamedir, base?}` | full page data: `{scope, base, game_name, caption, hint, dirty, seek_instant, sections:{primary,p2,directions,advanced}, rows:{action:{action,label,display,warn}}, games:[{gamedir,base,name}]}` |
| `daphne.clear` | `{action}` | `{row, message}` |
| `daphne.reset_defaults` | — | `{rows, message}` — stock layout into the buffer; Save applies |
| `daphne.bind` *(slow)* | `{action}` | `{message, warn, rows:{changed…}, dirty}` — runs lib/hypseus_capture.py (10 s, X-Arcade only); **emits `input.lock` true/false around the capture** (the press also reaches ES-DE); hat/axis/button semantics = Tk _dp_bind_done |
| `daphne.save` | — | `{message}` — write_global / write_per_game (+.commands link) per scope |
| `daphne.seek_set` | `{on}` | `{message, seek_instant}` — instant scene transitions, scope follows the buffer |
| `daphne.build_index` | `{arg:"all"\|"<folder>.daphne"}` | `{message}` — detached singe-indexer.sh (runs on-screen) |

### backup.* (Backup page; phase 5A — file logic = lib/mad_backup verbatim)
| method | params | result |
|---|---|---|
| `backup.sizes` | — | `{stream, sizes:{key:bytes,…}, already?}` — per-category byte sizes from `deck-backup.sh --sizes`: pushes `{key, bytes}` per category (keys: esde, emu, saves, bios, cores, bezels, rpcs3games, pcsx2tex, ryujinxgames, roms, media), `{done:true}` at the end. SINGLE-FLIGHT: re-request mid-sweep re-attaches to the live stream (`already:true`) — the response's `sizes` cache snapshot covers already-pushed keys. Sizes cached for the daemon's lifetime. Child runs in its OWN process group; a stop-watcher killpg()s it the moment the stream stops (the script is silent for minutes between lines, so loop-checks alone never fire) |
| `backup.run_full` | `{include:{<key>:bool,…}}` | `{stream}` — runs `deck-backup.sh --yes` with `--<flag>/--no-<flag>` per category (rpcs3games→rpcs3, ryujinxgames→ryujinx); pushes `{line}` per output line, `{done, rc}` at the end — done is emitted on EVERY path incl. exceptions (rc -1 = didn't finish cleanly) and always precedes closed. EBUSY if one is already running. The child runs in its own process group and DIES WITH THE DAEMON via the stop-watcher killpg (closing the panel kills a half-written archive — the page warns). cores/bezels are honored as standalone categories even with emu off (deck-backup.sh fix 2026-06-12) |
| `backup.snapshot` *(slow)* | — | `{message}` — `do_backup(backup_targets(merged))` → data/gui-backup |
| `backup.restore` | — | `{message}` — `do_restore` (**FAST**: copies local.toml back — single-writer invariant) |
| `backup.reset_local` | — | `{message}` — `reset_local` (**FAST**: unlinks local.toml — single-writer invariant) |
| `backup.restore_router` *(slow)* | — | `{message}` — reverts every emulator `*.router-backup` |
| `backup.mad_code` *(slow)* | — | `{message}` — tars launchers/ → ~/deck-config-backups (blocking, on the pool) |

## Planned (reserved names)
`panel.sections`.

### tester.* / gamepads.* / xarcade.* (live testers; phase 4)
| method | params | result |
|---|---|---|
| `gamepads.list` *(slow)* | — | `{pads:[{kind:"pad"\|"wii", path/slot+node, name, uniq, idtail, ext?, profile:{key,label,dir,icon,icon_path}}]}` — cached walk + LIVE DolphinBar probe (≤0.7s/slot) |
| `gamepads.layout` | `{key, dir, ext?, uniq?, name?}` | `{sprites:{stem:abs}, positions:{stem:[nx,ny]} (saved>baked), ext?:{kind,sprites,allowed,positions}, p2?}` |
| `gamepads.positions_save` | `{key, positions}` | control-panel/gp-`<key>`-positions.json (same format as Tk + baked defaults) |
| `gamepads.set_p2` | `{uniq, on}` | gp-p2-units.json |
| `xarcade.layout` | — | `{overlay, sprites, spots:[{key,label,x,y}], xbox_mode}` |
| `xarcade.status` | — | `{xbox_mode}` (metadata-only; page polls ~1.5s) |
| `xarcade.positions_save` | `{positions}` | xarcade-positions.json |
| `tester.start` *(slow)* | `{kind:"pad"\|"xarcade"\|"wii", path?, key?, stems?, slot?, node?}` | `{stream}` — ONE tester at a time (the previous stops first). Stream: `{ready}` after the **150 ms-delayed grab** (the starting press still reaches SDL); ≤30 Hz coalesced `{spots:{stem:bool}, sticks:{k:token}}`; `{countdown}` during escape holds; `{wii:{core,ext,kind,lstick,rstick}}`+`{status}` for wii; `{bound:{input,spot}}` on calibration capture; `{ended:reason, message}` then `{closed}`. **Backend-owned escapes**: pad = hold Start 6 s; wii = hold + 6 s; X-Arcade = P1+P2 Start 3 s; Steam Deck pad auto-stops ~20 s idle. Cleanup ungrabs+closes on EVERY path (teardown invariant). X-Arcade grabs GAMEPAD nodes only — the 1241:1111 trackball stays ungrabbed (Deck cursor lives) but is still read |
| `tester.stop` | — | `{stopped}` |
| `wii.barmode` | — | `{mode:"4"\|"1-3"\|"none", label, explanation}` — best-effort DolphinBar mode (4 = hidraw slots exist; 1-3 = Mayflash USB without slots; refine 1/2 vs 3 when observed) |
| `wii.probe_ext` *(slow)* | `{node}` | `{probed, ext?}` — one-shot accessory probe of one slot (the wii test page's idle poll; accessory hotplug emits no udev event). probed:false when the slot is empty/asleep or ANY tester stream is live. Leaves CONTINUOUS reporting set (the wii-nav-bridge co-reads) |
| `tester.calibrate` | `{action:"arm"\|"cancel", spot?}` / `{action:"save"}` | next input → spot into gp-`<key>`-calib.json / xarcade-calib.json |

### model2.* (Sega Model 2 emulator EMULATOR.INI editor; `lib/madsrv/model2_cmds.py`)
Edits `~/Emulation/roms/model2/EMULATOR.INI` (ElSemi m2emu). Comment-preserving regex
substitution (NOT configparser — would mangle inline `;` comments + the Wine `Z:\` path),
atomic temp+rename, one-time `.bak`. Only the curated keys in `GROUPS` are read/written;
debug/menu/launcher-managed keys (Wireframe, FullMode, Filter, DrawCross, RomDirs, …) are
never touched. Stateless live-save — each `set` re-reads disk, so it never fights the
launcher's per-game `DrawCross` sed. See deck-docs/model2-emulator-ini.md.
| method | params | result |
|---|---|---|
| `model2.get` | — | `{exists, path, groups:[{title, note, settings:[{key,label,type,value,options?,min?,max?,step?}]}]}` — `exists:false` (no error) if the INI hasn't been created yet. type ∈ bool\|enum\|int\|float\|resolution; enum/resolution carry `options[]` (enum value = index, resolution value = "WxH") |
| `model2.set` | `{key, value}` | `{key, value}` — writes one curated key (value sent as a string; backend coerces by the key's declared type) and returns the re-read effective value. Synthetic `key:"Resolution"`, value `"WxH"` → writes FullScreenWidth+Height in one atomic write. EINVAL on a non-editable key; ENOKEY if the key isn't in the file; ENOENT if the INI is missing |
