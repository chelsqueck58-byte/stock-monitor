"""Food-delivery / instant-commerce loss tracker: Meituan (3690), Alibaba
(9988), JD (9618). The China spending-war question is whether losses are
NARROWING - each company discloses a different proxy metric, so the tracker
records each company's own metric with its basis, per quarter, plus the
latest narrowing signal from management/analyst commentary.

Writes data/delivery-war.json (3-way synced):
{"companies":[{"tid","label","metric_name","metric_basis",
   "periods":[{"q":"e.g. Q2 2026","value":"e.g. -RMB22.1bn","src":"..."}],
   "latest_signal":"1-2 sentences - narrowing or not, per latest commentary",
   "narrowing":"yes|mixed|no"}],
 "war_summary":"2-3 sentences on the overall subsidy-war state",
 "fetched":"YYYY-MM-DD"}

Run: .venv/bin/python scripts/delivery_war.py
"""
import datetime
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "delivery-war.json"


def ask_claude(prompt, timeout=800):
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
    today = datetime.date.today()
    # 3-day TTL - the loss metrics only change quarterly; daily re-research
    # would be pure spend
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text())
            age = (today - datetime.date.fromisoformat(prev.get("fetched", ""))).days
            if age < 3 and prev.get("companies"):
                print(f"delivery_war: fresh ({age}d old), skipping")
                return
        except (ValueError, json.JSONDecodeError, OSError):
            pass
    prompt = (
        f"Today is {today.isoformat()}. Research the China food-delivery / instant-"
        "commerce subsidy war between Meituan (3690.HK), Alibaba (9988.HK, Taobao "
        "Instant Commerce / Ele.me) and JD.com (9618.HK, JD Takeaway). The investing "
        "question: are the losses NARROWING?\n\n"
        "For EACH of the three, from actual quarterly results and earnings-call "
        "commentary (never invent numbers):\n"
        "- metric_name/metric_basis: the company's OWN closest disclosed loss proxy "
        "(e.g. Meituan core local commerce operating profit decline or new-initiatives "
        "loss; Alibaba's disclosed instant-commerce EBITA drag; JD's new-business "
        "segment operating loss) - name the exact disclosed line used\n"
        "- periods: last 3-5 quarters of that metric, each {\"q\":\"Q2 2026\", "
        "\"value\":\"-RMB13.1bn\", \"src\":\"filing/call\"} - null/omit quarters where "
        "not disclosed\n"
        "- latest_signal: 1-2 sentences on the most recent narrowing/widening evidence "
        "(mgmt commentary, subsidy pullbacks, order-economics statements)\n"
        "- narrowing: your call - \"yes\", \"mixed\", or \"no\"\n"
        "Plus war_summary: 2-3 sentences on the overall state (subsidy intensity, "
        "regulator pressure on 'involution', who blinks first).\n\n"
        "SOURCES: these are HK-listed Chinese companies - search CHINESE-LANGUAGE "
        "coverage and cite it (LatePost/晚点 has the definitive delivery-war reporting; "
        "also 36kr, Caixin, Jiemian, Sina Finance, 21jingji) alongside HKEX filings "
        "and earnings-call transcripts. English wires alone are insufficient.\n\n"
        "CRITICAL: reply with ONLY the JSON object:\n"
        '{"companies":[{"tid":"3690","label":"Meituan","metric_name":"...",'
        '"metric_basis":"...","periods":[...],"latest_signal":"...","narrowing":"..."}],'
        '"war_summary":"..."}'
    )
    raw = ask_claude(prompt)
    obj = parse_obj(raw)
    if not obj and raw.strip():
        obj = parse_obj(ask_claude("Extract ONLY the JSON object. Reply ONLY JSON.\n\n" + raw))
    if not obj or not obj.get("companies"):
        print("delivery_war: no result - keeping previous file")
        return
    obj["fetched"] = today.isoformat()
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    OUT.write_text(payload)
    (ROOT / "site" / "delivery-war.json").write_text(payload)
    (ROOT / "delivery-war.json").write_text(payload)
    print(f"delivery_war: {len(obj['companies'])} companies -> {OUT}")


if __name__ == "__main__":
    main()
