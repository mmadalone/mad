#!/usr/bin/env bash
# Wraps an emulator launch with the controller-router setup phase.
#
# Invoked from ES-DE's es_systems.xml <command> entries. Argument layout:
#
#     controller-router-wrap.sh \
#         SYSTEM ROM_PATH "NAME" "FULLNAME" -- EMULATOR_CMD EMULATOR_ARGS...
#
# Splits the args on the literal `--` separator. Runs setup; on success,
# replaces this process with the emulator (no extra wrapper layer). On
# failure (router exits non-zero — e.g. user picked Cancel in a warning
# dialog), this script exits non-zero and ES-DE treats it as a launch
# failure.
set -euo pipefail

ROUTER=/home/deck/Emulation/tools/launchers/controller-router.py

SYSTEM=${1:-}; ROM=${2:-}; NAME=${3:-}; FULLNAME=${4:-}
if [[ $# -lt 5 ]]; then
    echo "$(basename "$0"): expected SYSTEM ROM NAME FULLNAME -- EMULATOR..." >&2
    exit 2
fi
shift 4
if [[ ${1:-} != "--" ]]; then
    echo "$(basename "$0"): missing '--' before EMULATOR command" >&2
    exit 2
fi
shift  # drop the literal --

# Run the router setup. If it exits non-zero, do NOT exec the emulator.
"$ROUTER" setup "$ROM" "$NAME" "$SYSTEM" "$FULLNAME"

# Hand off to the emulator.
exec "$@"
