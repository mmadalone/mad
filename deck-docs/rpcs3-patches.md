# RPCS3 game patches: patch.yml + patch_config.yml formats

Cached from RPCS3 `master` source (`Utilities/bin_patch.cpp` + `bin_patch.h`) and the live
`~/.config/rpcs3/patches/patch.yml` (880 KB) on the Deck. Retrieved / verified 2026-07-14.
Basis for the MAD "Manage patches" tile (P4): `lib/madsrv/rpcs3_patches.py` +
`rpcs3_patches_cmds.py`.

## patch.yml (the patch DATABASE, read-only for us)
Location: `~/.config/rpcs3/patches/patch.yml`. Downloaded/managed by RPCS3.

Top-level keys are `PPU-<sha1hex>` nodes (one per game executable checksum), plus `Version:`
and one-or-more `Anchors:` blocks. Each PPU node maps a patch DESCRIPTION to a patch spec:

```yaml
PPU-83681f61...:                # a game-executable hash (a serial can map to SEVERAL hashes: disc revisions/regions)
  "Unlock FPS":                 # patch description (the toggle label)
    Games:                      # which titles/serials/versions this patch applies to (often a YAML *anchor)
      "Demon's Souls":
        BLUS30443: [01.00]      # title -> serial -> [app_versions]
    Author: "..."
    Notes: "..."
    Patch Version: 2.1
    Group: "Aspect ratio"       # OPTIONAL: mutually-exclusive alternatives (radio in RPCS3's own UI)
    Configurable Values: ...    # OPTIONAL (see below)
    Patch:                      # the actual memory writes (bulk of the file; we DON'T parse these)
      - [ be16, 0x00025ed8, 0x981f ]
```

### Read quirks (all must be handled; see rpcs3_patches._Composer / _build_index)
- **serials + versions are `.Scalar()` STRINGS.** `[01.00]` -> the string `"01.00"`. PyYAML's
  resolver would turn `01.00` into the float `1.0`; then the patch_config version key never
  matches the disc's `"01.00"` and the patch silently won't apply. -> read raw `node.value`,
  never construct these scalars.
- **DUPLICATE top-level `PPU-<hash>` keys exist** (e.g. same hash twice with different patch
  sets). yaml-cpp iterates raw nodes and keeps both; PyYAML `safe_load` collapses duplicate
  mapping keys (LAST wins) and would DROP patches. -> a node-level walk preserving duplicates.
- **DUPLICATE anchors exist** (e.g. `&32_9_value` defined in two `Anchors:` blocks). yaml-cpp
  tolerates redefinition (later def wins for later aliases); PyYAML's Composer RAISES. -> a
  Composer subclass without the duplicate-anchor guard.
- `Anchors` and `Version` top-level nodes are skipped (`patch_key::anchors`/`::version`).

### Configurable Values
```yaml
Configurable Values:
  "Aspect Ratio":
    Type: double_enum           # double_enum | long_enum -> "Allowed Values" label->number map
    Value: 3.555555555555556    # default
    Allowed Values:
      "32:9": 3.555555555555556
      "21:9 (3840x1600)": 2.4
  "FOV":
    Type: double_range          # double_range | long_range -> Min/Max/Value (a numeric picker)
    Value: 0.75
    Min: 0.1
    Max: 1
```
All numeric values are read as f64. `long_*` = integer.

## patch_config.yml (the ENABLED-STATE file; we WRITE this)
Location: `~/.config/rpcs3/patch_config.yml` (the config ROOT, NOT the `patches/` subdir where
patch.yml lives -- verified on-device; a real 639-byte file exists there). RPCS3 reads it at game boot and rewrites
it from its own Patch Manager dialog (NOT on every game exit). Absent until first use.

Structure (RPCS3 `save_config`):
```yaml
PPU-83681f61...:                        # FULL hash, prefix KEPT (container.hash = pair.first.Scalar())
  "Aspect Ratio":                       # description
    "Demon's Souls":                    # title
      BLUS30443:                        # serial
        "01.00":                        # app_version (STRING, must match patch.yml)
          Enabled: true                 # written ONLY when true
          Configurable Values:          # written ONLY when the value map differs from the patch defaults
            "Aspect Ratio": 2.4         # BARE number (f64), NOT the label
```
- `Enabled` (bool) emitted only when true; a version node with neither Enabled nor a dirty
  config block is omitted entirely.
- `Configurable Values` emitted only when `default_config_values != config_values` (compare the
  WHOLE param map); when dirty, ALL params are written (each at its chosen-or-default value).
- Legacy read tolerance: `"<version>": true` (scalar instead of a map) == Enabled.
- We write enable-state under EVERY (hash,title,serial,version) target a description covers for
  the serial; RPCS3 applies only the one whose executable hash matches the actual disc, so the
  extra targets are harmless. Read-modify-write preserves other games' entries.

Key constants (`patch_key`): `enabled="Enabled"`, `config_values="Configurable Values"`,
`value="Value"`, `type="Type"`, `min="Min"`, `max="Max"`, `allowed_values="Allowed Values"`,
`games="Games"`, `group="Group"`, `anchors="Anchors"`, `version="Version"`.

Sources: github.com/RPCS3/rpcs3 `Utilities/bin_patch.cpp` (save_config/load_config/load) +
`Utilities/bin_patch.h` (patch_key), master branch; live patch.yml on-device. Verified 2026-07-14.
