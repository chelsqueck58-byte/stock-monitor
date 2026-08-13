#!/usr/bin/env python3
"""Daily stock-monitor pipeline orchestration.

Runs all data collection scripts sequentially with sensible checkpoints:
- Stage 1: Fetch fundamentals (P/E, market cap, earnings dates)
- Stage 2: Parallel web searches (catalysts, news, IV, macro)
- Stage 3: Detect and research price movements
- Stage 4: Merge all sources and deploy

Run: .venv/bin/python scripts/orchestrate.py
"""
import subprocess
import sys
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

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

    # Stage 1: Fundamentals (fresh P/E, market cap, earnings dates)
    results['fundamentals'] = run_script('fundamentals')

    # Stage 2: Parallel web research (catalysts, news, IV, macro)
    # These can theoretically run in parallel, but running sequentially is safer for API limits
    results['catalysts'] = run_script('catalysts')
    results['news'] = run_script('news')
    results['ivdata'] = run_script('ivdata')
    results['macro_events'] = run_script('macro_events')

    # Stage 3: Price movements & research
    results['movements'] = run_script('movements')
    results['earnings_research'] = run_script('earnings_research')
    results['movements_research'] = run_script('movements_research', timeout=900)

    # Stage 4: Merge & deploy
    results['build'] = run_script('build')

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
    sys.exit(main())
