#!/bin/zsh
# Biweekly refresh: fundamentals (P/E, PEG, earnings dates, beat/miss) + historical
# >=5% move detection. movements.py merges (keeps prior research), so
# movements_research.py only spends credits on genuinely new/unchecked moves.
export PATH="/opt/homebrew/bin:/Users/chelsqueck/.local/bin:/usr/local/bin:/usr/bin:/bin"
cd /Users/chelsqueck/stock-monitor

.venv/bin/python scripts/fundamentals.py
.venv/bin/python scripts/movements.py
# Checks data/feed-raw.txt (via scripts/feed.py) for moves in the last 2 days -
# note this runs at 06:00, before today's 07:30 news.py refresh, so the feed
# it sees is still yesterday's snapshot. Fine given this only matters for the
# newest couple of moves and runs just 2x/month; web search is the fallback.
.venv/bin/python scripts/movements_research.py
.venv/bin/python scripts/build.py --source yahoo --no-alert
./scripts/deploy-pages.sh || true
