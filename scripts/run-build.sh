#!/bin/zsh
# stock-monitor build runner — invoked by launchd 4x/day (see com.chels.stockmonitor.plist).
# Source defaults to yahoo; set PRICE_SOURCE=ibkr in the plist once IB Gateway is verified.
set -e

DIR="/Users/chelsqueck/stock-monitor"
cd "$DIR"

export PATH="/opt/homebrew/bin:/Users/chelsqueck/.local/bin:/usr/local/bin:/usr/bin:/bin"

if [[ ! -x "$DIR/.venv/bin/python" ]]; then
  uv venv --python 3.12 "$DIR/.venv"
  uv pip install --quiet --python "$DIR/.venv/bin/python" requests
fi

SOURCE="${PRICE_SOURCE:-yahoo}"

# Only alert on level-touch at 22:00 HKT (10pm). All other builds refresh data silently.
HOUR=$(date +%H)
if [[ "$HOUR" == "22" ]]; then
  ALERT_FLAG=""
else
  ALERT_FLAG="--no-alert"
fi

"$DIR/.venv/bin/python" "$DIR/scripts/build.py" --source "$SOURCE" $ALERT_FLAG
"$DIR/scripts/deploy-pages.sh" || echo "[warn] pages deploy failed"
