#!/bin/zsh
# Daily intel refresh: X/Gmail/Telegram -> per-ticker news + events + earnings
# focus + IV. The 09:30/22:30 price builds then pick up the fresh data.
export PATH="/opt/homebrew/bin:/Users/chelsqueck/.local/bin:/usr/local/bin:/usr/bin:/bin"

# Refresh the X digest (writes ~/x-reader/digest.json). Non-fatal if it fails.
~/x-reader/.venv/bin/python ~/x-reader/x_scrape.py >/dev/null 2>&1 || true
# IV rank / implied move from IBKR (keeps last iv.json if Gateway is down).
~/stock-monitor/.venv/bin/python ~/stock-monitor/scripts/ivdata.py || true
# News tags + dated events, one fetch + one Claude call (was two scripts).
~/stock-monitor/.venv/bin/python ~/stock-monitor/scripts/news.py
# Upcoming-earnings focus (batched, grounded).
~/stock-monitor/.venv/bin/python ~/stock-monitor/scripts/earnings_research.py || true
