#!/bin/zsh
# Push the freshly-built site to the public GitHub Pages repo.
# Called by run-build.sh after each build. Only commits when data.json changed.
set -e

SITE="/Users/chelsqueck/stock-monitor/site"
DEPLOY="/Users/chelsqueck/stock-monitor-pages"
export PATH="/opt/homebrew/bin:/Users/chelsqueck/.local/bin:/usr/local/bin:/usr/bin:/bin"

[ -d "$DEPLOY/.git" ] || { echo "deploy repo missing at $DEPLOY"; exit 1; }

cp -R "$SITE"/. "$DEPLOY"/
cd "$DEPLOY"
git add -A
if git diff --cached --quiet; then
  echo "no site changes to deploy"
else
  git -c user.name='chels' -c user.email='queckchels@gmail.com' commit -q -m "data refresh"
  git push -q origin main
  echo "deployed to GitHub Pages"
fi
