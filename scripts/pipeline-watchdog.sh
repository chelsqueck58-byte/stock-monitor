#!/bin/zsh
# Runs hourly (see com.stock-monitor.watchdog.plist). If today's data hasn't been
# generated yet — e.g. the 07:30 pipeline was missed because the Mac was asleep —
# this catches up as soon as the Mac is next awake, instead of staying stale all day.
set -e

DIR="/Users/chelsqueck/stock-monitor"
cd "$DIR"
export PATH="/opt/homebrew/bin:/Users/chelsqueck/.local/bin:/usr/local/bin:/usr/bin:/bin"

LOCK="/tmp/stock-monitor-pipeline.lock"
if [ -f "$LOCK" ]; then
  echo "$(date '+%F %T') pipeline already running (lock present), skipping"
  exit 0
fi

TODAY=$(TZ=Asia/Hong_Kong date +%Y-%m-%d)
HOUR=$(TZ=Asia/Hong_Kong date +%H)

# Don't catch up before the normal 07:30 trigger would've had a chance to fire.
if [ "$HOUR" -lt 7 ]; then
  exit 0
fi

GEN_DATE=$(.venv/bin/python3 -c "
import json, datetime
d = json.load(open('site/data.json'))
gen = datetime.datetime.fromisoformat(d['generated_at'].replace('Z', '+00:00'))
print(gen.astimezone(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d'))
" 2>/dev/null || echo "unknown")

if [ "$GEN_DATE" = "$TODAY" ]; then
  exit 0
fi

echo "$(date '+%F %T') data stale (last generated $GEN_DATE, today is $TODAY) -- running catch-up pipeline"
touch "$LOCK"
trap 'rm -f "$LOCK"' EXIT
.venv/bin/python3 scripts/orchestrate.py
