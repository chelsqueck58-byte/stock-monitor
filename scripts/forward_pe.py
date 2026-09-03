"""Forward P/E for the AI/tech cohort - next 3 fiscal years beyond each
company's last reported one.

Two different reliability tiers, deliberately not treated the same:

- FY+1 and FY+2 (the two nearest forward years): pulled directly from
  Yahoo's free earningsTrend module, which is a real aggregated analyst
  consensus with an actual analyst count attached
  (query1.finance.yahoo.com quoteSummary, "0y"/"+1y" periods). No LLM
  involved - these are the most reliable numbers in the whole P/E pipeline.
  Yahoo's own "0y"/"+1y" periods are relative to ITS notion of the current
  fiscal year, which for a company deep into an unreported fiscal year is
  1-2 years past the last *reported* FY (T) - "0y" is the nearest, in
  effect FY+1 from T, and "+1y" is the one after that, FY+2 from T. The
  frontend re-derives the actual T+N offset from each estimate's own
  fy_label rather than trusting positional order, so this is safe even
  when Yahoo's "0y" is missing and only "+1y" comes back.
- FY+3 (three years out): Yahoo's free tier stops at +1y, so this needs
  web research. Consensus coverage genuinely thins out this far out,
  especially for smaller/newer names - the prompt requires a real
  multi-analyst consensus figure from a named aggregator (Zacks, TipRanks,
  Visible Alpha, MarketBeat, Simply Wall St, or a sell-side note cited in
  press) and returns nothing (not a guess) when no such figure exists.

Forward P/E = TODAY's price / that fiscal year's consensus EPS estimate -
the standard convention (never a projected future price).

Run:  .venv/bin/python scripts/forward_pe.py
"""
import argparse
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


def fetch_yahoo_fy_trend(session, crumb, symbol):
    """Nearest two forward fiscal years' consensus EPS, straight from Yahoo -
    no LLM. '0y' = Yahoo's current (in-progress/unreported) fiscal year,
    '+1y' = the one after that. Keyed by period code; either key may be
    missing if Yahoo doesn't have coverage."""
    url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
    try:
        r = session.get(url, params={"modules": "earningsTrend", "crumb": crumb}, timeout=15)
        r.raise_for_status()
        trend = r.json()["quoteSummary"]["result"][0].get("earningsTrend", {}).get("trend", [])
    except Exception:
        return {}
    out = {}
    for t in trend:
        period = t.get("period")
        if period not in ("0y", "+1y"):
            continue
        eps = (t.get("earningsEstimate", {}).get("avg") or {}).get("raw")
        n = (t.get("earningsEstimate", {}).get("numberOfAnalysts") or {}).get("raw")
        end_date = t.get("endDate")
        if eps is None:
            continue
        out[period] = {"period_end": end_date, "eps_estimate": eps, "num_analysts": n}
    return out


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


def research_fy3(tid, label, symbol, currency, latest_yahoo_end):
    prompt = (
        f"Find the analyst CONSENSUS EPS estimate for {label} ({symbol}) for "
        f"its fiscal year immediately after the one ending {latest_yahoo_end} "
        f"- i.e. the next fiscal year out beyond that. Per share in {currency} "
        f"(matching the currency {symbol} actually trades in on this "
        "exchange).\n"
        "HARD REQUIREMENTS:\n"
        "1. Must be a genuine aggregated CONSENSUS across multiple analysts - "
        "from a named source like Zacks Consensus Estimate, TipRanks analyst "
        "consensus, Visible Alpha, MarketBeat, Simply Wall St analyst "
        "consensus, or a sell-side research note that itself cites a "
        "consensus figure. A single analyst's individual estimate or price "
        "target does NOT count.\n"
        "2. Consensus coverage this far out is often thin or nonexistent - "
        "if you cannot find a genuine multi-analyst consensus for this "
        "year, DO NOT estimate or guess one. Omit it entirely.\n"
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
        'If it has no genuine consensus coverage: {"years":[]}'
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

    yahoo_trend = fetch_yahoo_fy_trend(session, crumb, symbol)
    for period in ("0y", "+1y"):
        fy = yahoo_trend.get(period)
        if not fy or not price:
            continue
        eps = fy["eps_estimate"]
        pe = round(price / eps, 1) if eps and eps > 0 else None
        flag = None
        if eps is not None and eps <= 0:
            flag = "consensus EPS estimate is negative/zero"
        elif pe is not None and not (PE_SANITY_MIN < pe < PE_SANITY_MAX):
            flag = f"P/E {pe}x outside sanity range - check currency/units"
        fy_label = f"FY{fy['period_end'][:4]}" if fy.get("period_end") else None
        years.append({
            "fy_label": fy_label,
            "period_end": fy["period_end"],
            "eps_estimate": eps,
            "num_analysts": fy["num_analysts"],
            "converted": False,
            "source": f"Yahoo Finance earningsTrend consensus ({period})",
            "pe": pe,
            "flag": flag,
        })

    latest_end = max((y["period_end"] for y in years if y.get("period_end")), default=None)
    ref_end = latest_end or "the most recently completed fiscal year"
    parsed = research_fy3(tid, label, symbol, currency, ref_end)
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", help="comma-separated ticker ids to (re)research; default all in scope")
    args = ap.parse_args()

    universe = json.loads(UNIVERSE.read_text())
    data = json.loads(DATA.read_text())
    currency_by_id = {i["id"]: i.get("currency", "USD") for i in data["instruments"]}
    price_by_id = {i["id"]: (i["bars"][-1]["c"] if i.get("bars") else None) for i in data["instruments"]}
    scmap = json.loads((ROOT / "site" / "supply-chain-map.json").read_text())
    yahoo_by_id = {m["id"]: m.get("yahoo", m["id"]) for g in universe["groups"] for m in g["members"]}
    label_by_id = {m["id"]: m["label"] for g in universe["groups"] for m in g["members"]}

    if args.tickers:
        want = {t.strip() for t in args.tickers.split(",")}
        scmap = {tid: m for tid, m in scmap.items() if tid in want}

    todo = []
    for tid, m in scmap.items():
        if args.tickers or m.get("category") in COHORT_CATEGORIES or tid in EXTRA_TICKERS:
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
