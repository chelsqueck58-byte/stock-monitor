"""Full IPO-to-today daily closes for the Gold & Jewelry cohort, for the
tab's stock-vs-gold performance chart. Separate from each instrument's
regular `bars` field (site/data.json), which is intentionally capped at 730
days for the cards/technicals (SMA/RSI/52-week range) - this chart wants
each stock's real full history, not a 2-year window, and mixing a much
longer series into `bars` would change those calculations' behavior
elsewhere on the site.

Run:  .venv/bin/python scripts/gold_stock_full_history.py
"""
import json
from pathlib import Path

from sources import YahooSource

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE = ROOT / "config" / "universe.json"
OUT_PATHS = [
    ROOT / "data" / "gold-jewelry-full-history.json",
    ROOT / "site" / "gold-jewelry-full-history.json",
    ROOT / "gold-jewelry-full-history.json",
]
TICKERS = ["6181", "1929", "0590", "0116", "002867"]
LOOKBACK_DAYS = 20000  # ~54 years - Yahoo just returns as much real history as it has


def main():
    universe = json.loads(UNIVERSE.read_text())
    yahoo_by_id = {m["id"]: m.get("yahoo", m["id"]) for g in universe["groups"] for m in g["members"]}
    source = YahooSource()

    out = {}
    for tid in TICKERS:
        symbol = yahoo_by_id.get(tid, tid)
        bars, _ = source.fetch_bars({"id": tid, "yahoo": symbol}, lookback_days=LOOKBACK_DAYS)
        out[tid] = [{"d": b["date"], "c": b["close"]} for b in bars if b["close"] is not None]
        print(f"  {tid:8} {len(out[tid])} bars, {out[tid][0]['d']} -> {out[tid][-1]['d']}")

    for path in OUT_PATHS:
        path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"gold_stock_full_history: {len(out)} tickers -> {OUT_PATHS[0]}")


if __name__ == "__main__":
    main()
