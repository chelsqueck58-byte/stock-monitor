#!/bin/zsh
# Daily intel refresh (07:30 HKT, one run/day): X/Gmail/Telegram -> per-ticker
# news + events + earnings focus + IV + your own Telegram-sent research. The
# 10:00/22:00 price builds then pick up the fresh data.
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

# Refresh the X digest (writes ~/x-reader/digest.json). Non-fatal if it fails.
~/x-reader/.venv/bin/python ~/x-reader/x_scrape.py >/dev/null 2>&1 || true
# IV rank / implied move from IBKR (keeps last iv.json if Gateway is down).
~/stock-monitor/.venv/bin/python ~/stock-monitor/scripts/ivdata.py || true
# News tags + dated events, one fetch + one Claude call (was two scripts).
# Also writes data/feed-raw.txt (X+Gmail+Telegram combined) - the two scripts
# below read it via scripts/feed.py, so this MUST run before them.
~/stock-monitor/.venv/bin/python ~/stock-monitor/scripts/news.py
# Upcoming-earnings focus (batched, grounded, freshness-TTL'd). Checks
# feed-raw.txt for this ticker before web-searching.
~/stock-monitor/.venv/bin/python ~/stock-monitor/scripts/earnings_research.py || true
# Broader non-earnings catalysts for priority tickers (freshness-TTL'd). Same
# feed-first check as earnings_research.py above.
~/stock-monitor/.venv/bin/python ~/stock-monitor/scripts/catalysts.py || true
# Macro calendar (Fed/CPI/NFP/PMI) - market-wide, not tied to any ticker.
# Freshness-TTL'd weekly, dates don't move day to day.
~/stock-monitor/.venv/bin/python ~/stock-monitor/scripts/macro_events.py || true
