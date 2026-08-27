"""Daily China gold price (RMB/gram) for the Gold & Jewelry tab's stock-vs-
gold performance chart.

No Yahoo ticker exists for the actual Shanghai Gold Exchange Au99.99
benchmark (checked: AU9999.SS, AU=F, XAUCNY=X, 1088885 all 404). Uses
international spot/futures gold (GC=F, USD/oz) converted to RMB/gram via
the historical USD/CNY rate instead - a standard proxy, since SGE tracks
international spot closely (arbitraged through the exchange rate) and the
chart normalizes to % return anyway, which cancels out most of the small,
fairly stable SGE premium/discount over international spot.

Run:  .venv/bin/python scripts/gold_price_cny.py
"""
import json
from pathlib import Path

from sources import YahooSource

ROOT = Path(__file__).resolve().parent.parent
OUT_PATHS = [ROOT / "data" / "gold-price-cny.json", ROOT / "site" / "gold-price-cny.json", ROOT / "gold-price-cny.json"]
GRAMS_PER_TROY_OZ = 31.1034768


def main():
    source = YahooSource()
    gold_bars, _ = source.fetch_bars({"id": "GC=F", "yahoo": "GC=F"}, lookback_days=730)
    fx_bars, _ = source.fetch_bars({"id": "CNY=X", "yahoo": "CNY=X"}, lookback_days=730)

    fx_by_date = {b["date"]: b["close"] for b in fx_bars if b["close"] is not None}

    out = []
    for b in gold_bars:
        usd_per_oz = b["close"]
        usd_cny = fx_by_date.get(b["date"])
        if usd_per_oz is None or usd_cny is None:
            continue
        rmb_per_gram = round(usd_per_oz * usd_cny / GRAMS_PER_TROY_OZ, 2)
        out.append({"d": b["date"], "price": rmb_per_gram})

    for path in OUT_PATHS:
        path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"gold_price_cny: {len(out)} days (proxy: GC=F x USD/CNY) -> {OUT_PATHS[0]}")


if __name__ == "__main__":
    main()
