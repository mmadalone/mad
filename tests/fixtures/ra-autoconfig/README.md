# RetroArch udev autoconfig fixtures

Real copies, taken 2026-07-17 from this rig's
`~/.var/app/org.libretro.RetroArch/config/retroarch/autoconfig/udev/` -- the directory MAD
curates and `lib/device_binds._AUTOCONF_DIR` reads.

## Why they are here

`device_binds.binds_for()` is the udev base map: it turns a profile token like `l3` into that
pad's real button index by reading the pad's own autoconfig. Tests that resolve a profile under
udev therefore need these files, and CI has no RetroArch install at all -- `_AUTOCONF_DIR` does
not exist there, `binds_for` returns None, and `resolve_for` correctly writes nothing. So
`tests/test_ra_profiles_seed.py` passed here and failed on CI (commit fa67819): it was asserting
that a RetroArch flatpak was installed.

Copies rather than hand-written stubs on purpose: a stub is a replica, and a replica is not a
measurement (see the openbor-review-2026-07-17 and verify-hardware-facts-not-reasoning memories).
These are the exact bytes the router reads at launch, including the quirks that matter -- the
FC30's phantom BTN_C/BTN_Z shifting Select/Start to 10/11, and the X-Arcade profile declaring its
true USB id 045e:0719 while its evdev node reports 045e:02a1 (xpad rewrites id.product).

## Refreshing

Only if the seed's expectations genuinely change. Re-copy the file a pad resolves to:

    python3 -c "import sys; sys.path.insert(0,'.'); from lib import device_binds as db; \
                from tests._fakes import dev; \
                print(db._autoconfig_file(dev('045e:02a1','/d/e','Xbox 360 Wireless Receiver')))"

Do NOT edit them by hand to make a test pass. If a bind here disagrees with the rig, the rig wins
and the test is telling you something.
