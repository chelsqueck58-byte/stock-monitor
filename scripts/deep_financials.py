"""Deep-dive financials for a small, hand-curated watchlist (Meta, NVIDIA,
Alibaba, Laopu Gold) - company-wide headline metrics plus a per-segment view
that shows revenue AND whichever specific metric investors actually watch
for that segment (e.g. Alibaba Cloud's adjusted EBITDA margin, Meta's
Reality Labs operating loss, NVIDIA's gross margin) rather than a uniform
metric across every segment - segments don't all disclose the same things,
and investors don't watch them the same way.

Two time granularities, both from actual filings, never estimated:
- fiscal_years: the last 2 reported fiscal years.
- quarters: the last 8 reported fiscal quarters (trailing 2 years).

Company-wide, per period: revenue, gross margin %, net profit margin %,
diluted EPS, operating cash flow, capex, free cash flow, EBITDA.

Per segment, per period: revenue, plus ONE curated "headline metric" (name
+ value) - whatever that segment's own disclosure and investor commentary
actually centers on. Not every segment has one every period.

Writes data/deep-financials.json:
{tid: {"currency": "...", "fiscal_year_end": "e.g. Dec 31 or Mar 31",
       "fiscal_years": [{"fy_label":"...", "period_end":"...",
                  "revenue_m":0.0, "gross_margin_pct":0.0,
                  "net_margin_pct":0.0, "eps_diluted":0.0, "ocf_m":0.0,
                  "capex_m":0.0, "fcf_m":0.0, "ebitda_m":0.0,
                  "ebitda_basis":"...", "source":"..."}],
       "quarters": [{"quarter_label":"e.g. Q2 FY2026", "period_end":"...",
                  ... same metric fields ...}],
       "segments": [{"name":"...",
                     "fiscal_years": [{"fy_label":"...", "revenue_m":0.0,
                        "headline_metric_name":"...",
                        "headline_metric_value":"..."}],
                     "quarters": [{"quarter_label":"...", "revenue_m":0.0,
                        "headline_metric_name":"...",
                        "headline_metric_value":"..."}]}],
       "fetched": "YYYY-MM-DD"}}

Run:  .venv/bin/python scripts/deep_financials.py [--tickers TID,TID,...]
"""
import argparse
import datetime
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE = ROOT / "config" / "universe.json"
OUT = ROOT / "data" / "deep-financials.json"
WORKERS = 2  # each ticker also spawns 2 inner sub-calls (company-wide + segments
             # run in parallel) - keeping this low avoids resource contention
             # that pushed Alibaba's already-slow research (~9-10 min per
             # sub-call) past the timeout when 4 tickers ran at once.
DEFAULT_TICKERS = ["META", "NVDA", "9988", "6181"]


def ask_claude(prompt, timeout=900):
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


def ask_and_parse(prompt):
    raw = ask_claude(prompt)
    parsed = parse_obj(raw)
    if not parsed and raw.strip():
        fixup = (
            "Extract ONLY the JSON object from this text, matching the schema "
            "described. Reply with ONLY the JSON object.\n\n" + raw
        )
        parsed = parse_obj(ask_claude(fixup))
    return parsed


def research_company_wide(tid, label):
    prompt = (
        f"Research {label} ({tid})'s COMPANY-WIDE financial history at TWO "
        "granularities, from actual 10-K/10-Q/20-F/6-K/annual-report/"
        "earnings-release filings - never estimate or invent a number:\n"
        "  (A) the last 2 reported FISCAL YEARS (annual figures)\n"
        "  (B) the last 8 reported FISCAL QUARTERS (trailing ~2 years)\n\n"
        "For each period in both (A) and (B): revenue, gross margin %, net "
        "profit margin %, diluted EPS, operating cash flow, capital "
        "expenditure, free cash flow (OCF minus capex, or as the company "
        "itself defines it if disclosed directly), and EBITDA (use the "
        "company's own disclosed adjusted EBITDA if they report one, "
        "otherwise operating income + D&A, and say which basis you used). "
        "All dollar figures in millions of the company's own reporting "
        "currency.\n\n"
        "RULES: only real, filed numbers. If a metric wasn't disclosed for "
        "a given period, leave it null rather than estimating or "
        "backfilling.\n"
        "CRITICAL: reply with ONLY the JSON object, nothing else.\n"
        'Format: {"currency":"USD","fiscal_year_end":"e.g. Dec 31",'
        '"fiscal_years":[{"fy_label":"FY2025","period_end":"2025-12-31",'
        '"revenue_m":0.0,"gross_margin_pct":0.0,"net_margin_pct":0.0,'
        '"eps_diluted":0.0,"ocf_m":0.0,"capex_m":0.0,"fcf_m":0.0,'
        '"ebitda_m":0.0,"ebitda_basis":"...","source":"..."}],'
        '"quarters":[{"quarter_label":"Q2 FY2026","period_end":"2026-06-30",'
        '"revenue_m":0.0,"gross_margin_pct":0.0,"net_margin_pct":0.0,'
        '"eps_diluted":0.0,"ocf_m":0.0,"capex_m":0.0,"fcf_m":0.0,'
        '"ebitda_m":0.0,"ebitda_basis":"...","source":"..."}]}'
    )
    return ask_and_parse(prompt)


def research_segments(tid, label):
    prompt = (
        f"Research {label} ({tid})'s PER-SEGMENT financial history at TWO "
        "granularities, from actual 10-K/10-Q/20-F/6-K/annual-report/"
        "earnings-release filings - never estimate or invent a number:\n"
        "  (A) the last 2 reported FISCAL YEARS (annual figures)\n"
        "  (B) the last 8 reported FISCAL QUARTERS (trailing ~2 years)\n\n"
        "Use this company's CURRENT (most recent quarter's) reportable "
        "segment structure as the canonical set of segment names, and "
        "report EVERY period's data under those current names - do not "
        "create a separate segment entry for an old/renamed/merged "
        "predecessor (e.g. if a segment was renamed or folded into another "
        "during the window, map its historical data onto whichever current "
        "segment it continues into, and just note the rename/merge in that "
        "segment's own history rather than listing it twice). If the "
        "company reports segments along more than one dimension (e.g. by "
        "business line AND separately by geography/channel), pick ONLY the "
        "ONE dimension used in its PRIMARY segment footnote/note in the "
        "financial statements (the one used for segment operating income) "
        "- do not also report the secondary cut as more 'segments'. This "
        "should produce a SMALL, stable list (typically 2-6 segments), not "
        "a sprawling one.\n\n"
        "For each (canonical) segment, give revenue AND pick ONE 'headline "
        "metric' - whatever specific number that segment's own disclosures "
        "and analyst/investor commentary actually centers on. This varies "
        "by segment - do not force the same metric on every segment. "
        "Concrete examples of a real headline metric - MAKE SURE these "
        "specific ones are covered if the company has the relevant "
        "segment: Alibaba's cloud segment (whatever it's currently named) "
        "is watched for its own disclosed Adjusted EBITDA margin; within "
        "Alibaba's e-commerce segment specifically call out the quick-"
        "commerce/instant-retail business (Taobao Instant Commerce / "
        "Ele.me)'s own operating LOSS in dollars as an ADDITIONAL note on "
        "that segment (not as its own segment row), given the ongoing "
        "China food-delivery/instant-retail spending war; Meta's Reality "
        "Labs segment is watched for its operating LOSS in dollars (it's "
        "structurally unprofitable, so a margin isn't the useful number); "
        "Meta's Family of Apps segment is watched for its operating "
        "margin; NVIDIA's segments/end-markets are mainly watched on "
        "revenue growth (esp. Data Center) with company-wide gross margin "
        "as the real profitability signal since NVIDIA doesn't disclose "
        "segment gross margin. Use your judgment for any other segment/"
        "company - only report a headline metric you can find a real "
        "disclosed or company-stated basis for. If a segment doesn't have "
        "one clean standout metric beyond revenue, omit "
        "headline_metric_name/value for it (null), don't force one.\n\n"
        "RULES: only real, filed numbers. If a metric wasn't disclosed for "
        "a given period (e.g. a company started reporting adjusted EBITDA "
        "only recently, or a segment didn't exist yet), leave it null "
        "rather than estimating or backfilling.\n"
        "CRITICAL: reply with ONLY the JSON object, nothing else.\n"
        'Format: {"segments":[{"name":"...",'
        '"fiscal_years":[{"fy_label":"FY2025","revenue_m":0.0,'
        '"headline_metric_name":"e.g. Adjusted EBITDA margin",'
        '"headline_metric_value":"e.g. 34%","note":"optional short extra '
        'callout, e.g. quick-commerce sub-line operating loss - null if '
        'none","source":"..."}],'
        '"quarters":[{"quarter_label":"Q2 FY2026","revenue_m":0.0,'
        '"headline_metric_name":"...","headline_metric_value":"...",'
        '"note":null,"source":"..."}]}]}'
    )
    return ask_and_parse(prompt)


def research_one(item):
    tid, label = item
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_cw = ex.submit(research_company_wide, tid, label)
        fut_seg = ex.submit(research_segments, tid, label)
        company_wide = fut_cw.result()
        segments = fut_seg.result()
    if not company_wide and not segments:
        return tid, label, None
    merged = dict(company_wide or {})
    merged["segments"] = (segments or {}).get("segments") or []
    return tid, label, merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", help="comma-separated ticker ids; default the curated watchlist")
    args = ap.parse_args()

    universe = json.loads(UNIVERSE.read_text())
    labels = {m["id"]: m["label"] for g in universe["groups"] for m in g["members"]}

    todo_ids = [t.strip() for t in args.tickers.split(",")] if args.tickers else DEFAULT_TICKERS
    todo = [(tid, labels.get(tid, tid)) for tid in todo_ids]
    print(f"{len(todo)} tickers to research")

    out = json.loads(OUT.read_text()) if OUT.exists() else {}
    today = datetime.date.today().isoformat()

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for tid, label, parsed in ex.map(research_one, todo):
            if parsed and (parsed.get("fiscal_years") or parsed.get("quarters")):
                out[tid] = {
                    "currency": parsed.get("currency"),
                    "fiscal_year_end": parsed.get("fiscal_year_end"),
                    "fiscal_years": parsed.get("fiscal_years") or [],
                    "quarters": parsed.get("quarters") or [],
                    "segments": parsed.get("segments") or [],
                    "fetched": today,
                }
                n_fy = len(out[tid]["fiscal_years"])
                n_q = len(out[tid]["quarters"])
                n_segs = len(out[tid]["segments"])
                print(f"  {tid:8} {n_fy} fiscal years, {n_q} quarters, {n_segs} segments")
            else:
                if tid not in out:
                    out[tid] = {"currency": None, "fiscal_year_end": None,
                                "fiscal_years": [], "quarters": [], "segments": [],
                                "fetched": today}
                print(f"  {tid:8} no data found - keeping any previous entry")
            OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    print(f"deep_financials: {len(out)} tickers -> {OUT}")


if __name__ == "__main__":
    main()
