"""Revenue segment breakdown (% of total revenue by business segment) from
each company's most recent filing (10-Q/10-K/20-F/6-K/interim/annual report),
via web search - grounded, blank if no source. Scoped to china-internet,
compute, and hyperscaler-cloud categories per the site's "Revenue Mix" tab.

Not a daily pipeline stage: segment mix only moves quarterly with earnings,
so this is meant to be re-run occasionally (e.g. after each ticker's next
earnings date passes), not every day. Writes data/revenue-breakdown.json:
{tid: {"fiscal_period": "...", "segments": [{"name":"...","pct":0.0}, ...],
       "source": "...", "fetched": "YYYY-MM-DD"}}

Run:  .venv/bin/python scripts/revenue_breakdown.py [--tickers TID,TID,...]
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
OUT = ROOT / "data" / "revenue-breakdown.json"
WORKERS = 6
CATEGORIES = {"china-internet", "compute", "hyperscaler-cloud", "platform"}


def ask_claude(prompt):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    r = subprocess.run(["claude", "-p", prompt, "--allowedTools", "WebSearch"],
                        capture_output=True, text=True, env=env, timeout=240)
    return r.stdout.strip() if r.returncode == 0 else ""


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
        f"Find {label} ({tid})'s revenue breakdown by business segment from its "
        "MOST RECENT quarterly or annual filing (10-Q, 10-K, 20-F, 6-K, or "
        "official investor-relations earnings release - whichever is most "
        "recent). I need each segment's revenue as a PERCENT of total revenue "
        "for that period.\n"
        "RULES: use the company's own reported segment names (e.g. 'iPhone', "
        "'Services', 'Cloud', 'Commerce' - whatever they actually call them). "
        "Percentages should sum to approximately 100 (rounding is fine). Only "
        "include segments you can find real numbers for for - never estimate "
        "or invent a split. If the company is too newly listed to have a "
        "segment breakdown in any filing yet, say so.\n"
        "CRITICAL: reply with ONLY the JSON object, nothing else - no prose, "
        "no markdown fencing.\n"
        'Format: {"fiscal_period":"e.g. Q2 FY2026 or FY2025","segments":'
        '[{"name":"...","pct":0.0}, ...],"source":"outlet or filing name"}\n'
        'If no breakdown is findable: {"fiscal_period":null,"segments":[],'
        '"source":null}'
    )
    raw = ask_claude(prompt)
    parsed = parse_obj(raw)
    if not parsed and raw.strip():
        fixup = (
            "Extract ONLY the JSON object from this text, matching "
            '{"fiscal_period":"...","segments":[{"name":"...","pct":0.0}],'
            '"source":"..."}. Reply with ONLY the JSON object.\n\n' + raw
        )
        parsed = parse_obj(ask_claude(fixup))
    return tid, label, parsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", help="comma-separated ticker ids to (re)research; default all in scope")
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
            if parsed and parsed.get("segments"):
                out[tid] = {"fiscal_period": parsed.get("fiscal_period"),
                            "segments": parsed["segments"],
                            "source": parsed.get("source"), "fetched": today}
                print(f"  {tid:8} {parsed.get('fiscal_period')}: " +
                      ", ".join(f"{s['name']}={s['pct']}%" for s in parsed["segments"]))
            else:
                out[tid] = {"fiscal_period": None, "segments": [], "source": None, "fetched": today}
                print(f"  {tid:8} no breakdown found")
            OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    found = sum(1 for v in out.values() if v.get("segments"))
    print(f"revenue_breakdown: {found}/{len(out)} with a segment breakdown -> {OUT}")


if __name__ == "__main__":
    main()
