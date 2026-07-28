"""Macro/market-wide event calendar (Fed rate decisions, CPI, NFP, PMI, and
similar central bank/economic releases) for the next ~30 days - these move
the whole market and aren't tied to any single ticker, so they don't fit
data/events.json's per-ticker schema. Writes data/macro-events.json:
{"events": [{"date":"YYYY-MM-DD","event":"...","source":"..."}], "fetched":...}.

Added 2026-07-28 after a real miss: an earlier research pass produced a 4-week
catalyst calendar that omitted the FOMC meeting entirely - the single most
market-wide scheduled event there is. Company-specific catalyst scripts
(catalysts.py, earnings_research.py) have no mechanism to surface something
not tied to a ticker; this closes that gap specifically.

FRESHNESS TTL: macro dates are known well in advance and rarely change day to
day (Fed meets ~8x/year, CPI/NFP monthly on a fixed schedule) - re-searching
daily would be pure waste. Refreshed weekly.
"""
import os
import re
import json
import datetime
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "macro-events.json"
FRESH_DAYS = 7
WINDOW = 30


def ask_claude(prompt, timeout=300):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run(["claude", "-p", prompt, "--allowedTools", "WebSearch"],
                           capture_output=True, text=True, env=env, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def parse_json(text):
    if "```" in text:
        text = re.sub(r"```(json)?", "", text)
    s, e = text.find("["), text.rfind("]")
    if s < 0 or e < 0:
        return []
    try:
        return json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return []


def main():
    state = {}
    if OUT.exists():
        try:
            state = json.loads(OUT.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    today = datetime.date.today()
    fetched = state.get("fetched")
    if fetched:
        try:
            age = (today - datetime.date.fromisoformat(fetched)).days
            if age < FRESH_DAYS:
                print(f"macro events still fresh ({age}d old), skipping")
                return
        except ValueError:
            pass

    prompt = (
        f"Use web search to find major US/global macro-economic events from {today.isoformat()} "
        f"through the next {WINDOW} days INCLUSIVE (today and tomorrow count - do not skip an event "
        "just because it's imminent or already in progress over multiple days). Specifically check "
        "for, and do not omit if found: (1) the CURRENT or nearest FOMC/Fed meeting and rate decision "
        "date - search explicitly for 'FOMC meeting schedule' for the current date, this is the single "
        "most important one and must not be missed; (2) US CPI; (3) US nonfarm payrolls; (4) US PMI "
        "(ISM manufacturing/services); (5) any other market-moving central bank decision (ECB, BOJ, "
        "PBOC) or major scheduled economic release (PPI, retail sales, Jackson Hole, etc). For each, "
        "give the exact date and a short label (e.g. 'FOMC rate decision', 'US CPI (July)'). Grounded "
        "only - omit anything you can't find a real source for; never invent a date.\n\n"
        'Output ONLY a JSON array: [{"date":"YYYY-MM-DD","event":"short label","source":"outlet"}]. '
        "No prose."
    )
    raw = ask_claude(prompt)
    result = parse_json(raw)
    if not result and raw.strip():
        # Model narrated instead of returning pure JSON (observed elsewhere in
        # this pipeline under WebSearch tool use) - one retry with a blunt
        # reformat instruction, reusing what it already found.
        fixup = (
            'Extract ONLY the JSON array from this text, matching '
            '[{"date":"YYYY-MM-DD","event":"...","source":"..."}]. '
            "Reply with ONLY the JSON array, nothing else.\n\n" + raw
        )
        result = parse_json(ask_claude(fixup))

    events = []
    for e in result:
        if not (isinstance(e, dict) and e.get("date") and e.get("event")):
            continue
        try:
            d = datetime.date.fromisoformat(e["date"])
        except ValueError:
            continue
        if today <= d <= today + datetime.timedelta(days=WINDOW):
            events.append({"date": e["date"], "event": str(e["event"])[:120], "source": str(e.get("source", ""))[:60]})

    events.sort(key=lambda ev: ev["date"])

    # A 30-day US/global macro window is essentially never genuinely empty -
    # zero results is a signal the call failed, not that nothing's scheduled.
    # Keep the prior (still-useful) events and DON'T advance `fetched`, so the
    # next run retries instead of the freshness TTL silently hiding a blank
    # calendar for a week. Only overwrite when this run actually found something.
    if not events and state.get("events"):
        print(f"got 0 events this run (prior data had {len(state['events'])}) - "
              "keeping prior data, not marking fresh so this retries next run")
        return

    OUT.write_text(json.dumps({"events": events, "fetched": today.isoformat()},
                               ensure_ascii=False, separators=(",", ":")))
    print(f"macro events: {len(events)} found -> {OUT}")
    for ev in events:
        print(f"  {ev['date']}: {ev['event']} [{ev['source']}]")


if __name__ == "__main__":
    main()
