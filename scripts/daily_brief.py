"""Compose and send the Morning Brief to Telegram: watchlist movers >=5%,
a live index snapshot, the macro calendar, overnight catalysts, analyst
flags and a China section - all in one themed push, grounded in the
pipeline's own data plus X posts, refined and formatted by Claude with
WebSearch (same pattern as x-reader's x_digest.py and catalysts.py).

Sent once per day - a state file tracks the last date sent so a watchdog
catch-up run later the same day (or a manual rerun) doesn't resend it.

Usage: daily_brief.py [--force]
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_JSON = ROOT / "site" / "data.json"
MOVES = ROOT / "data" / "moves.json"
MACRO_EVENTS = ROOT / "data" / "macro-events.json"
STATE = ROOT / "data" / "telegram-brief-state.json"
X_DIGEST = Path.home() / "x-reader" / "digest.json"
TELEGRAM = Path.home() / ".claude" / "skills" / "telegram-sender" / "send.sh"

MOVE_THRESHOLD = 5.0
CALENDAR_DAYS = 14
X_DIGEST_MAX_AGE_HOURS = 48  # x-reader's own scrape has a 3h TTL; this is just
                              # a sanity cap so a badly stale file isn't fed in as "recent"

HKT = datetime.timezone(datetime.timedelta(hours=8))

PROMPT = """You are composing today's "Morning Brief" for a private markets Telegram \
channel, read by an equity research analyst. Use WebSearch to verify and fill in \
anything below marked as needing a live lookup - do not invent numbers.

TODAY: {today} ({time_hkt} HKT)

WATCHLIST MOVERS >= {threshold:.0f}% (each in its own most recent session - US and Asia \
names carry different calendar dates since Asia has already closed its next session \
while the US is still mid-day):
{movers_block}

FULL WATCHLIST, for context on what did NOT move (group: tickers, change% (date)):
{universe_block}

MACRO CALENDAR (next {days} days, already known - do not re-research dates, just add \
one line of framing/context per event, e.g. what's being debated or priced in):
{macro_block}

SPY/QQQ (already known - do not WebSearch these, only VIX/US 10Y/DXY/Brent below need it):
{index_block}

X POSTS FROM FOLLOWED MARKET ACCOUNTS (last ~24h - never print an @handle; if a claim's \
only attribution is the handle itself, state it without attribution; if it cites an \
outlet, name the outlet):
{x_block}

Produce the brief in EXACTLY this structure and order, Telegram HTML only (<b>, <i>, \
<code> - no markdown, no other tags):

🌏 <b>Morning Brief — {today} {time_hkt} HKT</b>

🚨 <b>Movers (≥{threshold:.0f}%)</b>
One line per mover: ticker, % move, live current price (already given below - do not \
WebSearch it), then a tight sentence naming the actual news/catalyst driving it \
(WebSearch for the real reason if \
the data given doesn't already explain it - never leave a mover unexplained). Follow \
each with a "→" line giving the one-sentence read-through / why it matters. If a name \
in the full watchlist moved a lot yesterday but not in today's session, note that it's \
already stale rather than listing it as a fresh mover. If nothing cleared the bar, say \
so plainly and name a few watchlist groups that stayed in a tight range as evidence you \
checked, not just asserted it.

📊 <b>Index Snapshot</b>
SPY, QQQ: use the levels given above, do not WebSearch them. VIX, US 10Y yield, DXY, \
Brent crude: WebSearch each. One line each, with the day's % or bp change.

📅 <b>Macro Calendar (next {days} days)</b>
One bullet per calendar event above, each with a sentence of framing (what's being \
debated, what's priced in, why it matters) - use WebSearch if you need the current \
market-implied odds or context to frame it accurately.

🔍 <b>Key Catalysts (last 24h)</b>
Bulleted, dated where relevant. Company or macro news from the last 24h that isn't \
already covered under Movers - draw from the X posts above and WebSearch for anything \
that needs verifying or completing.

⚡ <b>Analyst Flags</b>
Any price target changes, upgrades/downgrades from the last 24h relevant to today's \
movers or watchlist names - WebSearch for these.

🇨🇳 <b>China Pulse</b>
A short paragraph on China-specific market developments (HSI/HSTech, PBOC/regulatory, \
China watchlist names) - use the X posts and WebSearch.

Every section must appear, in this order, with these exact headers and emoji. No \
preamble, no closing commentary, no "here is the brief"."""


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def ask_claude(prompt, timeout=900):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    for attempt in range(3):
        try:
            r = subprocess.run(["claude", "-p", prompt, "--allowedTools", "WebSearch"],
                                capture_output=True, text=True, env=env, timeout=timeout)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        if attempt < 2:
            import time
            time.sleep(2)
    return ""


def movers_and_universe(moves, data_json):
    """Each instrument's own last_date is its own most recent close - US and
    Asia names legitimately carry different calendar dates at any given HKT
    run time (US is still mid-session when HK has already closed the next
    day), so movers must be judged per-instrument, never against one shared
    "session date" for the whole universe."""
    instruments = data_json.get("instruments", [])
    if not instruments:
        return None, "", ""

    overall_latest = max((i.get("last_date") for i in instruments if i.get("last_date")),
                         default=None)

    movers = []
    universe_lines = []
    by_group = {}
    for inst in instruments:
        tid = inst.get("id")
        label = inst.get("label", tid)
        group = inst.get("group", "Other")
        pct = inst.get("change_pct")
        last_date = inst.get("last_date")
        if pct is None or last_date is None:
            continue
        by_group.setdefault(group, []).append(f"{tid} {pct:+.1f}% ({last_date})")
        if abs(pct) >= MOVE_THRESHOLD:
            move_history = (moves.get(tid, {}).get("moves") or [{}])[0]
            reason = move_history.get("reason", "") if move_history.get("d") == last_date else ""
            last_close = inst.get("last_close")
            price_str = f", last close {last_close}" if last_close is not None else ""
            movers.append(f"- {tid} ({label}, {group}): {pct:+.1f}% on {last_date}{price_str}"
                          + (f" — known reason: {reason}" if reason else " — reason unknown, WebSearch it"))

    for group, rows in by_group.items():
        universe_lines.append(f"{group}: " + ", ".join(rows))

    movers_block = "\n".join(movers) if movers else \
        f"None. Every watchlist name stayed within +/-{MOVE_THRESHOLD:.0f}% in its most recent session."
    return overall_latest, movers_block, "\n".join(universe_lines)


def index_snapshot_block(data_json):
    by_id = {i.get("id"): i for i in data_json.get("instruments", [])}
    lines = []
    for tid in ("SPY", "QQQ"):
        inst = by_id.get(tid)
        if inst and inst.get("last_close") is not None:
            lines.append(f"{tid}: {inst['last_close']} ({inst.get('change_pct', 0):+.1f}%) "
                         f"as of {inst.get('last_date')}")
    return "\n".join(lines) if lines else "(SPY/QQQ not found in data.json - WebSearch these too)"


def macro_calendar_block(macro_events, today, days):
    cutoff = (datetime.date.fromisoformat(today) + datetime.timedelta(days=days)).isoformat()
    events = [e for e in macro_events.get("events", []) if today <= e.get("date", "") <= cutoff]
    events.sort(key=lambda e: e["date"])
    if not events:
        return "None scheduled."
    return "\n".join(f"- {e['date']}: {e['event']} ({e.get('source', '')})" for e in events)


def x_posts_block(max_posts=80):
    if not X_DIGEST.exists():
        return "(no X digest available)"
    age_hours = (datetime.datetime.now().timestamp() - X_DIGEST.stat().st_mtime) / 3600
    if age_hours > X_DIGEST_MAX_AGE_HOURS:
        return f"(X digest is {age_hours:.0f}h old - likely stale, use with caution)"
    posts = load_json(X_DIGEST, [])
    ok = [p for p in posts if "error" not in p][:max_posts]
    if not ok:
        return "(no posts)"
    return "\n".join(f"- {p.get('handle', '?')}: {p.get('text', '')}" for p in ok)


def build_message(today, time_hkt):
    data_json = load_json(DATA_JSON, {})
    moves = load_json(MOVES, {})
    macro_events = load_json(MACRO_EVENTS, {})

    latest_date, movers_block, universe_block = movers_and_universe(moves, data_json)
    if latest_date is None:
        return None

    prompt = PROMPT.format(
        today=today,
        time_hkt=time_hkt,
        threshold=MOVE_THRESHOLD,
        movers_block=movers_block,
        universe_block=universe_block,
        days=CALENDAR_DAYS,
        macro_block=macro_calendar_block(macro_events, today, CALENDAR_DAYS),
        index_block=index_snapshot_block(data_json),
        x_block=x_posts_block(),
    )
    return ask_claude(prompt)


def already_sent_today(today):
    state = load_json(STATE, {})
    return state.get("last_sent") == today


def mark_sent(today):
    STATE.write_text(json.dumps({"last_sent": today}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="send even if already sent today")
    args = ap.parse_args()

    now = datetime.datetime.now(HKT)
    today = now.date().isoformat()

    if not args.force and already_sent_today(today):
        print(f"daily brief already sent for {today}, skipping")
        return 0

    message = build_message(today, now.strftime("%H:%M"))
    if not message:
        print("daily brief: no data to build from", file=sys.stderr)
        return 1

    result = subprocess.run([str(TELEGRAM), message])
    if result.returncode != 0:
        print("daily brief send FAILED", file=sys.stderr)
        return 1

    mark_sent(today)
    print(f"daily brief sent for {today}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
