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
BIG = 5.0        # research threshold — every move this size or bigger
CAP = 999        # comprehensive: keep them all
MARKET_MOVE = 1.5  # if the index moved >= this same-direction, tag market-wide
LOOKBACK_DAYS = 450  # ~15 months - see module docstring for why this isn't 730


def daily_moves(bars):
    out = []
    for i in range(1, len(bars)):
        c, pc = bars[i]["close"], bars[i - 1]["close"]
        if c and pc:
            out.append({"d": bars[i]["date"], "pct": round((c / pc - 1) * 100, 1), "px": round(c, 2)})
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
            detected = [m for m in daily_moves(bars_by_id[tid]) if abs(m["pct"]) >= BIG]
            prior_by_date = {m["d"]: m for m in existing.get(tid, {}).get("moves", [])}

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
