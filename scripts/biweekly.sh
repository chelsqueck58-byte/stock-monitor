#!/bin/zsh
# Biweekly refresh: fundamentals (P/E, PEG, earnings dates, beat/miss) + historical
# >=5% move detection. movements.py merges (keeps prior research), so
# movements_research.py only spends credits on genuinely new/unchecked moves.
export PATH="/opt/homebrew/bin:/Users/chelsqueck/.local/bin:/usr/local/bin:/usr/bin:/bin"
cd /Users/chelsqueck/stock-monitor

.venv/bin/python scripts/fundamentals.py
.venv/bin/python scripts/movements.py
.venv/bin/python scripts/movements_research.py
.venv/bin/python scripts/build.py --source yahoo --no-alert
./scripts/deploy-pages.sh || true
