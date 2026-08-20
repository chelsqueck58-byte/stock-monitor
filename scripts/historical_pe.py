"""5-year annual P/E history for the AI/tech cohort (Compare/Revenue Mix scope).

Yahoo's free quoteSummary API only exposes 4 years of annual net income and no
historical diluted share count, so real historical EPS (not net-income-divided-
by-today's-share-count, which would be badly distorted for heavy-buyback names
like Apple) has to come from web research, same pattern as segment_margins /
capex_guidance in deep_fundamentals.py.

Currency is the sharp edge here: several names in this cohort report financials
in a different currency than the ticker actually trades in (e.g. Tencent/0700.HK
reports in RMB but trades in HKD). The research prompt requires EPS matching the
TRADED share's currency - the exact bug class caught earlier for market cap.

Price side is deterministic (no research): Yahoo's chart API for the close
nearest each fiscal year-end date.

Run:  .venv/bin/python scripts/historical_pe.py
"""
import datetime
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE = ROOT / "config" / "universe.json"
DATA = ROOT / "site" / "data.json"
OUT = ROOT / "data" / "historical-pe.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

COHORT_CATEGORIES = {"china-internet", "compute", "hyperscaler-cloud", "platform"}
EXTRA_TICKERS = {"ASML"}

# Sanity bound - a computed P/E outside this range is more likely a currency
# or unit bug than a real number. Flagged, not silently kept.
PE_SANITY_MIN, PE_SANITY_MAX = 2, 500
# A P/E under this floor is more likely an inflated/mis-scaled EPS than a
# stock genuinely priced at a few years of earnings - confirmed failure mode,
# see forward_pe.py (PDD's Yahoo-derived EPS was ~8-10x too high).


def ask_claude(prompt, timeout=280):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run(["claude", "-p", prompt, "--allowedTools", "WebSearch"],
                            capture_output=True, text=True, env=env, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except subprocess.TimeoutExpired:
        return ""


def parse_obj(text):
    if "```" in text:
        text = re.sub(r"```(json)?", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        return None
    try:
        return json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return None


def research_eps(tid, label, symbol, currency):
    prompt = (
        f"Find {label} ({symbol})'s GAAP diluted EPS (NOT adjusted/non-GAAP) for "
        f"its most recent 5 completed fiscal years, as reported per share in "
        f"{currency} - matching exactly the share class that trades under the "
        f"ticker {symbol} (which trades in {currency}). "
        "IMPORTANT: some companies report their financial statements in a "
        "different currency than the currency their shares actually trade in "
        f"(e.g. a company can report in RMB while its Hong Kong-listed shares "
        f"trade in HKD). If that's the case here, convert each fiscal year's "
        f"diluted EPS to {currency} using that year's actual historical exchange "
        f"rate (not today's rate) and set converted=true. If the company reports "
        f"directly in {currency}, set converted=false. Also watch for ADS/ADR "
        "ratios - if the primary financial reports state EPS per American "
        "Depositary Share but the ticker here is the ordinary/local share, "
        "adjust by the ADS ratio (state it in the note).\n"
        "If the company has been public for fewer than 5 years, return as many "
        "fiscal years as are actually available - do not fabricate earlier years.\n"
        "CRITICAL: reply with ONLY JSON, nothing else, in this exact shape:\n"
        '{"years":[{"fy_label":"FY2025","period_end":"YYYY-MM-DD",'
        '"eps_diluted":0.00,"converted":false,"note":""}, ...],'
        '"source":"..."}'
    )
    raw = ask_claude(prompt)
    parsed = parse_obj(raw)
    if not parsed and raw.strip():
        parsed = parse_obj(ask_claude(
            "Extract ONLY the JSON object from this text. Reply with ONLY the JSON.\n\n" + raw,
            timeout=120))
    return parsed


def yahoo_chart_close_near(symbol, date_str, session):
    """Nearest trading-day close on or before date_str, via Yahoo's chart API."""
    target = datetime.date.fromisoformat(date_str)
    period1 = int(datetime.datetime.combine(target - datetime.timedelta(days=10),
                                             datetime.time()).timestamp())
    period2 = int(datetime.datetime.combine(target + datetime.timedelta(days=3),
                                             datetime.time()).timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        r = session.get(url, params={"period1": period1, "period2": period2, "interval": "1d"},
                         timeout=15)
        r.raise_for_status()
        result = r.json()["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except Exception:
        return None
    best = None
    for ts, c in zip(timestamps, closes):
        if c is None:
            continue
        d = datetime.date.fromtimestamp(ts)
        if d <= target + datetime.timedelta(days=3):
            best = c
    return best


def process_one(item, session):
    tid, label, symbol, currency = item
    parsed = research_eps(tid, label, symbol, currency)
    years_raw = (parsed or {}).get("years") or []
    years = []
    for y in years_raw:
        period_end = y.get("period_end")
        eps = y.get("eps_diluted")
        if not period_end or not isinstance(eps, (int, float)):
            continue
        price = yahoo_chart_close_near(symbol, period_end, session)
        pe = None
        flag = None
        if price is not None and eps > 0:
            pe = round(price / eps, 1)
            if not (PE_SANITY_MIN < pe < PE_SANITY_MAX):
                flag = f"P/E {pe}x outside sanity range - check currency/units"
        elif eps <= 0:
            flag = "loss year"
        years.append({
            "fy_label": y.get("fy_label"),
            "period_end": period_end,
            "eps_diluted": eps,
            "converted": bool(y.get("converted")),
            "note": y.get("note") or "",
            "price": price,
            "pe": pe,
            "flag": flag,
        })
    # years/valid_pe are newest-first (as returned by the research prompt), so
    # the most recent 3 years are the first 3 elements, not the last 3.
    valid_pe = [y["pe"] for y in years if y["pe"] is not None]
    avg_3y = round(sum(valid_pe[:3]) / len(valid_pe[:3]), 1) if len(valid_pe) >= 1 else None
    avg_5y = round(sum(valid_pe) / len(valid_pe), 1) if valid_pe else None
    return tid, {
        "currency": currency,
        "years": years,
        "avg_3y": avg_3y,
        "avg_3y_count": min(3, len(valid_pe)),
        "avg_5y": avg_5y,
        "avg_5y_count": len(valid_pe),
        "source": (parsed or {}).get("source"),
        "fetched": datetime.date.today().isoformat(),
    }


def main():
    universe = json.loads(UNIVERSE.read_text())
    data = json.loads(DATA.read_text())
    currency_by_id = {i["id"]: i.get("currency", "USD") for i in data["instruments"]}
    scmap = json.loads((ROOT / "site" / "supply-chain-map.json").read_text())
    yahoo_by_id = {m["id"]: m.get("yahoo", m["id"]) for g in universe["groups"] for m in g["members"]}
    label_by_id = {m["id"]: m["label"] for g in universe["groups"] for m in g["members"]}

    todo = []
    for tid, m in scmap.items():
        if m.get("category") in COHORT_CATEGORIES or tid in EXTRA_TICKERS:
            todo.append((tid, label_by_id.get(tid, tid), yahoo_by_id.get(tid, tid),
                         currency_by_id.get(tid, "USD")))
    print(f"{len(todo)} tickers in cohort")

    out = {}
    if OUT.exists():
        try:
            out = json.loads(OUT.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    session = requests.Session()
    session.headers.update(HEADERS)

    flags = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(process_one, item, session) for item in todo]
        for fut in futures:
            tid, result = fut.result()
            out[tid] = result
            n_years = len(result["years"])
            n_flagged = sum(1 for y in result["years"] if y["flag"])
            print(f"  {tid:8} {n_years} years, avg3y={result['avg_3y']}, avg5y={result['avg_5y']}"
                  + (f"  [{n_flagged} FLAGGED]" if n_flagged else ""))
            for y in result["years"]:
                if y["flag"]:
                    flags.append(f"{tid} {y.get('fy_label')}: {y['flag']}")
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    print(f"done -> {OUT}")
    if flags:
        print("FLAGGED for manual review:")
        for f in flags:
            print("  ", f)


if __name__ == "__main__":
    main()
