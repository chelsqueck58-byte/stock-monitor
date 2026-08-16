"""Geographic/country revenue mix (% of revenue by country or region) from
each company's latest filing - grounded via web search, blank if not
disclosed. Scoped to china-internet, compute, and hyperscaler-cloud
categories, same as revenue_breakdown.py / deep_fundamentals.py.

Not every company discloses this - large multinationals (Apple, Amazon,
Google) do; single-market names (many China-domestic internet cos) may not,
since their revenue is ~100% one country and not worth a separate breakdown.
Writes data/geo-mix.json:
{tid: {"fiscal_period": "...", "regions": [{"name":"...","pct":0.0}],
       "source": "...", "fetched": "YYYY-MM-DD"}}

Run:  .venv/bin/python scripts/geo_mix.py [--tickers TID,TID,...]
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
OUT = ROOT / "data" / "geo-mix.json"
WORKERS = 6
CATEGORIES = {"china-internet", "compute", "hyperscaler-cloud", "platform"}


def ask_claude(prompt):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run(["claude", "-p", prompt, "--allowedTools", "WebSearch"],
                            capture_output=True, text=True, env=env, timeout=280)
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
    tid, label = item
    prompt = (
        f"Find {label} ({tid})'s revenue breakdown by COUNTRY or GEOGRAPHIC "
        "REGION from its most recent quarterly or annual filing. Percentages "
        "as a share of total revenue.\n"
        "RULES: use the company's own reported region names (e.g. 'Americas', "
        "'Greater China', 'EMEA', 'North America', 'China', 'International' - "
        "whatever they actually call them). Percentages should sum to "
        "approximately 100. Only include what you find real numbers for. If "
        "the company doesn't disclose a country/region breakdown at all "
        "(common for single-market domestic companies), or their revenue is "
        "effectively ~100% one country and they don't bother breaking it "
        "out, say so explicitly rather than inventing a split.\n"
        "CRITICAL: reply with ONLY the JSON object, nothing else.\n"
        'Format: {"fiscal_period":"...","regions":[{"name":"...","pct":0.0}],'
        '"source":"..."}\n'
        'If no breakdown exists: {"fiscal_period":null,"regions":[],"source":null}'
    )
    raw = ask_claude(prompt)
    parsed = parse_obj(raw)
    if not parsed and raw.strip():
        fixup = (
            "Extract ONLY the JSON object from this text, matching "
            '{"fiscal_period":"...","regions":[{"name":"...","pct":0.0}],'
            '"source":"..."}. Reply with ONLY the JSON object.\n\n' + raw
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
            if parsed and parsed.get("regions"):
                # Defensive: same fraction-vs-percentage bug seen elsewhere in
                # this pipeline - normalize whole-list fractions (sum ~1) but
                # leave individually-small-but-correct percentages alone.
                regs = parsed["regions"]
                total = sum(r.get("pct") or 0 for r in regs)
                if 0 < total <= 2.5:
                    for r in regs:
                        if r.get("pct") is not None:
                            r["pct"] = round(r["pct"] * 100, 2)
                out[tid] = {"fiscal_period": parsed.get("fiscal_period"),
                            "regions": regs, "source": parsed.get("source"), "fetched": today}
                print(f"  {tid:8} {parsed.get('fiscal_period')}: " +
                      ", ".join(f"{r['name']}={r['pct']}%" for r in regs))
            else:
                out[tid] = {"fiscal_period": None, "regions": [], "source": None, "fetched": today}
                print(f"  {tid:8} no geo breakdown found")
            OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    found = sum(1 for v in out.values() if v.get("regions"))
    print(f"geo_mix: {found}/{len(out)} with a geo breakdown -> {OUT}")


if __name__ == "__main__":
    main()
