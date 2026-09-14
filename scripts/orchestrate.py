#!/usr/bin/env python3
"""Daily stock-monitor pipeline orchestration.

Runs all data collection scripts sequentially with sensible checkpoints:
- Stage 1: Fetch fundamentals (P/E, market cap, earnings dates)
- Stage 2: Parallel web searches (catalysts, news, IV, macro)
- Stage 3: Detect and research price movements
- Stage 4: Merge all sources, reprice forward P/E to today's close
- Stage 5: Deploy to stock-monitor + ai-supply-chain GitHub Pages

Run: .venv/bin/python scripts/orchestrate.py
"""
import fcntl
import subprocess
import sys
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

LOCK_PATH = Path("/tmp/stock-monitor-pipeline.lock")

def run_script(name, *args, timeout=600):
    """Run a script and return True if successful."""
    path = SCRIPTS / f"{name}.py"
    print(f"\n{'='*60}\n[{datetime.datetime.now().strftime('%H:%M:%S')}] Running {name}...\n", flush=True)
    try:
        result = subprocess.run(
            [".venv/bin/python3", str(path)] + list(args),
            cwd=ROOT,
            timeout=timeout,
            capture_output=False
        )
        success = result.returncode == 0
        print(f"\n[{name}] {'✓ OK' if success else '✗ FAILED'}", flush=True)
        return success
    except subprocess.TimeoutExpired:
        print(f"\n[{name}] ✗ TIMEOUT after {timeout}s", flush=True)
        return False
    except Exception as e:
        print(f"\n[{name}] ✗ ERROR: {e}", flush=True)
        return False

def main():
    start = datetime.datetime.now()
    print(f"\n{'='*60}\n[PIPELINE START] {start.strftime('%Y-%m-%d %H:%M:%S')}\n", flush=True)

    results = {}

    # Stage 0: prereq ingestion - your Telegram-forwarded research + IBKR IV
    # data. Best-effort (matches the script's own `|| true` per step): never
    # blocks the rest of the pipeline.
    print(f"\n{'='*60}\n[{datetime.datetime.now().strftime('%H:%M:%S')}] Running refresh-news (prereqs)...\n", flush=True)
    try:
        prereq = subprocess.run(["/bin/zsh", str(SCRIPTS / "refresh-news.sh")],
                                 cwd=ROOT, timeout=600, capture_output=False)
        results['refresh_news'] = prereq.returncode == 0
    except Exception as e:
        results['refresh_news'] = False
        print(f"\n[refresh_news] ✗ ERROR: {e}", flush=True)

    # Stage 1: Fundamentals (fresh P/E, market cap, earnings dates)
    results['fundamentals'] = run_script('fundamentals')

    # Stage 2: Parallel web research (catalysts, news, macro)
    # news.py MUST run before catalysts/earnings_research - it writes
    # data/feed-raw.txt (X+Gmail+Telegram combined) that they read.
    results['news'] = run_script('news')
    results['catalysts'] = run_script('catalysts')
    # 3-month dated calendar for key names - internal 3-day freshness TTL, so
    # most days this returns in seconds; a full refresh runs 4 parallel
    # research calls and can take ~15 min.
    results['catalyst_calendar'] = run_script('catalyst_calendar', timeout=2100)
    # Stock Pages research - each has an internal freshness TTL so most days
    # these are fast no-ops; full refreshes are long, hence generous timeouts.
    results['stock_page_extras'] = run_script('stock_page_extras', '--fresh-days', '7', timeout=3600)
    results['delivery_war'] = run_script('delivery_war', timeout=900)  # 3-day TTL internally
    results['model_launches'] = run_script('model_launches', timeout=1800)  # 14-day TTL internally
    results['deal_map'] = run_script('deal_map', timeout=1000)  # 7-day TTL internally
    # segment financials refresh only for names whose newest researched quarter
    # is >100 days old (a fresh print has likely landed) - usually a no-op
    results['deep_financials'] = run_script('deep_financials', '--stale-only', timeout=3600)
    results['macro_events'] = run_script('macro_events')

    # Stage 3: Price movements & research
    results['movements'] = run_script('movements')
    results['earnings_research'] = run_script('earnings_research')
    results['movements_research'] = run_script('movements_research', timeout=900)

    # Stage 4: Merge
    results['build'] = run_script('build')

    # Stage 4b: Reprice forward P/E to today's close - cheap, no API calls,
    # keeps "forward P/E" honest even though the underlying EPS estimates
    # (forward_pe.py) are only researched occasionally, not daily.
    if results['build']:
        results['reprice_forward_pe'] = run_script('reprice_forward_pe')
    else:
        results['reprice_forward_pe'] = False

    # Stage 5: Deploy to both public GitHub Pages repos (stock-monitor + ai-supply-chain)
    if results['build']:
        print(f"\n{'='*60}\n[{datetime.datetime.now().strftime('%H:%M:%S')}] Deploying...\n", flush=True)
        try:
            deploy = subprocess.run(
                ["/bin/zsh", str(SCRIPTS / "deploy-pages.sh")],
                cwd=ROOT, timeout=300, capture_output=False,
            )
            results['deploy'] = deploy.returncode == 0
            print(f"\n[deploy] {'✓ OK' if results['deploy'] else '✗ FAILED'}", flush=True)
        except Exception as e:
            results['deploy'] = False
            print(f"\n[deploy] ✗ ERROR: {e}", flush=True)
    else:
        results['deploy'] = False
        print("\n[deploy] ✗ SKIPPED (build failed)", flush=True)

    # Stage 6: Daily Telegram brief - only once build succeeded, since it's
    # built from the same freshly-merged data the site uses.
    if results['build']:
        results['daily_brief'] = run_script('daily_brief')
    else:
        results['daily_brief'] = False
        print("\n[daily_brief] ✗ SKIPPED (build failed)", flush=True)

    # Summary
    end = datetime.datetime.now()
    elapsed = (end - start).total_seconds()
    ok = sum(1 for v in results.values() if v)
    total = len(results)

    print(f"\n{'='*60}\n[PIPELINE COMPLETE] {end.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"[SUMMARY] {ok}/{total} stages OK, elapsed {elapsed:.0f}s\n", flush=True)

    for stage, success in results.items():
        status = "✓" if success else "✗"
        print(f"  {status} {stage}", flush=True)

    return 0 if ok == total else 1

if __name__ == "__main__":
    lock_fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] another orchestrate.py run holds the lock, skipping", flush=True)
        sys.exit(0)
    try:
        sys.exit(main())
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
