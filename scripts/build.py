"""Pull bars, compute levels, write data.json for the site.

Usage: build.py [--source yahoo|ibkr] [--no-alert]
"""
import argparse
import json
import os
import re
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
EARNINGS = ROOT / "data" / "earnings.json"
EVENTS = ROOT / "data" / "events.json"
CATALYSTS = ROOT / "data" / "catalysts.json"
MOVES = ROOT / "data" / "moves.json"
MACRO_EVENTS = ROOT / "data" / "macro-events.json"
TELE_DOCS = Path.home() / ".claude" / "tele-docs"
TELEGRAM = Path.home() / ".claude" / "skills" / "telegram-sender" / "send.sh"

EARNINGS_KEYWORDS = ("earnings", "eps", "revenue", "quarter", "guidance", "results",
                     " q1", " q2", " q3", " q4", "profit", "loss")
STOPWORDS = {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "at", "with",
             "its", "new", "this", "day", "event"}


def _opens_with_earnings_ref(reason):
    """True if a quarter reference (Q1-Q4) or "earnings" appears within the
    reason's first 3 words - the real signal for "this reason IS about that
    report", found by testing against real data. A fixed character-count
    cutoff was tried first and proved fragile: it's pushed around by variable-
    length verb/adjective openers real entries actually use ("Blowout Q4...",
    "Fiscal Q3...", "Beat Q1...", "Missed Q1..." all put the quarter at word
    2, not a fixed character offset). A false match like "Investor re-rating
    on Q1 results" puts Q1 at word 4 - reliably later, since it's referencing
    a report in passing rather than describing it.

    EV/auto delivery reports ("Q2 2026 deliveries ~62,682 units missed...")
    open with the exact same "Q[1-4]" pattern but are a DIFFERENT event from
    quarterly earnings - confirmed live on TSLA/XPeng/NIO/Li Auto, all
    wrongly tagged as earnings moves until this exclusion. A delivery report
    only counts if "earnings" is also explicit nearby (a wider 6-word window
    catches that combo without re-admitting the delivery-only false positives,
    which never mention "earnings" that early)."""
    words6 = re.findall(r"[a-zA-Z0-9]+", reason)[:6]
    head6 = " ".join(words6).lower()
    head3 = " ".join(words6[:3]).lower()
    has_ref = bool(re.search(r"\bq[1-4]\b", head3)) or "earnings" in head3
    if not has_ref:
        return False
    if "deliver" in head6 and "earnings" not in head6:
        return False
    return True


def find_prev_earnings_reactions(moves, limit=4):
    """The ticker's last `limit` earnings REPORT DAY reactions — a cheap
    heuristic, not a search. Reuses what movements_research.py already
    grounded and sourced instead of asking an LLM to recall history it might
    get wrong. Returns newest-first, fewer than `limit` if that's all we have.

    Among moves whose reason opens with an earnings reference (see
    _opens_with_earnings_ref), greedily clusters candidates within 7 days of
    each other into one reporting cycle (the report day plus a smaller
    follow-on drift/analyst-note day that also happens to open with the same
    "Q_ earnings" phrasing, confirmed live on AVGO: 2026-06-04 -12.6% real
    report day vs 2026-06-10 -5.1% follow-on) and takes each cycle's larger-
    magnitude move as that quarter's representative, since the report day
    itself is reliably the decisive move.
    """
    candidates = [m for m in moves if m.get("reason") and _opens_with_earnings_ref(m["reason"])]
    if not candidates:
        return []
    candidates.sort(key=lambda x: x["d"], reverse=True)

    # Chain/single-linkage clustering, not fixed-anchor: a candidate joins a
    # cluster if it's within 7 days of ANY existing member, not just the
    # first one added. Fixed-anchor missed a real 3-date chain on SE (05-14,
    # 05-12, 05-06 - all one Q1 2026 cycle) because 05-06 sits 8 days from
    # 05-14 directly, even though it's only 6 days from 05-12 in between.
    clusters = []
    for c in candidates:
        c_date = datetime.strptime(c["d"], "%Y-%m-%d")
        for cluster in clusters:
            if any(abs((c_date - datetime.strptime(m["d"], "%Y-%m-%d")).days) <= 7 for m in cluster):
                cluster.append(c)
                break
        else:
            clusters.append([c])

    reactions = []
    for cluster in clusters[:limit]:
        m = max(cluster, key=lambda x: abs(x["pct"]))
        reactions.append({"date": m["d"], "pct": m["pct"], "reason": m["reason"], "source": m.get("source")})
    return reactions


def find_prev_event_reaction(moves, event_desc):
    """Best keyword-overlap match between an upcoming event's description and
    past researched move reasons for the same ticker — e.g. an upcoming
    'Advancing AI 2026' event matches a past move reasoned around 'Advancing
    AI'. Heuristic and approximate by design (chosen over a fresh search per
    event); returns nothing rather than a low-confidence guess."""
    words = {w.lower() for w in re.findall(r"[a-zA-Z]+", event_desc)
             if w.lower() not in STOPWORDS and len(w) > 2}
    if not words:
        return None
    best, best_score = None, 0
    for m in moves:
        if not m.get("reason"):
            continue
        reason_words = {w.lower() for w in re.findall(r"[a-zA-Z]+", m["reason"])}
        score = len(words & reason_words)
        if score > best_score:
            best_score, best = score, m
    if best_score >= 1:
        return {"date": best["d"], "pct": best["pct"], "reason": best["reason"], "source": best.get("source")}
    return None


def load_tele_research():
    """Parse catalysts.md/fundamentals.md/historicals.md (your own Telegram
    research, curated by tele-memory.py) into {ticker: {catalysts, fundamentals,
    historicals}} so it can be merged onto each instrument for the website."""
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

    def load_json(path):
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    events = load_json(EVENTS)
    # earnings.json / catalysts.json store {"focus"/"line":..., "fetched":...}
    # (freshness tracking for the research scripts) — unwrap to the plain
    # string for the site.
    # .get("focus") is the current key; .get("line") is a fallback for entries
    # written before the 2026-07-23 schema rename that haven't been re-
    # researched since (FRESH_DAYS TTL means most won't be for days) - without
    # this fallback every ticker's investor-focus line went silently blank.
    earnings = {k: (v.get("focus") or v.get("line")) for k, v in load_json(EARNINGS).items() if isinstance(v, dict)}
    catalysts = {k: v.get("line") for k, v in load_json(CATALYSTS).items() if isinstance(v, dict)}
    tele_research = load_tele_research()
    moves_data = load_json(MOVES)
    # Macro/market-wide events (Fed, CPI, NFP, PMI...) - not tied to any single
    # ticker, so they live at the payload level rather than per-instrument.
    macro_events = load_json(MACRO_EVENTS).get("events", [])

    def build_metadata(tid):
        """Everything that doesn't depend on fresh bars — fundamentals, news,
        IV, catalysts, earnings focus/history, events. Shared by both the
        success path and the stale-price fallback below: a cached entry used
        to freeze ALL of this along with the price when a fetch failed, not
        just the price, so a ticker with one bad Yahoo request would show
        yesterday's catalysts/earnings-focus/events too. This runs regardless
        of whether bars came back, so only price/levels/technicals actually
        stay frozen on a fallback - everything else refreshes every run."""
        ticker_moves = moves_data.get(tid, {}).get("moves", [])
        ticker_events = events.get(tid)
        if ticker_events:
            for ev in ticker_events:
                ev["prev_reaction"] = find_prev_event_reaction(ticker_moves, ev.get("event", ""))
        return {
            "fund": fundamentals.get(tid),
            "news": news.get(tid),
            "earn": earnings.get(tid),
            "prev_earnings": find_prev_earnings_reactions(ticker_moves),
            "events": ticker_events,
            "catalyst": catalysts.get(tid),
            "tele": tele_research.get(tid),
        }

    entries = []
    failures = []
    reused = []

    for group in config["groups"]:
        for member in group["members"]:
            try:
                bars, meta = source.fetch_bars(member)
                # A per-member "min_bars" override lets a genuinely-recent
                # IPO (e.g. CXMT, listed 2026-07-27) show up right away with
                # whatever's computable - trend_signal() already degrades
                # gracefully below 50 bars (sma50/trend fall back to
                # None/"flat"), so this floor exists to catch a broken
                # symbol mapping, not to gate every ticker at 100+ days.
                minimum = member.get("min_bars", settings.get("min_bars", 210))
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
                })
                entry.update(build_metadata(member["id"]))
                if group["name"] == "Index ETF":
                    entry["idea"] = None  # market context, not a stock pick
                entries.append(entry)
                print(f"  ok   {member['id']:<8} {entry['last_close']:>12,.2f}  "
                      f"{len(bars)} bars")
            except SourceError as exc:
                cached = prev.get(member["id"])
                if cached:
                    cached["stale"] = True
                    cached.update(build_metadata(member["id"]))
                    entries.append(cached)
                    reused.append(member["id"])
                    print(f"  STALE {member['id']:<8} reused last-good price, metadata refreshed ({exc})")
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
        "macro_events": macro_events,
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

    # triggered = fresh_flags(entries, datetime.now(timezone.utc))
    # if triggered:
    #     notify(format_levels_message(triggered))
    if failures:
        notify(f"⚠ <b>Data gap</b>: {len(failures)} dropped — {', '.join(failures[:10])}")
    if reused:
        notify(f"⚠ <b>Stale</b>: {len(reused)} names reused last-good — {', '.join(reused[:10])}")


if __name__ == "__main__":
    main()
