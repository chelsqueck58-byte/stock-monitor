"""Current FX rates (local currency units per 1 USD) for every non-USD
currency the universe trades in. Needed because Yahoo's market_cap field
comes back in each stock's LOCAL trading currency, not USD - confirmed live
on Cambricon (686.7B raw value = CNY, not USD; its real USD-equivalent cap
is ~$102B) and Tencent (3.97T raw = HKD, not USD). Without this, market cap
was being silently mislabeled with a '$' sign on every non-US stock.

Run:  .venv/bin/python scripts/fx_rates.py
"""
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "fx-rates.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
PAIRS = {"HKD": "HKD=X", "CNY": "CNY=X", "KRW": "KRW=X", "SGD": "SGD=X"}


def main():
    s = requests.Session()
    s.headers.update(HEADERS)
    rates = {"USD": 1.0}
    for currency, yahoo_symbol in PAIRS.items():
        r = s.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}",
                   params={"range": "1d", "interval": "1d"}, timeout=15)
        r.raise_for_status()
        price = r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
        rates[currency] = price
        print(f"  {currency}: {price} per USD")
    OUT.write_text(json.dumps(rates, separators=(",", ":")))
    print(f"fx_rates: {len(rates)} currencies -> {OUT}")


if __name__ == "__main__":
    main()
