"""Segment-level margins + capex (trailing actual and forward guidance) from
each company's latest filing/earnings call - grounded via web search, blank
if no source. Scoped to china-internet, compute, and hyperscaler-cloud
categories per the site's fundamentals comparison tab.

Company-wide gross/net margins and OCF already come free from Yahoo's
financialData module (see fundamentals.py) - this script only covers what
that API doesn't have: per-segment margins (rarely a clean structured field
anywhere) and capex (missing entirely from Yahoo's free cashflow module as
of this pipeline - confirmed empty on a live check).

Not a daily stage - capex guidance and segment margins only move quarterly.
Writes data/deep-fundamentals.json:
{tid: {"segment_margins": [{"name":"...","margin_pct":0.0,"margin_type":"gross|operating"}],
       "trailing_capex": {"amount_usd_m":0.0,"period":"...","source":"..."},
       "capex_guidance": {"headline":"...","detail":"...","period":"...","source":"..."} or null,
       "fetched": "YYYY-MM-DD"}}

Run:  .venv/bin/python scripts/deep_fundamentals.py [--tickers TID,TID,...]
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
SCM = ROOT / "data" / "supply-chain-map.json"
UNIVERSE = ROOT / "config" / "universe.json"
OUT = ROOT / "data" / "deep-fundamentals.json"
WORKERS = 6
CATEGORIES = {"china-internet", "compute", "hyperscaler-cloud", "platform"}


def ask_claude(prompt):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run(["claude", "-p", prompt, "--allowedTools", "WebSearch"],
                            capture_output=True, text=True, env=env, timeout=300)
        return r.stdout.strip() if r.returncode == 0 else ""
    except subprocess.TimeoutExpired:
        # A single slow ticker (e.g. Chinese-language filings needing more
        # search rounds) must not crash the whole batch - confirmed live on
        # Cambricon, which took down all 22 results before this fix even
        # though 21 had already succeeded and saved.
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
    tid, label = item
    prompt = (
        f"Research {label} ({tid})'s most recent filing and latest earnings call "
        "for two things:\n\n"
        "1) SEGMENT MARGINS: for each business segment they report, find its "
        "gross margin or operating margin (whichever the company actually "
        "discloses - state which type). Many companies do NOT break this out "
        "by segment - if so, return an empty segment_margins list, don't guess. "
        "margin_pct MUST be a percentage number like 26.4 (meaning 26.4%), NOT "
        "a decimal fraction like 0.264 - confirmed failure mode on Intel's Q2 "
        "FY2026 filing, where margins came back as 0.264/0.395 instead of "
        "26.4/39.5.\n\n"
        "2) CAPEX: (a) their most recently reported ACTUAL capital expenditure "
        "(dollar figure and the period it covers), and (b) their most recent "
        "forward CAPEX GUIDANCE - what management said on the latest earnings "
        "call or investor presentation about planned capex (a number, range, "
        "or % of revenue - whatever they actually stated, in their own terms), "
        "and whether that guidance was RAISED or CUT versus what they said "
        "last quarter (state which, if you can tell).\n\n"
        "capex_guidance ONLY exists if a concrete figure/range/% was actually "
        "given - if management only said something vague like 'substantial "
        "increase' with no number ANYWHERE (not this quarter, not a prior "
        "multi-year plan being referenced), return capex_guidance as null "
        "entirely. Do NOT return an object whose text just says guidance "
        "wasn't given - null means null.\n"
        "When capex_guidance exists, it has TWO fields:\n"
        "  - headline: ONE short phrase, <= 12 words, just the number/range - "
        "e.g. \"RMB380bn 3-year AI/cloud plan, being raised\" or \"$4.5B "
        "FY2026 capex\" or \"~35% of revenue, up from 30%\". Only append a "
        "trend word (raised/cut) when guidance genuinely CHANGED this quarter "
        "- if it's just unchanged/reiterated, state the figure plainly with "
        "NO trend word at all (do not say \"reaffirmed\" - that's not useful "
        "information, it's noise). This is what renders by default in a "
        "narrow UI cell - it must stand alone with no other context.\n"
        "  - detail: 2-3 sentences, <= 350 characters, the fuller context "
        "(what period, what qualifier matters most) - shown on hover only, "
        "so it can be a bit richer than the headline but still concise.\n\n"
        "RULES: only report numbers you find real grounded evidence for. Never "
        "invent or estimate.\n"
        "CRITICAL: reply with ONLY the JSON object, nothing else.\n"
        'Format: {"segment_margins":[{"name":"...","margin_pct":0.0,'
        '"margin_type":"gross or operating"}],'
        '"trailing_capex":{"amount_usd_m":0.0,"period":"...","source":"..."},'
        '"capex_guidance":{"headline":"...","detail":"...","period":"...",'
        '"source":"..."}}\n'
        'Use null for amount_usd_m, trailing_capex, or capex_guidance if '
        "genuinely not found - null the whole object, never a placeholder "
        "explaining the absence."
    )
    raw = ask_claude(prompt)
    parsed = parse_obj(raw)
    if not parsed and raw.strip():
        fixup = (
            "Extract ONLY the JSON object from this text, matching the schema "
            "described. Reply with ONLY the JSON object.\n\n" + raw
        )
        parsed = parse_obj(ask_claude(fixup))
    return tid, label, parsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", help="comma-separated ticker ids; default all in scope")
    args = ap.parse_args()

    scm = json.loads(SCM.read_text())
    universe = json.loads(UNIVERSE.read_text())
    labels = {m["id"]: m["label"] for g in universe["groups"] for m in g["members"]}

    if args.tickers:
        todo_ids = [t.strip() for t in args.tickers.split(",")]
    else:
        todo_ids = [tid for tid, info in scm.items() if info.get("category") in CATEGORIES]

    todo = [(tid, labels.get(tid, scm.get(tid, {}).get("name", tid))) for tid in todo_ids]
    print(f"{len(todo)} tickers to research")

    out = json.loads(OUT.read_text()) if OUT.exists() else {}
    today = datetime.date.today().isoformat()

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for tid, label, parsed in ex.map(research_one, todo):
            if parsed:
                out[tid] = {
                    "segment_margins": parsed.get("segment_margins") or [],
                    "trailing_capex": parsed.get("trailing_capex"),
                    "capex_guidance": parsed.get("capex_guidance"),
                    "fetched": today,
                }
                sm = out[tid]["segment_margins"]
                tc = out[tid]["trailing_capex"]
                cg = out[tid]["capex_guidance"]
                print(f"  {tid:8} segments={len(sm)} "
                      f"trailing_capex={tc.get('amount_usd_m') if tc else None} "
                      f"guidance={cg.get('headline') if cg else 'no'}")
            else:
                out[tid] = {"segment_margins": [], "trailing_capex": None,
                            "capex_guidance": None, "fetched": today}
                print(f"  {tid:8} no data found")
            OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    print(f"deep_fundamentals: {len(out)} tickers -> {OUT}")


if __name__ == "__main__":
    main()
