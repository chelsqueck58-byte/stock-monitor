"""Full IPO-to-today daily closes for the Gold & Jewelry cohort, for the
tab's stock-vs-gold performance chart. Separate from each instrument's
regular `bars` field (site/data.json), which is intentionally capped at 730
days for the cards/technicals (SMA/RSI/52-week range) - this chart wants
each stock's real full history, not a 2-year window, and mixing a much
longer series into `bars` would change those calculations' behavior
elsewhere on the site.

Uses split/dividend-ADJUSTED close (Yahoo's "adjclose"), not raw close -
confirmed necessary: Chow Tai Seng (002867.SZ) had two 1.5:1 splits (2019,
2021) that raw close doesn't account for, which would otherwise show an
artificial price drop at each split date with no corresponding change in
real shareholder return. Regular `bars` elsewhere on the site uses raw
close deliberately (matches the quoted trading price over a short 2-year
window where splits are unlikely) - this script's multi-decade window
makes split-adjustment necessary for an accurate return comparison.

Run:  .venv/bin/python scripts/gold_stock_full_history.py
"""
import json
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE = ROOT / "config" / "universe.json"
OUT_PATHS = [
    ROOT / "data" / "gold-jewelry-full-history.json",
    ROOT / "site" / "gold-jewelry-full-history.json",
    ROOT / "gold-jewelry-full-history.json",
]
TICKERS = ["6181", "1929", "0590", "0116", "002867"]
LOOKBACK_DAYS = 20000  # ~54 years - Yahoo just returns as much real history as it has
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def fetch_adjclose_bars(symbol, lookback_days):
    params = {"range": f"{lookback_days}d", "interval": "1d"}
    payload = None
    for attempt in range(3):
        try:
            r = requests.get(YAHOO_CHART.format(symbol=symbol), params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            payload = r.json()
            break
        except (requests.RequestException, ValueError):
            time.sleep(1.5 * (attempt + 1))
    if payload is None:
        raise RuntimeError(f"{symbol}: fetch failed after 3 tries")

    result = payload["chart"]["result"][0]
    stamps = result.get("timestamp") or []
    adjclose = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or []

    bars = []
    for i, stamp in enumerate(stamps):
        c = adjclose[i] if i < len(adjclose) else None
        if c is None:
            continue
        bars.append({"d": time.strftime("%Y-%m-%d", time.gmtime(stamp)), "c": round(c, 4)})
    return bars


def main():
    universe = json.loads(UNIVERSE.read_text())
    yahoo_by_id = {m["id"]: m.get("yahoo", m["id"]) for g in universe["groups"] for m in g["members"]}

    out = {}
    for tid in TICKERS:
        symbol = yahoo_by_id.get(tid, tid)
        out[tid] = fetch_adjclose_bars(symbol, LOOKBACK_DAYS)
        print(f"  {tid:8} {len(out[tid])} bars, {out[tid][0]['d']} -> {out[tid][-1]['d']}")
        time.sleep(0.3)

    for path in OUT_PATHS:
        path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"gold_stock_full_history: {len(out)} tickers (split-adjusted) -> {OUT_PATHS[0]}")


if __name__ == "__main__":
    main()
