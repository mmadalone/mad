#!/usr/bin/env bash
# Front-end for the DirtBagXon Supermodel Sinden/ManyMouse fork.
# Delegates to the smart Python launcher (which detects current Sinden
# MOUSE# and X-Arcade JOY# at runtime and rewrites Supermodel.ini bindings
# accordingly), then execs supermodel itself.
#
# We tee through a log file so /tmp/supermodel-sinden-last.log captures
# both the launcher's detection output and the emulator's runtime output.

LOG=/tmp/supermodel-sinden-last.log
{
    echo "==== $(date) ===="
    echo "argv: $*"
    /home/deck/Emulation/tools/launchers/supermodel-sinden-smart.py "$@" 2>&1
} 2>&1 | tee "$LOG"
# Propagate the real launch status (the brace group's = the .py / its exec'd
# supermodel) past tee, so ES-DE sees a failed launch instead of tee's always-0.
exit "${PIPESTATUS[0]}"
