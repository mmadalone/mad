#!/usr/bin/env bash
# Full-screen ES-DE STARTUP splash via a SURGICAL binary patch.
#
# ES-DE hardcodes the splash to  setResize(0, clamp(H*0.62, 0, W*0.42))  — ~62% of
# screen height, centered (es-core/src/Window.cpp). The 0.62 and 0.42 floats are
# SHARED in .rodata (also used for menu sizing), so patching the CONSTANTS makes
# menus mis-render (proven: alt-emulator dialog went wide/blank). Instead we
# repoint ONLY the splash setResize's two `mulss xmm,[const]` instruction operands
# to the existing 1.0f constant, leaving every .rodata constant untouched:
#   @0x38cdf8  mulss xmm1,[0x567adc]=0.42  ->  [0x559764]=1.0   (clamp bound = W*1)
#   @0x38ce10  mulss xmm0,[0x565fa8]=0.62  ->  [0x559764]=1.0   (height    = H*1)
# => setResize(0, min(W*1,H*1)) = full screen height; width auto = full width for a
#    screen-aspect splash.svg; ES-DE's own centering then resolves to (0,0). Menus
#    keep the real 0.42/0.62.
#
# ES-DE ships read-only -> extract once to an AppDir, patch the binary there, run
# via a wrapper (which also regenerates the splash image). Fully reversible
# (ES-DE.AppImage.real kept). Re-run after any ES-DE/EmuDeck update.
#
# OFFSETS ARE VERSION-SPECIFIC (ES-DE 3.4.1 r51, built Apr 5 2026). The patch
# STRICTLY verifies the instruction bytes + current target before touching
# anything and ABORTS safely if the build differs.
set -uo pipefail
APPS="$HOME/Applications"
APP="$APPS/ES-DE.AppImage"; REAL="$APPS/ES-DE.AppImage.real"; APPDIR="$APPS/ES-DE.AppDir"
WORK="$HOME/.cache/esde-bigsplash"
log(){ echo "[bigsplash] $*"; }

is_genuine(){ [ -f "$1" ] && [ "$(stat -c%s "$1")" -gt 100000000 ] \
  && [ "$(dd if="$1" bs=1 skip=8 count=3 2>/dev/null | xxd -p)" = "414902" ]; }

if is_genuine "$APP"; then cp -f "$APP" "$REAL"; log "snapshotted stock AppImage -> .real"
elif [ -f "$REAL" ] && is_genuine "$REAL"; then log "ES-DE.AppImage is our wrapper; using existing .real"
else log "ERROR: no genuine ES-DE AppImage (neither ES-DE.AppImage nor .real)"; exit 1; fi

rm -rf "$WORK"; mkdir -p "$WORK"
( cd "$WORK" && "$REAL" --appimage-extract >/dev/null 2>&1 ) || { log "extract failed"; exit 1; }
SRC="$WORK/squashfs-root"; [ -L "$SRC" ] && SRC="$(readlink -f "$SRC")"
BIN="$SRC/usr/bin/es-de"; [ -f "$BIN" ] || { log "es-de binary not found"; exit 1; }

python3 - "$BIN" <<'PY' || exit 1
import struct, sys
p = sys.argv[1]; d = bytearray(open(p, "rb").read())
ONE = 0x559764  # existing 1.0f constant (rodata is identity-mapped: vaddr==fileoff)
if struct.unpack_from("<f", d, ONE)[0] != 1.0:
    print("[bigsplash] ABORT: 1.0f not at 0x559764 (build changed)"); sys.exit(1)
def repoint(off, opc, want):
    if d[off:off+4] != opc:
        print(f"[bigsplash] ABORT: opcode@{hex(off)} != expected (ES-DE build changed) — not patching"); sys.exit(1)
    rip = off + 8; cur = struct.unpack_from("<i", d, off+4)[0]
    if rip + cur != want:
        print(f"[bigsplash] ABORT: {hex(off)} targets {hex(rip+cur)} != {hex(want)} — not patching"); sys.exit(1)
    struct.pack_into("<i", d, off+4, ONE - rip)
    print(f"[bigsplash] repointed {hex(off)} ({hex(want)} -> 1.0f)")
repoint(0x38cdf8, b'\xf3\x0f\x59\x0d', 0x567adc)   # W*0.42 -> W*1.0  (clamp upper bound)
repoint(0x38ce10, b'\xf3\x0f\x59\x05', 0x565fa8)   # H*0.62 -> H*1.0  (height factor)
open(p, "wb").write(d)
PY

rm -rf "$APPDIR"; mv "$SRC" "$APPDIR"
cat > "$APP" <<EOF
#!/usr/bin/env bash
# Full-screen-splash wrapper: regenerate the splash image, then run the
# surgically-patched ES-DE AppDir. Reverse: restore ES-DE.AppImage.real over this.
[ -x "\$HOME/Emulation/tools/launchers/esde-splash-gen.sh" ] && \\
  "\$HOME/Emulation/tools/launchers/esde-splash-gen.sh" 2>/dev/null || true
exec "$APPDIR/AppRun" "\$@"
EOF
chmod +x "$APP"; rm -rf "$WORK"

out=$(timeout 60 "$APPDIR/AppRun" --help 2>&1)
if echo "$out" | grep -qiE "no-splash|usage|--home|emulationstation"; then
  log "OK — patched ES-DE runs. Full-screen splash on next launch; menus unaffected."
else
  log "WARNING: patched ES-DE failed the --help check; restoring stock AppImage."
  cp -f "$REAL" "$APP"; chmod +x "$APP"; rm -rf "$APPDIR"; exit 1
fi
log "done. Re-run after any ES-DE/EmuDeck update."
