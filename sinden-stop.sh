#!/usr/bin/env bash
# Stop the Sinden Lightgun driver.
HERE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"   # absolute, for sinden.conf
pkill -f 'sinden-smoother.py' 2>/dev/null  # legacy; smoother is currently disabled
pkill -f 'LightgunMono.exe' 2>/dev/null
sleep 0.5
rm -f /tmp/LightgunMono* 2>/dev/null

# Fire Home Assistant webhook to turn OFF the TV LED border strip (configurable; sinden.conf).
CONF="$HERE_DIR/sinden.conf"
if [ -f "$CONF" ]; then . "$CONF"; else echo "sinden-stop: $CONF missing — LED strip control off" >&2; fi
if [ "${SINDEN_LED_ENABLED:-0}" = "1" ] && [ -n "${SINDEN_LED_HA_BASE:-}" ] && [ -n "${SINDEN_LED_WEBHOOK_STOP:-}" ]; then
    curl -fsS -m 3 -X POST "$SINDEN_LED_HA_BASE/api/webhook/$SINDEN_LED_WEBHOOK_STOP" \
        >/dev/null 2>&1 &
    disown
fi

echo "sinden-stop: driver stopped" >&2
exit 0
