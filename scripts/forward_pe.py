"""Forward P/E for the AI/tech cohort - next 3 fiscal years beyond each
company's last reported one.

Two different reliability tiers, deliberately not treated the same:

- FY+1 (the nearest forward year): pulled directly from Yahoo's free
  earningsTrend module, which is a real aggregated analyst consensus with an
  actual analyst count attached (query1.finance.yahoo.com quoteSummary,
  "+1y" period). No LLM involved - this is the most reliable number in the
  whole P/E pipeline.
- FY+2 / FY+3 (two and three years out): Yahoo's free tier stops at +1y, so
  these need web research. Consensus coverage genuinely thins out this far
  out, especially for smaller/newer names - the prompt requires a real
  multi-analyst consensus figure from a named aggregator (Zacks, TipRanks,
  Visible Alpha, MarketBeat, Simply Wall St, or a sell-side note cited in
  press) and returns nothing (not a guess) when no such figure exists.

Forward P/E = TODAY's price / that fiscal year's consensus EPS estimate -
the standard convention (never a projected future price).

Run:  .venv/bin/python scripts/forward_pe.py
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
OUT = ROOT / "data" / "forward-pe.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

COHORT_CATEGORIES = {"china-internet", "compute", "hyperscaler-cloud", "platform"}
EXTRA_TICKERS = {"ASML"}
PE_SANITY_MIN, PE_SANITY_MAX = 2, 750  # forward multiples run hotter than trailing
# A P/E under this floor is more likely an inflated/mis-scaled EPS (a real,
# confirmed failure mode: Yahoo's own earningsTrend gave PDD an EPS estimate
# of $82.59 vs its real ~$3/share earning power, implying pe=1.0x) than a
# stock genuinely priced at a few years of earnings.


def session_with_crumb():
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get("https://fc.yahoo.com", timeout=10)
    except requests.RequestException:
        pass
    crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10).text
    return s, crumb


def fetch_yahoo_fy1(session, crumb, symbol):
    """Nearest forward fiscal year consensus EPS, straight from Yahoo - no LLM."""
    url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
    try:
        r = session.get(url, params={"modules": "earningsTrend", "crumb": crumb}, timeout=15)
        r.raise_for_status()
        trend = r.json()["quoteSummary"]["result"][0].get("earningsTrend", {}).get("trend", [])
    except Exception:
        return None
    for t in trend:
        if t.get("period") == "+1y":
            eps = (t.get("earningsEstimate", {}).get("avg") or {}).get("raw")
            n = (t.get("earningsEstimate", {}).get("numberOfAnalysts") or {}).get("raw")
            end_date = t.get("endDate")
            if eps is None:
                return None
            return {"period_end": end_date, "eps_estimate": eps, "num_analysts": n}
    return None


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


def research_fy2_fy3(tid, label, symbol, currency, fy1_end):
    prompt = (
        f"Find analyst CONSENSUS EPS estimates for {label} ({symbol}) for its "
        f"TWO fiscal years after the one ending {fy1_end} - i.e. the second and "
        f"third fiscal years out from today. Per share in {currency} (matching "
        f"the currency {symbol} actually trades in on this exchange).\n"
        "HARD REQUIREMENTS:\n"
        "1. Must be a genuine aggregated CONSENSUS across multiple analysts - "
        "from a named source like Zacks Consensus Estimate, TipRanks analyst "
        "consensus, Visible Alpha, MarketBeat, Simply Wall St analyst "
        "consensus, or a sell-side research note that itself cites a "
        "consensus figure. A single analyst's individual estimate or price "
        "target does NOT count.\n"
        "2. Consensus coverage this far out is often thin or nonexistent - "
        "if you cannot find a genuine multi-analyst consensus for a given "
        "year, DO NOT estimate or guess one. Omit that year entirely.\n"
        "2b. Many consensus-aggregator sites (Fintel, TipRanks, MarketScreener, "
        "MarketBeat, Alpha Spread) block automated/non-browser fetches with a "
        "Cloudflare challenge or 403 page. If a fetch tool returns a challenge "
        "page, an error, or content that doesn't look like the real site (e.g. "
        "a 'just a moment' interstitial, broken template strings, suspiciously "
        "round or oddly-precise numbers you can't trace to visible page text), "
        "DO NOT report numbers from it - treat that source as unavailable and "
        "either find a different source or omit the year, per rule 2.\n"
        "3. If the company reports in a different currency than it trades in, "
        "convert using a current spot rate and say so.\n"
        "CRITICAL: reply with ONLY JSON, nothing else:\n"
        '{"years":[{"fy_label":"FY2028","period_end":"YYYY-MM-DD",'
        '"eps_estimate":0.00,"num_analysts":0,"converted":false,"source":"..."}]}\n'
        'If neither year has genuine consensus coverage: {"years":[]}'
    )
    raw = ask_claude(prompt)
    parsed = parse_obj(raw)
    if not parsed and raw.strip():
        parsed = parse_obj(ask_claude(
            "Extract ONLY the JSON object from this text. Reply with ONLY the JSON.\n\n" + raw,
            timeout=120))
    return parsed


def process_one(item, session, crumb, price_by_id):
    tid, label, symbol, currency = item
    price = price_by_id.get(tid)
    years = []

    fy1 = fetch_yahoo_fy1(session, crumb, symbol)
    if fy1 and price:
        eps = fy1["eps_estimate"]
        pe = round(price / eps, 1) if eps and eps > 0 else None
        flag = None
        if eps is not None and eps <= 0:
            flag = "consensus EPS estimate is negative/zero"
        elif pe is not None and not (PE_SANITY_MIN < pe < PE_SANITY_MAX):
            flag = f"P/E {pe}x outside sanity range - check currency/units"
        fy1_label = f"FY{fy1['period_end'][:4]}" if fy1.get("period_end") else None
        years.append({
            "fy_label": fy1_label,
            "period_end": fy1["period_end"],
            "eps_estimate": eps,
            "num_analysts": fy1["num_analysts"],
            "converted": False,
            "source": "Yahoo Finance earningsTrend consensus (+1y)",
            "pe": pe,
            "flag": flag,
        })

    fy1_end = fy1["period_end"] if fy1 else "the most recently completed fiscal year"
    parsed = research_fy2_fy3(tid, label, symbol, currency, fy1_end)
    for y in (parsed or {}).get("years") or []:
        eps = y.get("eps_estimate")
        if not isinstance(eps, (int, float)):
            continue
        pe = round(price / eps, 1) if price and eps > 0 else None
        flag = None
        if eps <= 0:
            flag = "consensus EPS estimate is negative/zero"
        elif pe is not None and not (PE_SANITY_MIN < pe < PE_SANITY_MAX):
            flag = f"P/E {pe}x outside sanity range - check currency/units"
        years.append({
            "fy_label": y.get("fy_label"),
            "period_end": y.get("period_end"),
            "eps_estimate": eps,
            "num_analysts": y.get("num_analysts"),
            "converted": bool(y.get("converted")),
            "source": y.get("source"),
            "pe": pe,
            "flag": flag,
        })

    years.sort(key=lambda y: y.get("period_end") or "")
    return tid, {
        "currency": currency,
        "price_used": price,
        "years": years,
        "fetched": datetime.date.today().isoformat(),
    }


def main():
    universe = json.loads(UNIVERSE.read_text())
    data = json.loads(DATA.read_text())
    currency_by_id = {i["id"]: i.get("currency", "USD") for i in data["instruments"]}
    price_by_id = {i["id"]: (i["bars"][-1]["c"] if i.get("bars") else None) for i in data["instruments"]}
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

    session, crumb = session_with_crumb()

    flags = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(process_one, item, session, crumb, price_by_id) for item in todo]
        for fut in futures:
            tid, result = fut.result()
            out[tid] = result
            n_years = len(result["years"])
            n_flagged = sum(1 for y in result["years"] if y["flag"])
            print(f"  {tid:8} {n_years} yrs found, price={result['price_used']}"
                  + (f"  [{n_flagged} FLAGGED]" if n_flagged else ""))
            for y in result["years"]:
                if y["flag"]:
                    flags.append(f"{tid} {y.get('fy_label') or y.get('period_end')}: {y['flag']}")
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    print(f"done -> {OUT}")
    if flags:
        print("FLAGGED for manual review:")
        for f in flags:
            print("  ", f)


if __name__ == "__main__":
    main()
