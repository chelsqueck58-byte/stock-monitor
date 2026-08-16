"""Off-balance-sheet / off-balance-sheet-adjacent liabilities from each
company's most recent filing - operating lease commitments, purchase
obligations, guarantees, unconsolidated VIE exposure, etc. Grounded via web
search, blank if not disclosed. Yahoo's free API has no structured field for
this (balanceSheetHistory module confirmed empty on a live check) - genuine
research gap, not a fetch-fix.

Scoped to china-internet, compute, hyperscaler-cloud, platform categories,
same as the rest of the Compare tab's research-derived data, plus any
individually-added extras (ASML).

Writes data/off-bs-liabilities.json:
{tid: {"items": [{"name":"...","amount_usd_m":0.0,"note":"..."}],
       "source": "...", "fetched": "YYYY-MM-DD"}}

Run:  .venv/bin/python scripts/off_bs_liabilities.py [--tickers TID,TID,...]
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
OUT = ROOT / "data" / "off-bs-liabilities.json"
WORKERS = 6
CATEGORIES = {"china-internet", "compute", "hyperscaler-cloud", "platform"}
EXTRA_TICKERS = {"ASML"}


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
        f"Find {label} ({tid})'s off-balance-sheet or off-balance-sheet-adjacent "
        "liabilities from its most recent 10-K/10-Q/20-F/annual report "
        "commitments & contingencies footnote. Include whichever of these "
        "the company actually discloses: total future operating lease "
        "payments not yet on balance sheet (if any remain off-BS under "
        "current accounting), purchase obligations/commitments, guarantees, "
        "unconsolidated VIE (variable interest entity) exposure, or other "
        "contractual commitments disclosed in that footnote.\n"
        "RULES: only report what you find real grounded evidence for, with "
        "the dollar figure and what it covers. If the company has nothing "
        "material disclosed there, say so.\n"
        "CRITICAL: reply with ONLY the JSON object, nothing else.\n"
        'Format: {"items":[{"name":"...","amount_usd_m":0.0,"note":"..."}],'
        '"source":"..."}\n'
        'If nothing material found: {"items":[],"source":null}'
    )
    raw = ask_claude(prompt)
    parsed = parse_obj(raw)
    if not parsed and raw.strip():
        fixup = (
            "Extract ONLY the JSON object from this text. Reply with ONLY the JSON object.\n\n" + raw
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
        todo_ids = [tid for tid, info in scm.items()
                    if info.get("category") in CATEGORIES or tid in EXTRA_TICKERS]

    todo = [(tid, labels.get(tid, scm.get(tid, {}).get("name", tid))) for tid in todo_ids]
    print(f"{len(todo)} tickers to research")

    out = json.loads(OUT.read_text()) if OUT.exists() else {}
    today = datetime.date.today().isoformat()

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for tid, label, parsed in ex.map(research_one, todo):
            if parsed:
                out[tid] = {"items": parsed.get("items") or [],
                            "source": parsed.get("source"), "fetched": today}
                print(f"  {tid:8} {len(out[tid]['items'])} items")
            else:
                out[tid] = {"items": [], "source": None, "fetched": today}
                print(f"  {tid:8} no data found")
            OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    found = sum(1 for v in out.values() if v.get("items"))
    print(f"off_bs_liabilities: {found}/{len(out)} with disclosed items -> {OUT}")


if __name__ == "__main__":
    main()
