"""Daily repricing of forward P/E using today's live price - no research, no
API calls, just price / cached_eps_estimate. The EPS estimate itself is only
worth re-researching occasionally (analyst consensus doesn't move daily and
T+2/T+3 require expensive LLM+WebSearch), but forward_pe.py isn't part of the
daily pipeline, so price_used and the resulting P/E were going stale as the
stock price moved while the EPS estimate sat still. Run this daily instead -
cheap, deterministic, keeps "forward P/E" honest to TODAY's price.

Run: .venv/bin/python scripts/reprice_forward_pe.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "site" / "data.json"
FWD_PE_PATHS = [ROOT / "data" / "forward-pe.json", ROOT / "site" / "forward-pe.json", ROOT / "forward-pe.json"]

PE_SANITY_MIN, PE_SANITY_MAX = 2, 750


def main():
    data = json.loads(DATA.read_text())
    price_by_id = {i["id"]: i.get("last_close") for i in data["instruments"]}

    fwd = json.loads(FWD_PE_PATHS[0].read_text())
    repriced = 0
    for tid, entry in fwd.items():
        price = price_by_id.get(tid)
        if not price:
            continue
        entry["price_used"] = price
        for y in entry.get("years", []):
            eps = y.get("eps_estimate")
            if not isinstance(eps, (int, float)) or eps <= 0:
                continue
            pe = round(price / eps, 1)
            y["pe"] = pe
            if not (PE_SANITY_MIN < pe < PE_SANITY_MAX):
                y["flag"] = f"P/E {pe}x outside sanity range - check currency/units"
            elif (y.get("flag") or "").startswith("P/E") and "outside sanity range" in (y.get("flag") or ""):
                y["flag"] = None
            repriced += 1

    for path in FWD_PE_PATHS:
        path.write_text(json.dumps(fwd, ensure_ascii=False, separators=(",", ":")))

    print(f"reprice_forward_pe: {repriced} year-entries repriced across {len(fwd)} tickers -> {FWD_PE_PATHS[0]}")


if __name__ == "__main__":
    main()
