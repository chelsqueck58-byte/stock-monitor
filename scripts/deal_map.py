"""Deal map: who are each AI supplier's customers - signed and reported deals,
one compact line each. Rendered as the Deal Map card wall.

Writes data/deal-map.json (3-way synced):
{"groups":[{"key":"hyperscalers","label":"Hyperscalers",
  "suppliers":[{"tid","label","customers":[
    {"name":"GOOGL","deal":"TPU v8/v9 co-design","status":"confirmed|reported",
     "src":"..."}],
    "disclosed_mix":"company's own stated customer-concentration breakdown, "
      "e.g. NVIDIA: ~20% Anthropic/OpenAI, ~50% neocloud (mgmt commentary)"}]}],
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
        f"Today is {today.isoformat()}. Build a concise AI supply-chain DEAL MAP, "
        "organized into FOUR groups:\n"
        "1. gpu_accel (key: 'gpu_accel', label 'GPU / Accelerators / Foundry'): NVDA, "
        "AMD, INTC, TSM\n"
        "2. custom_silicon (key: 'custom_silicon', label 'Custom Silicon (ASIC "
        "design)'): AVGO, MRVL, 2454 (MediaTek)\n"
        "3. memory (key: 'memory', label 'Memory (HBM/DRAM)'): MU, 000660 (SK Hynix), "
        "005930 (Samsung)\n"
        "4. hyperscalers (key: 'hyperscalers', label 'Hyperscalers (compute sellers)'): "
        "MSFT, GOOGL, AMZN, ORCL, META - their AI compute/capacity deals with AI labs "
        "(e.g. Microsoft-OpenAI, Amazon-Anthropic, Oracle-OpenAI, Meta-external labs)\n\n"
        "For each supplier, two things:\n"
        "A) customers: 3-7 of its MOST MATERIAL customer relationships. Each: "
        '{"name":"the customer (ticker or name: MSFT/GOOGL/AMZN/META/AAPL/OpenAI/'
        "Anthropic/xAI/Tesla/...)\", \"deal\":\"what they buy, <=45 chars, specific "
        "e.g. 'TPU v8/v9 ASIC co-design', 'HBM4 12-hi for Vera Rubin', '$100B+ compute "
        "commitment'\", \"status\":\"confirmed\"|\"reported\", \"src\":\"outlet/filing\"}\n"
        "B) disclosed_mix: a SEPARATE thing from the customer list - the company's OWN "
        "stated breakdown of its revenue/demand mix BY CUSTOMER TYPE or concentration, "
        "from earnings calls or filings. Example: NVIDIA management has disclosed "
        "roughly ~20% of data center revenue to OpenAI/Anthropic-type AI labs and ~50% "
        "to neocloud/GPU-cloud providers, with the rest to traditional hyperscalers - "
        "find NVIDIA's actual current disclosed split, and similarly for others if "
        "they've given one (e.g. AMD or Broadcom customer concentration %, TSM revenue "
        "by customer tier). Format: \"<=180 chars, name the metric and who said it\" - "
        "omit (null) if the company hasn't disclosed a mix.\n\n"
        "Ground everything in real reporting - verify by web search where unsure; do "
        "NOT invent relationships or numbers. Keep deals CURRENT (drop long-dead ones).\n\n"
        "CRITICAL: reply with ONLY the JSON object:\n"
        '{"groups":[{"key":"gpu_accel","label":"GPU / Accelerators / Foundry",'
        '"suppliers":[{"tid":"NVDA","label":"NVIDIA","disclosed_mix":"...",'
        '"customers":[{"name":"OpenAI","deal":"...","status":"confirmed","src":"..."}]'
        "}]}]}"
    )
    raw = ask_claude(prompt)
    obj = parse_obj(raw)
    if not obj and raw.strip():
        obj = parse_obj(ask_claude("Extract ONLY the JSON object. Reply ONLY JSON.\n\n" + raw))
    if not obj or not obj.get("groups"):
        print("deal_map: no result - keeping previous")
        return
    n_sup = 0
    for g in obj["groups"]:
        for s in g.get("suppliers") or []:
            s["customers"] = [c for c in (s.get("customers") or [])
                              if c.get("name") and c.get("deal")][:7]
            if not s.get("disclosed_mix"):
                s["disclosed_mix"] = None
            n_sup += 1
    obj["fetched"] = today.isoformat()
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    OUT.write_text(payload)
    (ROOT / "site" / "deal-map.json").write_text(payload)
    (ROOT / "deal-map.json").write_text(payload)
    print(f"deal_map: {len(obj['groups'])} groups, {n_sup} suppliers -> {OUT}")


if __name__ == "__main__":
    main()
