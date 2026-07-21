"""IV rank + implied 1-month move + realized vol, free from IBKR (no subscription).
US-listed names only (options data). Writes data/iv.json; build.py merges it.
Needs IB Gateway up — degrades to leaving the last iv.json if unreachable.
"""
import json
import math
from pathlib import Path

from ib_insync import IB, Stock, util

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "universe.json"
OUT = ROOT / "data" / "iv.json"
NO_OPTIONS = {"KRX", "KSE", "SGX", "TWSE"}  # no options IV; US + HK (SEHK) work


def series(ib, contract, what, dur):
    try:
        b = ib.reqHistoricalData(contract, "", dur, "1 day", what, True, 1, timeout=25)
        return [x.close for x in b if x.close and x.close > 0]
    except Exception:
        return []


def main():
    config = json.loads(CONFIG.read_text())
    ib = IB()
    ib.RequestTimeout = 30
    try:
        util.run(ib.client.connectAsync("127.0.0.1", 4001, clientId=24, timeout=20))
    except Exception as exc:
        print(f"IB Gateway not reachable ({exc}); keeping existing iv.json")
        return

    out = {}
    for group in config["groups"]:
        for member in group["members"]:
            spec = member.get("ibkr", {})
            if spec.get("exchange") in NO_OPTIONS:
                continue
            contract = Stock(spec.get("symbol", member["id"]),
                             spec.get("exchange", "SMART"), spec.get("currency", "USD"))
            try:
                ib.qualifyContracts(contract)
            except Exception:
                continue
            iv = series(ib, contract, "OPTION_IMPLIED_VOLATILITY", "1 Y")
            hv = series(ib, contract, "HISTORICAL_VOLATILITY", "30 D")
            if not iv:
                continue
            cur, lo, hi = iv[-1], min(iv), max(iv)
            out[member["id"]] = {
                "iv": round(cur * 100, 1),
                "iv_rank": round((cur - lo) / (hi - lo) * 100) if hi > lo else 0,
                "realized": round(hv[-1] * 100, 1) if hv else None,
                "move_1m": round(cur * math.sqrt(21 / 252) * 100, 1),
            }
            ib.sleep(0.3)

    ib.disconnect()
    if out:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(out, separators=(",", ":")))
        print(f"iv: {len(out)} names -> {OUT}")
    else:
        print("no IV returned; keeping existing iv.json")


if __name__ == "__main__":
    main()
