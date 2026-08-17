"""Trailing ROIC for the AI/tech cohort (Compare tab scope).

Deliberately NOT computed from Yahoo's own fields. ROIC has several
competing formulas (NOPAT/invested capital, with invested capital itself
defined at least three different ways - debt+equity-cash, total assets-
current liabilities-cash, average vs point-in-time), and Yahoo's free
balanceSheetHistory module is empty except endDate (confirmed live), so any
homegrown version here would need an assumed tax rate and an approximated
book equity figure - a real risk of quietly producing a number nobody else
would recognize.

"The ROIC the market is looking at" means the published, commonly-cited
figure - sourced from a real data provider (GuruFocus, StockAnalysis.com,
WSJ, Morningstar, Wisesheets, or a sell-side note that itself cites one) via
the same research pattern as historical_pe.py / forward_pe.py, not derived
from scratch here.

Run:  .venv/bin/python scripts/roic.py
"""
import datetime
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE = ROOT / "config" / "universe.json"
OUT = ROOT / "data" / "roic.json"

COHORT_CATEGORIES = {"china-internet", "compute", "hyperscaler-cloud", "platform"}
EXTRA_TICKERS = {"ASML"}
ROIC_SANITY_MIN, ROIC_SANITY_MAX = -100, 200


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


def research_one(item):
    tid, label, symbol = item
    prompt = (
        f"Find {label} ({symbol})'s current TRAILING TWELVE MONTH (TTM) ROIC "
        "(Return on Invested Capital), as published by a real financial data "
        "provider - GuruFocus, StockAnalysis.com, WSJ, Morningstar, "
        "Wisesheets, Simply Wall St, or a sell-side research note that "
        "itself cites a computed ROIC. This should be the commonly-cited "
        "published figure, NOT something you calculate yourself from raw "
        "financials.\n"
        "RULES:\n"
        "1. Must be a real number from a named source - if multiple sources "
        "disagree, prefer GuruFocus or StockAnalysis.com and note the others "
        "if meaningfully different.\n"
        "2. Double-check you are not confusing ROIC with ROE (Return on "
        "Equity) or ROA (Return on Assets) - these are different metrics and "
        "sites sometimes list them near each other. If a figure looks like "
        "it might actually be ROE/ROA (e.g. unusually high, or from a page "
        "section labeled differently), verify it's specifically labeled ROIC "
        "before using it.\n"
        "3. If no named provider publishes a ROIC figure for this company "
        "(rare, but possible for very recently public names), do not "
        "calculate one yourself - return null.\n"
        "4. Note the period (e.g. 'TTM', 'FY2025', 'MRQ').\n"
        "CRITICAL: reply with ONLY JSON, nothing else:\n"
        '{"roic_pct": 0.0, "period": "...", "source": "..."}\n'
        'If not found: {"roic_pct": null, "period": null, "source": null}'
    )
    raw = ask_claude(prompt)
    parsed = parse_obj(raw)
    if not parsed and raw.strip():
        parsed = parse_obj(ask_claude(
            "Extract ONLY the JSON object from this text. Reply with ONLY the JSON.\n\n" + raw,
            timeout=120))
    return tid, parsed


def main():
    universe = json.loads(UNIVERSE.read_text())
    scmap = json.loads((ROOT / "site" / "supply-chain-map.json").read_text())
    yahoo_by_id = {m["id"]: m.get("yahoo", m["id"]) for g in universe["groups"] for m in g["members"]}
    label_by_id = {m["id"]: m["label"] for g in universe["groups"] for m in g["members"]}

    todo = []
    for tid, m in scmap.items():
        if m.get("category") in COHORT_CATEGORIES or tid in EXTRA_TICKERS:
            todo.append((tid, label_by_id.get(tid, tid), yahoo_by_id.get(tid, tid)))
    print(f"{len(todo)} tickers in cohort")

    out = {}
    if OUT.exists():
        try:
            out = json.loads(OUT.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    flags = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for tid, parsed in ex.map(research_one, todo):
            roic = (parsed or {}).get("roic_pct")
            flag = None
            if isinstance(roic, (int, float)) and not (ROIC_SANITY_MIN < roic < ROIC_SANITY_MAX):
                flag = f"ROIC {roic}% outside sanity range - check source"
            out[tid] = {
                "roic_pct": roic if isinstance(roic, (int, float)) else None,
                "period": (parsed or {}).get("period"),
                "source": (parsed or {}).get("source"),
                "flag": flag,
                "fetched": datetime.date.today().isoformat(),
            }
            print(f"  {tid:8} roic={out[tid]['roic_pct']}  period={out[tid]['period']}"
                  + (f"  [FLAGGED: {flag}]" if flag else ""))
            if flag:
                flags.append(f"{tid}: {flag}")
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    found = sum(1 for v in out.values() if v.get("roic_pct") is not None)
    print(f"roic: {found}/{len(out)} with a found figure -> {OUT}")
    if flags:
        print("FLAGGED for manual review:")
        for f in flags:
            print("  ", f)


if __name__ == "__main__":
    main()
