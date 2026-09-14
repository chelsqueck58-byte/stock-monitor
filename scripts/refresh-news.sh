#!/bin/zsh
# Prereq ingestion, run as orchestrate.py's first stage (one run/day): your
# own Telegram-forwarded research + IBKR implied-vol data. Everything that
# reads X (x-reader's own com.x-reader.scrape-safe daemon, ~5min earlier) or
# writes/reads data/feed-raw.txt (news.py, catalysts.py, earnings_research.py,
# macro_events.py) now runs inside orchestrate.py itself, in dependency order
# - this used to be a second, independently-scheduled LaunchAgent racing
# orchestrate.py's identical 07:30 trigger with no ordering guarantee between
# them, and separately called x_scrape.py directly, duplicating x-reader's
# own scrape and doubling the bot-detection surface for no benefit.
export PATH="/opt/homebrew/bin:/Users/chelsqueck/.local/bin:/usr/local/bin:/usr/bin:/bin"

# --- Your Telegram research pipeline (synced to this one daily run, not
# continuous polling — nothing downstream reads it more than once a day). ---
# 1. Pull any new messages since last run.
/usr/bin/python3 ~/.claude/scripts/tele-receiver.py || true
# 2. Download + extract text from any attached documents.
~/.claude/scripts/.venv/bin/python ~/.claude/scripts/tele-doc-processor.py || true
# 3. Parse new material into catalysts.md/fundamentals.md/historicals.md (the
#    only token-spending tele step; tracks merged IDs, never re-parses).
~/.claude/scripts/.venv/bin/python ~/.claude/scripts/tele-memory.py || true

# IV rank / implied move from IBKR (keeps last iv.json if Gateway is down).
~/stock-monitor/.venv/bin/python ~/stock-monitor/scripts/ivdata.py || true
