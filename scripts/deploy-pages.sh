#!/bin/zsh
# Push the freshly-built site to BOTH public GitHub Pages repos.
# Called by run-build.sh and orchestrate.py after each build. Only commits when files changed.
set -e

SITE="/Users/chelsqueck/stock-monitor/site"
export PATH="/opt/homebrew/bin:/Users/chelsqueck/.local/bin:/usr/local/bin:/usr/bin:/bin"

# --- stock-monitor (production branch, flat repo root) ---
SM_DEPLOY="/Users/chelsqueck/stock-monitor-pages"
if [ -d "$SM_DEPLOY/.git" ]; then
  cd "$SM_DEPLOY"
  git fetch -q origin production
  git checkout -q -B production origin/production
  cp -R "$SITE"/. "$SM_DEPLOY"/
  git add -A
  if git diff --cached --quiet; then
    echo "[stock-monitor] no site changes to deploy"
  else
    git -c user.name='chels' -c user.email='queckchels@gmail.com' commit -q -m "data refresh"
    git push -q origin production
    echo "[stock-monitor] deployed to GitHub Pages (production)"
  fi
else
  echo "[stock-monitor] deploy repo missing at $SM_DEPLOY" >&2
fi

# --- ai-supply-chain (main branch, flat repo root, no index-live/supply-chain-live) ---
# NOTE: front-end files (supply-chain.html etc.) are hand-edited directly in this
# repo and are NOT part of the generic site build. Never `git add -A` here - that
# stages this stale copy over hand edits. publish.py stages only the generated
# JSON in its GENERATED allowlist and restores anything else the cp below touched.
ASC_DEPLOY="/Users/chelsqueck/ai-supply-chain-pages"
if [ -d "$ASC_DEPLOY/.git" ]; then
  cd "$ASC_DEPLOY"
  git fetch -q origin main
  git checkout -q -B main origin/main
  cp -R "$SITE"/. "$ASC_DEPLOY"/
  rm -f "$ASC_DEPLOY/index-live.html" "$ASC_DEPLOY/supply-chain-live.html"
  python3 scripts/publish.py -m "data refresh"
  echo "[ai-supply-chain] published via publish.py (main)"
else
  echo "[ai-supply-chain] deploy repo missing at $ASC_DEPLOY" >&2
fi
