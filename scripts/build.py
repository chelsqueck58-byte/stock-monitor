"""Pull bars, compute levels, write data.json for the site.

Usage: build.py [--source yahoo|ibkr] [--no-alert]
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from levels import summarise
from sources import SourceError, get_source

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "universe.json"
OUTPUT = ROOT / "site" / "data.json"
ALERT_STATE = ROOT / "data" / "alert_state.json"
FUNDAMENTALS = ROOT / "data" / "fundamentals.json"
NEWS = ROOT / "data" / "news.json"
IV = ROOT / "data" / "iv.json"
EARNINGS = ROOT / "data" / "earnings.json"
EVENTS = ROOT / "data" / "events.json"
TELEGRAM = Path.home() / ".claude" / "skills" / "telegram-sender" / "send.sh"


def load_config():
    try:
        return json.loads(CONFIG.read_text())
    except FileNotFoundError:
        sys.exit(f"Missing config: {CONFIG}")
    except json.JSONDecodeError as exc:
        sys.exit(f"Bad config JSON: {exc}")


def notify(message):
    """Push an alert to Telegram, if the sender is configured."""
    if not TELEGRAM.exists():
        print(f"[alert skipped, no sender] {message}")
        return
    try:
        subprocess.run([str(TELEGRAM), message], check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"[alert failed] {exc}")


def load_alert_state():
    try:
        return json.loads(ALERT_STATE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_alert_state(state):
    ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
    ALERT_STATE.write_text(json.dumps(state, separators=(",", ":")))


def fresh_flags(entries, cooldown_hours, now):
    """Return flag lines not already alerted within the cooldown window.

    Keyed by (instrument, window, kind, rounded price) so the same level sitting
    in play across the day's runs fires once, not on every run.
    """
    state = load_alert_state()
    cutoff = now - timedelta(hours=cooldown_hours)
    lines = []
    for entry in entries:
        for flag in entry["flags"]:
            key = f"{entry['id']}|{flag['window']}|{flag['kind']}|{flag['price']:.2f}"
            last = state.get(key)
            if last and datetime.fromisoformat(last) > cutoff:
                continue
            state[key] = now.isoformat()
            lines.append(
                f"{entry['label']} {flag['kind'][:3].upper()} {flag['price']:,.2f} "
                f"({flag['window']}, {flag['distance_pct']:+.1f}%)"
            )
    # Drop keys not seen for well beyond the cooldown so the file can't grow forever.
    stale = now - timedelta(hours=cooldown_hours * 8)
    state = {k: v for k, v in state.items() if datetime.fromisoformat(v) > stale}
    save_alert_state(state)
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=os.environ.get("PRICE_SOURCE", "yahoo"))
    parser.add_argument("--no-alert", action="store_true")
    args = parser.parse_args()

    config = load_config()
    settings = config["settings"]

    try:
        source = get_source(args.source)
    except SourceError as exc:
        sys.exit(str(exc))

    # Last-good cache: a transient miss reuses the prior bars rather than dropping
    # the name off the site entirely (graceful degradation).
    prev = {}
    if OUTPUT.exists():
        try:
            prev = {e["id"]: e for e in json.loads(OUTPUT.read_text()).get("instruments", [])}
        except (json.JSONDecodeError, OSError):
            pass

    fundamentals = {}
    if FUNDAMENTALS.exists():
        try:
            fundamentals = json.loads(FUNDAMENTALS.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    news = {}
    if NEWS.exists():
        try:
            news = json.loads(NEWS.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    iv = {}
    if IV.exists():
        try:
            iv = json.loads(IV.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    earnings, events = {}, {}
    for path, target in ((EARNINGS, "earnings"), (EVENTS, "events")):
        if path.exists():
            try:
                d = json.loads(path.read_text())
                if target == "earnings":
                    earnings = d
                else:
                    events = d
            except (json.JSONDecodeError, OSError):
                pass

    entries = []
    failures = []
    reused = []

    for group in config["groups"]:
        for member in group["members"]:
            try:
                bars, meta = source.fetch_bars(member)
                minimum = settings.get("min_bars", 210)
                if len(bars) < minimum:
                    raise SourceError(
                        f"{member['id']}: only {len(bars)} bars, need {minimum} "
                        f"for a valid 200DMA - check the symbol"
                    )
                entry = summarise(bars, settings)
                entry.update({
                    "id": member["id"],
                    "label": member["label"],
                    "group": group["name"],
                    "currency": meta.get("currency"),
                    "stale": False,
                    "fund": fundamentals.get(member["id"]),
                    "news": news.get(member["id"]),
                    "iv": iv.get(member["id"]),
                    "earn": earnings.get(member["id"]),
                    "events": events.get(member["id"]),
                })
                if group["name"] == "Index ETF":
                    entry["idea"] = None  # market context, not a stock pick
                entries.append(entry)
                print(f"  ok   {member['id']:<8} {entry['last_close']:>12,.2f}  "
                      f"{len(bars)} bars")
            except SourceError as exc:
                cached = prev.get(member["id"])
                if cached:
                    cached["stale"] = True
                    entries.append(cached)
                    reused.append(member["id"])
                    print(f"  STALE {member['id']:<8} reused last-good ({exc})")
                else:
                    failures.append(member["id"])
                    print(f"  FAIL {member['id']:<8} {exc}")

    if not entries:
        sys.exit("No instruments fetched - refusing to write data.json")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source.name,
        "stale_after_hours": settings["stale_after_hours"],
        "failures": failures,
        "instruments": entries,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, separators=(",", ":")))

    # Publish the moves file (History tab) alongside the site so it deploys too.
    moves_src = ROOT / "data" / "moves.json"
    if moves_src.exists():
        (OUTPUT.parent / "moves.json").write_text(moves_src.read_text())

    size_kb = OUTPUT.stat().st_size / 1024
    print(f"\nWrote {OUTPUT} ({size_kb:.0f} KB) - "
          f"{len(entries)} ok, {len(failures)} failed, source={source.name}")

    if args.no_alert:
        return

    cooldown = settings.get("alert_cooldown_hours", 18)
    triggered = fresh_flags(entries, cooldown, datetime.now(timezone.utc))
    if triggered:
        head = f"<b>Levels touched</b> ({len(triggered)})\n"
        notify(head + "\n".join(f"• {line}" for line in triggered[:20]))
    if failures:
        notify(f"⚠ <b>Data gap</b>: {len(failures)} dropped — {', '.join(failures[:10])}")
    if reused:
        notify(f"⚠ <b>Stale</b>: {len(reused)} names reused last-good — {', '.join(reused[:10])}")


if __name__ == "__main__":
    main()
