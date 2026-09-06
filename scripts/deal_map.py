"""Deal map: who are each AI supplier's customers - signed and reported deals,
one compact line each. Rendered as the Deal Map card wall.

Writes data/deal-map.json (3-way synced):
{"suppliers":[{"tid","label","customers":[
    {"name":"GOOGL","deal":"TPU v8/v9 co-design","status":"confirmed|reported",
     "src":"..."}]}],
 "fetched":"YYYY-MM-DD"}

7-day TTL. Run: .venv/bin/python scripts/deal_map.py [--force]
"""
import argparse
import datetime
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "deal-map.json"


def ask_claude(prompt, timeout=900):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run(["claude", "-p", prompt, "--allowedTools", "WebSearch"],
                           capture_output=True, text=True, env=env, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    today = datetime.date.today()
    if OUT.exists() and not args.force:
        try:
            prev = json.loads(OUT.read_text())
            age = (today - datetime.date.fromisoformat(prev.get("fetched", ""))).days
            if age < 7 and prev.get("suppliers"):
                print(f"deal_map: fresh ({age}d), skipping")
                return
        except (ValueError, json.JSONDecodeError, OSError):
            pass

    prompt = (
        f"Today is {today.isoformat()}. Build a concise AI supply-chain DEAL MAP: for "
        "each supplier below, its major disclosed customer relationships in AI - who "
        "buys what. Suppliers (use these tids): NVDA, AMD, AVGO, MRVL, INTC, TSM, MU, "
        "000660 (SK Hynix), 005930 (Samsung), and the cloud/compute sellers MSFT, "
        "GOOGL, AMZN, ORCL (their AI compute customer deals, e.g. Amazon-Anthropic, "
        "Microsoft-OpenAI, Oracle-OpenAI).\n\n"
        "For each supplier list 3-7 of the MOST MATERIAL customer relationships. Each "
        "customer entry:\n"
        "- name: the customer (ticker or name: MSFT/GOOGL/AMZN/META/AAPL/OpenAI/"
        "Anthropic/xAI/Tesla/NVDA/...)\n"
        "- deal: what they buy, <=45 chars, specific (e.g. 'TPU v8/v9 ASIC co-design', "
        "'HBM4 12-hi for Vera Rubin', 'Maia 300 custom silicon', '18A foundry "
        "(preliminary)', '$100B+ compute commitment')\n"
        "- status: 'confirmed' (signed/disclosed in filings or official statements) or "
        "'reported' (credible press, not confirmed by the companies)\n"
        "- src: outlet or filing\n"
        "Ground everything in real reporting - verify by web search where unsure; do "
        "NOT invent relationships. Keep deals CURRENT (drop long-dead ones).\n\n"
        "CRITICAL: reply with ONLY the JSON object:\n"
        '{"suppliers":[{"tid":"AVGO","label":"Broadcom","customers":'
        '[{"name":"GOOGL","deal":"...","status":"confirmed","src":"..."}]}]}'
    )
    raw = ask_claude(prompt)
    obj = parse_obj(raw)
    if not obj and raw.strip():
        obj = parse_obj(ask_claude("Extract ONLY the JSON object. Reply ONLY JSON.\n\n" + raw))
    if not obj or not obj.get("suppliers"):
        print("deal_map: no result - keeping previous")
        return
    for s in obj["suppliers"]:
        s["customers"] = [c for c in (s.get("customers") or [])
                          if c.get("name") and c.get("deal")][:7]
    obj["fetched"] = today.isoformat()
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    OUT.write_text(payload)
    (ROOT / "site" / "deal-map.json").write_text(payload)
    (ROOT / "deal-map.json").write_text(payload)
    print(f"deal_map: {len(obj['suppliers'])} suppliers -> {OUT}")


if __name__ == "__main__":
    main()
