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
CATALYSTS = ROOT / "data" / "catalysts.json"
TELE_DOCS = Path.home() / ".claude" / "tele-docs"
TELEGRAM = Path.home() / ".claude" / "skills" / "telegram-sender" / "send.sh"


def load_tele_research():
    """Parse catalysts.md/fundamentals.md/historicals.md (your own Telegram
    research, curated by tele-memory.py) into {ticker: {catalysts, fundamentals,
    historicals}} so it can be merged onto each instrument for the website."""
    import re
    out = {}
    for name, key in (("catalysts.md", "catalysts"), ("fundamentals.md", "fundamentals"),
                       ("historicals.md", "historicals")):
        p = TELE_DOCS / name
        if not p.exists():
            continue
        text = p.read_text()
        for m in re.finditer(r"^## (.+?)\n(.*?)(?=\n## |\Z)", text, re.DOTALL | re.MULTILINE):
            ticker, body = m.group(1).strip(), m.group(2).strip()
            if body:
                out.setdefault(ticker, {})[key] = body
    return out


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


def fresh_flags(entries, now):
    """One alert per level per HKT calendar day - not a rolling-hour cooldown.

    Keyed by (instrument, window, kind) WITHOUT the exact price. The old key
    included the rounded price, but S/R prices recompute slightly build to
    build as the rolling swing-cluster window shifts (e.g. SINGTEL resistance
    4.48 -> 4.49 twelve hours apart) - that made the same level look like a
    brand-new touch and fire a second alert same-day for what a human would
    call one touch. Dropping price from the key fixes that at the cost of
    only ever alerting once per level per day even if it's genuinely retested.
    """
    HKT = timezone(timedelta(hours=8))
    today = now.astimezone(HKT).date().isoformat()
    state = load_alert_state()
    triggered = []
    for entry in entries:
        for flag in entry["flags"]:
            key = f"{entry['id']}|{flag['window']}|{flag['kind']}"
            if state.get(key) == today:
                continue
            state[key] = today
            triggered.append({
                "id": entry["id"], "label": entry["label"], "kind": flag["kind"],
                "window": flag["window"], "price": flag["price"],
                "distance_pct": flag["distance_pct"],
            })
    # Same-day dedup means anything not from today is dead weight.
    state = {k: v for k, v in state.items() if v == today}
    save_alert_state(state)
    return triggered


def format_levels_message(triggered):
    """Split into Support / Resistance sections, and consolidate a ticker that
    touched the same kind of level in both the 1m and 3m windows into ONE
    line instead of two - a ticker used to appear twice in a row (once per
    window) with the same name and often the same price, reading as a typo
    or duplicate rather than two genuinely distinct windows agreeing."""
    grouped = {}
    for t in triggered:
        key = (t["id"], t["kind"])
        grouped.setdefault(key, {"label": t["label"], "hits": []})
        grouped[key]["hits"].append((t["window"], t["price"], t["distance_pct"]))

    def section(kind, icon, title):
        rows = [(tid, g) for (tid, k), g in grouped.items() if k == kind]
        if not rows:
            return ""
        # No silent cap - send.sh already chunks over-long Telegram messages,
        # so there's no reason to drop real levels here (a low hardcoded cap
        # silently dropped 5 genuine touches on an entirely ordinary day).
        lines = []
        for tid, g in rows:
            hits = g["hits"]
            distinct_prices = {round(p, 2) for _, p, _ in hits}
            if len(hits) > 1 and len(distinct_prices) == 1:
                # same price across windows - one line, windows joined
                windows = " & ".join(w for w, _, _ in hits)
                _, price, dist = hits[0]
                lines.append(f"• {g['label']} {price:,.2f} ({windows}, {dist:+.1f}%)")
            else:
                # genuinely different prices per window - keep them distinct
                sub = ", ".join(f"{w} {p:,.2f} ({d:+.1f}%)" for w, p, d in hits)
                lines.append(f"• {g['label']} — {sub}")
        return f"\n{icon} <b>{title}</b>\n" + "\n".join(lines)

    parts = [f"<b>Levels touched</b> ({len(triggered)})"]
    parts.append(section("support", "🟢", "Support"))
    parts.append(section("resistance", "🔴", "Resistance"))
    return "\n".join(p for p in parts if p)


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

    def load_json(path):
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    events = load_json(EVENTS)
    # earnings.json / catalysts.json store {"line":..., "fetched":...} (freshness
    # tracking for the research scripts) — unwrap to the plain line for the site.
    earnings = {k: v.get("line") for k, v in load_json(EARNINGS).items() if isinstance(v, dict)}
    catalysts = {k: v.get("line") for k, v in load_json(CATALYSTS).items() if isinstance(v, dict)}
    tele_research = load_tele_research()

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
                    "catalyst": catalysts.get(member["id"]),
                    "tele": tele_research.get(member["id"]),
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

    triggered = fresh_flags(entries, datetime.now(timezone.utc))
    if triggered:
        notify(format_levels_message(triggered))
    if failures:
        notify(f"⚠ <b>Data gap</b>: {len(failures)} dropped — {', '.join(failures[:10])}")
    if reused:
        notify(f"⚠ <b>Stale</b>: {len(reused)} names reused last-good — {', '.join(reused[:10])}")


if __name__ == "__main__":
    main()
