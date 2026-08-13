# MEGA cloud backup: rclone + restic on the Steam Deck

Cache of official-doc findings + our implementation decisions for the MEGA cloud
backup feature (deck-cloud.sh). Sources read 2026-07-22.

## What we built (see plan polished-riding-deer.md)
- `~/Emulation/tools/bin/{rclone,restic}` (static binaries, live on /home so a SteamOS
  update does NOT wipe them). Versions at build time: rclone v1.74.4, restic v0.19.1.
- `deck-cloud.sh` = the single owner of every rclone/restic call. Subcommands:
  push-precious, sync-library, snapshots, restore-precious, restore-library, status,
  set-toggle, prune, is-connected.
- `deck-cloud-setup.sh` = one-time guided connect (Desktop Mode; needs the user's password).
- Tier A (precious: saves + configs) => restic, versioned/encrypted, on game-exit hook
  (game-end/20-cloud-push.sh) + opt-in during-play timer (cloud-sync.timer).
- Tier B (big library: ROMs/media/...) => rclone COPY (additive, never deletes),
  manual "Sync library now".
- One MEGA folder: `SteamDeck-Backup/` (restic-precious/ for A, library/<cat>/ for B).

## Install / persistence
- rclone + restic are each a single static Go binary, no deps. Never put them in /usr
  (read-only A/B image, wiped on OS update). Under $HOME they persist. Use ABSOLUTE
  paths in scripts (hooks run without ~/.local/bin on PATH); for restic pass
  `-o rclone.program=/home/deck/Emulation/tools/bin/rclone`.
- Configs (persist, under $HOME): rclone `~/.config/rclone/rclone.conf` (chmod 600);
  restic password file `~/.config/restic/pw` (chmod 600).
- Source: https://rclone.org/install/  https://rclone.org/docs/

## rclone MEGA backend
- Configure: `rclone config create mega mega user=<email> pass=<pw> --obscure`. VERIFIED
  2026-07-22: --obscure stores the password obscured (reversible, NOT a hash - so the
  file MUST be chmod 600; `rclone reveal` round-trips it). `rclone config update mega
  hard_delete true` sets `hard_delete = true`.
- PREREQ: log into mega.nz in a browser once first (MEGA only generates the account
  encryption keys after a real browser login, else rclone login fails).
- Backend uses the third-party go-mega lib (not MEGA's official SDK). Consequences:
  - No modtimes, no hashes => plain `rclone sync` compares by SIZE ONLY. A same-size
    save edit is MISSED. This is THE reason Tier A uses restic, not rclone sync.
  - "Blocked under heavy use": MEGA rejects logins under fast successive commands
    (self-clears in ~a week). Rare permanent BANS reported even on paid Pro under mass
    churn/deletes. Mitigations we use: low --transfers/--checkers (4), --tpslimit 10,
    COPY not sync for the seed (no deletes), prune rarely+throttled, and restic (one
    login per run).
  - Duplicate filenames possible (`rclone dedupe mega:` to fix). Memory scales with
    file count (go-mega loads the whole node tree at login).
- Throttle flags (global, work on MEGA): `--tpslimit`, `--tpslimit-burst`, `--transfers`,
  `--checkers`, `--bwlimit UP:DOWN` (+ timetable `--bwlimit "08:00,512k 23:00,off"`),
  `--low-level-retries`, `--retries`, `--retries-sleep`.
- Source: https://rclone.org/mega/  https://rclone.org/docs/

## restic over rclone (Tier A, versioned)
- Repo URL `rclone:<remote>:<path>` e.g. `rclone:mega:SteamDeck-Backup/restic-precious`.
  restic launches `rclone serve restic --stdio` = ONE long-lived MEGA login per run
  (ban-friendly). init: `restic -o rclone.program=<rclone> -r <repo> init`.
- Content-addressed: restic uses its OWN index + snapshots, so it detects changed saves
  reliably despite MEGA having no modtime/hash. This is why restic is correct here.
- Throttle: `--limit-upload <KiB/s>` (1024 = ~1 MiB/s), `--limit-download`, plus
  `nice -n 19 ionice -c3`. `--pack-size <MiB>` (default 16, range 4-128; we use 64) =
  fewer/larger packs = fewer MEGA requests.
- Restore (never blind-overwrite): `restic restore <snap|latest> --target <scratch>`,
  then copy back what you want. `snapshots` / `forget --keep-daily 7 --keep-weekly 5
  --keep-monthly 12 --prune`.
- Password: `RESTIC_PASSWORD_FILE` (chmod 600). LOSING IT = data unrecoverable (the
  setup helper makes the user choose it and warns to write it down; it is the only key
  to the encrypted MEGA backup if the Deck dies).
- prune GOTCHA: rclone MEGA delete goes to the Rubbish bin (still counts against quota)
  unless the remote has `hard_delete = true` (we set it). prune churns many packs =
  top ban-risk op; run it rarely + throttled.
- Source: https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html
  https://restic.readthedocs.io/en/stable/040_backup.html
  https://restic.readthedocs.io/en/stable/manual_rest.html
  https://restic.readthedocs.io/en/stable/050_restore.html
  https://restic.readthedocs.io/en/stable/060_forget.html
  https://restic.readthedocs.io/en/latest/047_tuning_parameters.html

## Rejected / deferred
- MEGAsync GUI: needs Desktop Mode, two-way live sync (risky for saves), native package
  wiped by OS updates. Not used.
- MEGAcmd (official SDK): safest against bans, but needs a background daemon, native
  package wiped by updates, its WebDAV bridge for restic is flaky/crashy (forum reports),
  restore less granular. DEFERRED fallback only if go-mega bans ever bite. `mega-backup`
  (one-way + `--num-backups` retention) would be the entry point.
  Refs: https://github.com/meganz/MEGAcmd
  https://forum.rclone.org/t/got-banned-from-mega-due-to-massive-overhead/49308

## Troubleshooting (observed 2026-07-22/23, Miquel's account) - ROOT CAUSE CONFIRMED
- `rclone about mega:` -> "couldn't login: unexpected end of JSON input". Config well formed,
  2FA OFF. `-vv --dump bodies` trace (2026-07-23) shows: `us0` OK, `us` (login) OK -> valid
  session, then `[{"a":"f","c":1}]` (FETCH NODE TREE) returns bare **`-3`** (EAGAIN, "retry")
  twice, then go-mega gives up -> the JSON error. So: LOGIN SUCCEEDS; the whole-tree fetch is
  refused. NOT a bad password, NOT a login block, NOT fixable by waiting (persistent 6h+).
- Cause: rclone's third-party go-mega backend fetches the ENTIRE account tree in one `f`
  call; MEGA `-3`'s that for large/rate-limited accounts and go-mega does not paginate/retry
  properly. Known go-mega limitation. No rclone flag fixes it. rclone v1.74.4.
- FIX: use MEGA's official client MEGAcmd (proper paginated fetch + backoff). This forces the
  transport rework the plan deferred (restic-over-rclone -> MEGAcmd mega-* / native
  versioning). Verify MEGAcmd actually connects on this account BEFORE reworking. If MEGAcmd
  also `-3`s, it is an account/IP throttle (wait / contact MEGA). Alternative: keep the clean
  restic design and point it at a different rclone backend that works (GDrive/Dropbox/etc.).
- Handling: do NOT loop retries (prolongs the block). Wait, then run ONE bounded check:
  `deck-cloud.sh probe` (single `rclone about`, --retries 1, 120s timeout). Re-run
  `deck-cloud-setup.sh` only once it reports reachable.
- If 2FA is ON: rclone's mega backend has `--mega-2fa`/`RCLONE_MEGA_2FA`, but the TOTP code
  changes every 30s and each backup re-logs-in, so a stored code cannot drive unattended
  backups. 2FA accounts must either disable 2FA or use MEGAcmd (official client, persistent
  session) as the transport. Not needed here (2FA off).
- Same "end of JSON" error can also be a wrong password; the hardened setup surfaces the
  error clearly and lets you re-enter creds.

## SOLUTION (2026-07-23): MEGA S4 (S3), NOT the cloud drive
The go-mega failure is because the account has 1,036,529 files; rclone fetches the whole
tree in one `f` call and MEGA `-3`s it. MEGAcmd works (fetched all 1M, ~488MB RAM in the
esde-ubuntu distrobox) but is heavy. THE FIX = **MEGA S4**, MEGA's S3-compatible object
storage: per-bucket, no global tree fetch, so the `-3` cannot happen. restic (native S3)
and rclone (s3 backend) both work. VERIFIED end-to-end via the engine: Tier A restic
backup+restore + Tier B rclone copy+restore + 20MB multipart, byte-identical.
- Endpoint `https://s3.g.s4.mega.io` (global; S4 routes any region), region `eu-central-1`,
  bucket `steamdeck`. restic repo `s3:https://s3.g.s4.mega.io/steamdeck/restic-precious`.
  Tier B rclone remote `s4` (type s3, provider Other, env_auth=true) -> `s4:steamdeck/library`.
- Creds = AWS keys in a file (aws_access_key_id / aws_secret_access_key). deck-cloud.sh
  resolves it from ~/.ssh/credentials-steamdeck, ~/.config/deck-cloud/, ~/.claude/tokens/,
  ~/Emulation/tools/tokens/mega-steamdeck (first found; DECK_CLOUD_CREDS overrides). restic
  reads AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY env; rclone uses env_auth (NO secret in
  rclone.conf). Keep the file chmod 600.
- S4 quirks (harmless): GetBucketVersioning -> 501 NotImplemented (rclone logs + continues;
  restic versions via its own snapshots). Multipart: parts 1..N-1 equal size, part N smaller
  (restic/minio-go already do this).
- NOTE: `grep` in the Claude Code interactive shell is a ugrep function, but `bash script.sh`
  children use REAL /usr/bin/grep, so the scripts' cred parsing is fine.

## FINAL DESIGN UPDATE (2026-07-23): dropped restic, both tiers = browsable rclone copies
Miquel wanted to SEE his files in the MEGA web UI (restic stores opaque encrypted hash-named
packs). For small game saves that encryption/dedup is marginal, and on S4 `rclone copy` is
already incremental (S3 has real modtime/hash), so restic was overengineering. So restic is
DROPPED; both tiers are plain rclone copies to S4:
- Tier A saves+configs -> `rclone copy` each precious path to `s4:steamdeck/precious/<home-rel>`
  with `--backup-dir s4:steamdeck/precious-versions/<YYYYmmdd-HHMMSS>/...` (browsable rollback
  net; overwritten files kept per run). `-L` follows symlinked saves. rclone `--exclude`
  patterns (matched per-copy: `*.cache`, `**/dev_hdd0/game/**`, `**/retroarch/cores/**`,
  `themes/**`, `downloaded_media/**`, etc.) drop the big/re-downloadable stuff.
- Tier B ROMs/media -> `rclone copy` to `s4:steamdeck/library/<cat>` (no -L; matches prior).
- `snapshots` lists version-folder timestamps (rollback points); `restore-precious [ver] [dir]`
  copies precious/ or a version into a STAGING dir; `prune` keeps newest N (DECK_CLOUD_KEEP_VERSIONS,
  default 30). No restic, no backup password. Setup = keys + rclone remote + enable (3 steps).
- Validated live on S4: browsable copy, per-copy excludes, version net (changed save -> old copy
  in precious-versions/<ts>/), restore, library. The restic binary is now unused.

## MEGA S4 endpoints + regions (read 2026-07-23; for the server-picker UI)
Sources: https://github.com/meganz/s4-specs (endpoint format `s3.<region>.megas4.com`,
`iam.<region>.megas4.com`); the region list lives at
https://help.mega.io/megas4/setup-guides/mega-s4-endpoint-urls (host blocked to WebFetch; read via
reader proxy); AUTHORITATIVE machine-readable region code from the Cyberduck profile XML
https://raw.githubusercontent.com/iterate-ch/profiles/main/MEGA%20S4%20Barcelona.cyberduckprofile
(Hostname s3.eu-barcelona.megas4.com, Region eu-barcelona, SigV4/AWS4-HMAC-SHA256).

TWO endpoint schemes:
1. GLOBAL / legacy: `https://s3.g.s4.mega.io`, region `eu-central-1` (auto-routes; = our current
   working default, bucket `steamdeck` created here).
2. REGIONAL (newer `s3.<seg>.megas4.com`) - the REGION STRING = the hostname segment (NOT eu-central-*):
   | Location   | S3 endpoint                     | region        |
   | Amsterdam  | s3.eu-amsterdam.megas4.com      | eu-amsterdam  |
   | Luxembourg | s3.eu-luxembourg.megas4.com     | eu-luxembourg |
   | Paris      | s3.eu-paris.megas4.com          | eu-paris      |
   | Barcelona  | s3.eu-barcelona.megas4.com      | eu-barcelona  | (CONFIRMED via profile XML)
   | Montreal   | s3.ca-montreal.megas4.com       | ca-montreal   |
   | Vancouver  | s3.ca-vancouver.megas4.com      | ca-vancouver  |
   | Tokyo      | s3.ap-tokyo.megas4.com          | ap-tokyo      |
   The OLD Cyberduck help-text region codes (Amsterdam=eu-central-1, Lux=eu-central-2,
   Montreal=ca-central-1, Vancouver=ca-west-1) are STALE for megas4.com; trust the profile XML.

EMPIRICAL (2026-07-23, Miquel's account, read-only `rclone lsd :s3,...:steamdeck`): the SAME bucket
+ SAME `precious/` and `precious-versions/` are reachable IDENTICALLY from the global, Barcelona AND
Amsterdam endpoints with the SAME AWS keys. => S4 buckets are GLOBALLY accessible from any regional
endpoint; account access keys are region-independent; switching the endpoint does NOT move data or
break access to existing uploads - it only changes the route (latency/throughput). The rclone remote
must set `region` to MATCH the chosen endpoint (SigV4 signing scope); pair them from the table above.
NOTE: the on-the-fly `:s3,...:bucket` connection string mangles `endpoint=https://...` (colon parse) -
use a real remote / config stanza with `endpoint = https://...` instead.

## Suspend/sleep behaviour of an ongoing MEGA S4 transfer (answered 2026-07-23)
Q (Miquel): does putting the Deck to SLEEP interrupt an in-progress cloud backup, and does it RESUME
after wake?

Facts (docs + the actual v1.74.4 binary):
- This Deck suspends via DEEP / S3 (the kernel forbids s2idle - see deck-docs/power-suspend.md). In S3
  the whole system halts: userspace processes are FROZEN by the kernel freezer before S3 and THAWED on
  wake - they are NOT killed. So a running rclone is PAUSED at suspend and CONTINUES at wake (same
  process, same invocation). Network is fully down during S3, so its TCP connections to S4 are dead on
  wake.
- rclone v1.74.4 defaults (verified via `rclone help flags`; we set NO overrides for these):
  `--timeout 5m0s` (IO idle), `--contimeout 1m0s`, `--low-level-retries 10`, `--retries 3`,
  `--retries-sleep 0`. On wake the stale connection either errors (ECONNRESET) or trips the 5m idle
  timeout -> a low-level retry re-establishes the connection and continues the operation.
- S3 object ATOMICITY: an object appears at the destination ONLY on a completed PUT / completed
  multipart; an interrupted upload leaves NO partial/corrupt object (the OLD object stays until the new
  one fully lands). `--s3-upload-cutoff` default = 200Mi: files <=200Mi upload as a SINGLE atomic PUT;
  larger files use multipart (our `--s3-chunk-size 16M`). rclone tracks a multipart upload within the
  run and retries failed PARTS (part-level resume); on give-up it aborts the multipart.

Conclusion:
- Sleep PAUSES a transfer; it does NOT corrupt it, and it RESUMES on wake (rclone reconnects via its
  retries and continues). No partial/corrupt file can ever appear at S4 (S3 atomicity).
- Tier A (saves + configs) files are almost all <200Mi -> single atomic PUT -> a file is either fully
  uploaded or not there yet. This is exactly the "back up during play / on exit" case, and suspend is
  completely safe for it.
- If the retries are exhausted (network still down well after wake, or a sleep far longer than the
  retry budget), only the leftover files fail THAT run - they get re-copied on the NEXT backup (the
  game-exit hook or the 5-min timer), because rclone copy is incremental (skips already-uploaded files,
  re-does missing ones). The version-net (`--backup-dir`) keeps any prior copy under
  precious-versions/<ts>/, so an overwrite interrupted mid-flight never loses the OLD data - it
  self-heals on the next run.
- flock: a frozen rclone keeps push.lock; the first timer tick after wake finds it held and SKIPS
  (`flock -n`) - no double-run, no deadlock; ticks proceed once the resumed run releases it.
- Only caveat, and it is NOT a suspend issue: if rclone is HARD-KILLED mid-multipart (force-quit/crash,
  not a freeze), orphan multipart parts can linger on S4 and consume quota until aborted. Suspend does
  not cause this (freeze != kill). Cleanup if ever needed: `rclone backend cleanup s4:steamdeck` (or an
  S4 lifecycle rule) to abort stale multipart uploads; Tier A single-PUT files cannot orphan.
NOT YET reproduced on-device. To confirm empirically: start a large `sync-library`, suspend mid-copy,
wake, confirm it finishes and the files are byte-intact (needs the user present to power the Deck).
Sources: `rclone help flags` (v1.74.4, local); https://rclone.org/docs/ (--timeout/--low-level-retries/
--retries); https://rclone.org/s3/ (--s3-upload-cutoff, multipart); deck-docs/power-suspend.md (deep/S3
freeze-thaw).

## Throughput / tuning (measured on-device 2026-07-23)
Not a MEGA throttle: raw S4 upload ceiling from this Deck is ~8-14 MB/s (a 50 MB single object
went up in ~3 s, even under nice-19/ionice-idle). The slow "371 KB/s" a user saw was per-file S3
PUT latency (~200 ms/object) x too few parallel transfers. Benchmark: 300 x 16 KB files took 62 s
at `--transfers 8` vs 22 s at `--transfers 32` (~2.8x). So on the many-small-files precious set,
throughput is TRANSFER-COUNT bound, not bandwidth. deck-cloud.sh now uses --transfers 16
(background timer/hook) / 32 (manual "Back up now"/"Sync"/restore, which also drop the idle I/O
priority since the user is watching). Also: the biggest single win is not uploading re-acquirable
bulk at all (the scraper tool, the AppImage = the GitHub release, RetroArch's online-updater
assets/overlays/shaders/downloads, EmuDeck's backend+caches, all logs) - see memory mega-cloud-backup.
Source: live benchmark + rclone flag semantics (--transfers/--checkers), 2026-07-23.

## MEGA S4 endpoint stalls writes: "backup runs busy but nothing uploads" (2026-07-24) - PER-ENDPOINT
Symptom (Miquel, live): "Back up now" ran for minutes, busy at very low speed, bucket stayed EMPTY
(0 objects). Progress UI: each file 100% but the file COUNT stuck at `Transferred: 0 / N`, bytes
growing run over run. MEANWHILE the user could drag&drop files into the SAME bucket via the browser.
ROOT CAUSE (proven by an endpoint head-to-head, NOT theory) = a SINGLE degraded S4 REGIONAL ENDPOINT,
and the backup was PINNED to it via the server picker:
- The backup's active server (deck-cloud.sh `status` -> server/endpoint) was **barcelona**
  (`s3.eu-barcelona.megas4.com`). Every diag test I ran used the GLOBAL endpoint by habit.
- Head-to-head, same account, same minute: GLOBAL single-PUT = 0s + 20/20 bulk in 3s; BARCELONA
  single-PUT = `net/http: timeout awaiting response headers` (22s) + 0/20 bulk. READS/listing fine on
  both. So it is ONE endpoint's WRITE path stalling, NOT the account, NOT concurrency, NOT the flags
  (the EXACT backup command - `copy --backup-dir -L --files-from --transfers 32 --fast-list` - lands
  6/6 in 3s on global). The browser works because it uses MEGA's NATIVE API, a different transport
  from the S3 gateway.
- The stall = PUT connects + sends its body, server never returns the response -> rclone waits, times
  out, retries, re-sends -> bytes climb, 0 files finalize. `errors:0` in every stats block (a stall,
  not an error; and `"errors":0`/`"retryError":false` are keys in EVERY stats JSON line - do NOT
  grep-count them as errors, that was a false read).
FIX = switch the server to a working endpoint: `deck-cloud.sh set-server global` (it probes it). S4
buckets are globally accessible from any regional endpoint with the same keys (see the endpoints
section above), so switching does NOT move data or break access - it only changes the route. Then
re-run the backup. (barcelona is the geo-closest for a Spanish-region user; switch back once it
recovers - test it first with one PUT to that exact endpoint.)
DIAGNOSIS LESSON (the important one): when the cloud backup "does nothing," FIRST check WHICH endpoint
the server picker is on and PUT-test THAT EXACT endpoint - regional S4 endpoints degrade independently
while global works, and reads/browser succeeding does NOT prove the S3 write path works. My first-pass
"MEGA is blocking your account" was WRONG (overstated from testing only one endpoint at a bad moment
~04:45 when several were briefly degraded); the persistent cause was barcelona-specific. Instrument the
real path, don't generalize.
Open follow-up (not built): deck-cloud.sh could auto-probe the active endpoint's WRITE path before a
run and fall back to / warn about switching servers, and surface "endpoint not accepting uploads" instead
of spinning at 100%/0-files. Also worth: `--tpslimit` + a stall detector.
Source: live endpoint head-to-head on Miquel's account 2026-07-24 (rclone v1.74.4, global vs barcelona
vs amsterdam); deck-cloud.sh server picker (`set-server`/`status`).

## 2026-07-29: persistent transfer jobs + global toggles (timer RETIRED)

- The 5-min `cloud-sync.timer`/`.service` are GONE (`deck-cloud.sh remove-timer-units`;
  `ensure-units` is a legacy alias that now also removes them - deck-post-update.sh cleans
  existing installs). `set-toggle timer` dies with a pointer at the new switch.
- Every LONG deck-cloud.sh command self-registers in the transfer-job registry
  (`lib/job_registry.py`; `$STATE_DIR/jobs/<id>.json` + `<id>.out`) via `_job_begin` at
  dispatch + an EXIT trap that records the rc. The MAD backend spawns transfers DETACHED
  (new session, output to the job's `.out`, id passed via `DECK_CLOUD_JOB_ID`) and only
  TAILS the file - closing the panel never kills a transfer anymore.
- rclone's per-second JSON stats go to the job's `.out` ONLY (not `cloud.log` - the old tee
  grew it to 186 MB); unregistered/internal copies drop the progress flags entirely.
- Toggles are state files under `$STATE_DIR`, set via `set-toggle <onexit|autoresume|gameplay>`:
  `onexit.enabled` (flag), `autoresume` (value; absent=on), `gameplay.enabled` (flag;
  ABSENT = off = the game-start hook SIGSTOPs every running job's pgid; game-end thaws
  exactly the gameplay-paused ones). That "every running job" is the toggle-OFF case only: a
  restore/fetch job is SIGSTOPped EITHER way (lib/job_registry.protected_during_gameplay, read
  by deprioritize_running - added audit 2026-08-12 phase 5) - toggle ON merely deprioritizes
  (ionice+renice) the uploads, it never lets a restore keep writing live saves mid-game. Hooks:
  game-start/01-cloud-pause.sh, game-end/19-cloud-resume.sh (19 < 20-cloud-push.sh,
  case-sensitive hook sort; the pause hook was renamed from 20- to 01- in the same audit phase
  so it freezes BEFORE the emulator-config-writing hooks run, not after them).
  The ES-DE Other-settings switches read these files directly and write via set-toggle.
- The old validator bug (rejecting "autoresume") was fixed in the same pass, in the RPC method
  the panel called at the time, `cloud.set_toggle`. That method is itself RETIRED now (audit
  2026-08-12 phase 5, dead code): the ES-DE Other-settings switches shell out to
  `deck-cloud.sh set-toggle` directly (lib/madsrv/cloud_cmds.py's module docstring), so there is
  no RPC method for the toggles to call anymore.

## 2026-07-30: Lutris game data in the steam backup tile

- A Lutris-launched shortcut (lutris:rungameid/<id> in the shortcut's LaunchOptions OR
  in the exe field's own arguments - Steam stores it in either; a %command% wrapper like
  the lsfg script pushes it into the exe args) resolves through lib/lutris_games.py:
  pga.db (sqlite, read-only) -> per-game YAML (data/lutris/games/<configpath>.yml) ->
  wine `prefix:` + `exe:`/`working_dir:`.
- Groups: saves = every drive_c/users/<user> home except Public (wine seats both `deck`
  and `steamuser` here); full prefix = ONE folder row, rel namespace
  steam/lutrisprefix/<home-rel>, restore re-bounded by the LIVE prefix from the Lutris
  config; game folder rides the shared steam/gamedir namespace (bound falls back to the
  Lutris working_dir/exe parent). SHARED prefixes are real (FoC/Devastation/Ultimate
  Spider-Man/Deadpool all use ~/Games/transformers-fall-of-cybertron) - the label says
  "SHARED with N other game(s)" because restoring it touches all of them.
- LAUNCH TRUTH FIRST: a game with BOTH a Lutris prefix and a leftover compatdata prefix
  (tried under Proton before moving to Lutris - Ultimate Spider-Man on this Deck) gets
  the Lutris groups as the live data plus an "Old Proton prefix (pre-Lutris leftover)"
  group (key prefix-proton, compatdata namespace).
- Preview: the two handheld text routes are now real P1 pad rows (Steam Deck icon).
