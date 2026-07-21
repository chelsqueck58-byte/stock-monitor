#!/bin/zsh
# Daily intel refresh: re-scrape X -> re-tag per-ticker news. The 4x/day price
# builds then pick up the fresh data/news.json. Runs before the morning build.
export PATH="/opt/homebrew/bin:/Users/chelsqueck/.local/bin:/usr/local/bin:/usr/bin:/bin"

# Refresh the X digest (writes ~/x-reader/digest.json). Non-fatal if it fails.
~/x-reader/.venv/bin/python ~/x-reader/x_scrape.py >/dev/null 2>&1 || true
# Tag the news to tickers (writes ~/stock-monitor/data/news.json).
~/stock-monitor/.venv/bin/python ~/stock-monitor/scripts/news.py
# IV rank / implied move from IBKR (keeps last iv.json if Gateway is down).
~/stock-monitor/.venv/bin/python ~/stock-monitor/scripts/ivdata.py || true
