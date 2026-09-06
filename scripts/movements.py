"""Detect significant (>=5%) daily moves over the last ~15 months for every
name, and classify each as market-wide (SPY/QQQ moved the same way) or stock-
specific. MERGES into data/moves.json — existing moves keep their researched
reason/source/checked status; only genuinely new moves are added unresearched.
Run every ~2 weeks; movements_research.py then only has to research what's new.

Fetches bars directly from the price source (same as build.py) rather than
reading site/data.json's stored bars - data.json only keeps the trailing 260
bars per instrument (for the website's chart), which used to silently cap this
script's effective detection window at ~13 months regardless of LOOKBACK_DAYS
below. Decoupled 2026-07-23 (real bug: SE's May 2025 post-earnings +8.2%/+5.9%
were undetectable even though the underlying Yahoo history had them). That fix
also surfaced ~1400 older moves nobody asked to have researched - LOOKBACK_DAYS
is now a deliberate, explicit bound instead of an accidental side-effect: 450
days comfortably covers the last 4-5 quarters of earnings (what the site's
"last 4 earnings" feature needs) without reaching back 2 full years into
history nobody's using. Doesn't affect already-researched older entries -
those stay via the merge below regardless of age, only NEW detection is bounded.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sources import SourceError, get_source

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "universe.json"
OUT = ROOT / "data" / "moves.json"
BIG = 5.0        # research threshold — every single-day move this size or bigger
CAP = 999        # comprehensive: keep them all
MARKET_MOVE = 1.5  # if the index moved >= this same-direction, tag market-wide
LOOKBACK_DAYS = 450  # ~15 months - see module docstring for why this isn't 730
GRIND_WINDOW = 5     # trading days
GRIND_BIG = 6.0      # cumulative % over GRIND_WINDOW days that flags a "slow grind" -
                     # confirmed live on META's Aug 18 -> Sep 4 2026 climb (+13.4%
                     # cumulative), which never tripped BIG on any single day (worst
                     # day was -4.4%) and so sat completely unresearched despite
                     # being a real, catalyst-driven move


def daily_moves(bars):
    out = []
    for i in range(1, len(bars)):
        c, pc = bars[i]["close"], bars[i - 1]["close"]
        if c and pc:
            out.append({"d": bars[i]["date"], "pct": round((c / pc - 1) * 100, 1), "px": round(c, 2)})
    return out


def grind_moves(bars, daily):
    """Detect multi-day 'slow grind' moves that never trip BIG on any single
    day but add up to something real over GRIND_WINDOW trading days - e.g. a
    steady climb on a string of sub-5% days. Skips any window that contains a
    day already >=BIG (that day gets researched on its own; no need to double
    up). Overlapping candidate windows are resolved by keeping the strongest
    (by |cumulative %|) and discarding anything else that shares a day with it,
    so a single real 2-3 week grind produces one flagged window, not five."""
    n = len(bars)
    big_days = {m["d"] for m in daily if abs(m["pct"]) >= BIG}
    candidates = []
    for i in range(GRIND_WINDOW, n):
        c, pc = bars[i]["close"], bars[i - GRIND_WINDOW]["close"]
        if not c or not pc:
            continue
        window_dates = [bars[j]["date"] for j in range(i - GRIND_WINDOW + 1, i + 1)]
        if any(d in big_days for d in window_dates):
            continue
        pct = round((c / pc - 1) * 100, 1)
        if abs(pct) >= GRIND_BIG:
            candidates.append({
                "d": bars[i]["date"], "pct": pct, "px": round(c, 2),
                "window_days": GRIND_WINDOW, "window_start": bars[i - GRIND_WINDOW]["date"],
                "_span": set(window_dates),
            })
    candidates.sort(key=lambda m: abs(m["pct"]), reverse=True)
    out, claimed = [], set()
    for m in candidates:
        if m["_span"] & claimed:
            continue
        claimed |= m["_span"]
        del m["_span"]
        out.append(m)
    return out


def main():
    config = json.loads(CONFIG.read_text())
    source = get_source("yahoo")  # move detection is always Yahoo-sourced, independent of PRICE_SOURCE

    existing = {}
    if OUT.exists():
        try:
            existing = json.loads(OUT.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    bars_by_id = {}
    failed = []
    for group in config["groups"]:
        for member in group["members"]:
            try:
                bars, _ = source.fetch_bars(member, LOOKBACK_DAYS)
                bars_by_id[member["id"]] = bars
            except SourceError as exc:
                failed.append(member["id"])
                print(f"  FAIL {member['id']:<8} {exc}")

    # index moves by date, to flag market-wide days
    index_by_date = {}
    for idx in ("SPY", "QQQ"):
        if idx in bars_by_id:
            for m in daily_moves(bars_by_id[idx]):
                index_by_date.setdefault(m["d"], []).append(m["pct"])

    out = {}
    new_count = 0
    for group in config["groups"]:
        if group["name"] == "Index ETF":
            continue
        for member in group["members"]:
            tid = member["id"]
            if tid not in bars_by_id:
                continue
            all_daily = daily_moves(bars_by_id[tid])
            detected = [m for m in all_daily if abs(m["pct"]) >= BIG]
            prior_by_date = {m["d"]: m for m in existing.get(tid, {}).get("moves", [])
                             if not m.get("window_days")}
            # grinds keyed by (window_start, end date) - distinct from the plain-date
            # key above so a grind ending on the same day as an unrelated single-day
            # move (different ticker-days, same calendar date) never collides
            prior_by_grind = {(m.get("window_start"), m["d"]): m
                              for m in existing.get(tid, {}).get("moves", [])
                              if m.get("window_days")}

            moves = []
            for m in detected:
                if m["d"] in prior_by_date:
                    moves.append(prior_by_date[m["d"]])  # keep researched reason/source/checked
                    continue
                idx_moves = index_by_date.get(m["d"], [])
                m["market_wide"] = any((ip > 0) == (m["pct"] > 0) and abs(ip) >= MARKET_MOVE for ip in idx_moves)
                m["reason"] = None
                m["source"] = None
                m["checked"] = False
                moves.append(m)
                new_count += 1

            for m in grind_moves(bars_by_id[tid], all_daily):
                key = (m["window_start"], m["d"])
                if key in prior_by_grind:
                    moves.append(prior_by_grind[key])
                    continue
                m["market_wide"] = False  # grinds are cumulative drift, not a dated index-wide event
                m["reason"] = None
                m["source"] = None
                m["checked"] = False
                moves.append(m)
                new_count += 1

            moves.sort(key=lambda m: m["d"], reverse=True)
            moves = moves[:CAP]
            if moves:
                out[tid] = {"label": member["label"], "moves": moves}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    total = sum(len(v["moves"]) for v in out.values())
    print(f"moves: {len(out)} names, {total} total (>= {BIG}%), {new_count} new unresearched "
          f"({len(failed)} fetch failures) -> {OUT}")


if __name__ == "__main__":
    main()
