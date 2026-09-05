"""AI model-launch history for the model-builder names: what shipped, whether
the market judged it a success, why, and what the stock actually did (move
computed from our own price bars, not researched).

Two blocks in data/model-launches.json (3-way synced):
- tickers: {tid: {"launches":[{"date","model","claim","verdict":
    "success|mixed|flop","why","src","move_pct"(computed)}], "fetched"}}
- ecosystem: launches by private labs (OpenAI/Anthropic/DeepSeek/xAI) that
  moved PUBLIC names - each carries affected tickers and their computed moves:
  [{"date","model","lab","why_it_mattered","affected":[{"tid","move_pct"}],"src"}]

Run: .venv/bin/python scripts/model_launches.py [--tickers ...] [--fresh-days N]
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import feed

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE = ROOT / "config" / "universe.json"
DATA_JSON = ROOT / "site" / "data.json"
OUT = ROOT / "data" / "model-launches.json"
WORKERS = 3
AI_TICKERS = ["META", "GOOGL", "MSFT", "AMZN", "9988", "0700"]
CHINA_TIDS = {"9988", "0700"}
CHINA_HINT = ("\nThis is a China-listed company: actively search Chinese-language "
              "sources (36kr, LatePost/晚点, Caixin, Jiemian, Sina, IT之家) and cite "
              "them alongside English coverage.\n")


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


def ask_and_parse(prompt):
    raw = ask_claude(prompt)
    obj = parse_obj(raw)
    if not obj and raw.strip():
        obj = parse_obj(ask_claude("Extract ONLY the JSON object. Reply ONLY JSON.\n\n" + raw))
    return obj


def research_launches(tid, label):
    prompt = (
        f"Today is {datetime.date.today().isoformat()}. Build the AI MODEL LAUNCH "
        f"history for {label} ({tid}) over the last ~2 years. Every notable model/"
        "agent-product release (foundation models, major versions, agent products, "
        "open-weight releases). For each:\n"
        "- date (YYYY-MM-DD, the announcement/release date)\n"
        "- model: the name/version\n"
        "- claim: what the company claimed at launch (<=100 chars)\n"
        "- verdict: how the MARKET/developer community judged it within ~a month - "
        "'success' (adoption/benchmarks validated, narrative win), 'mixed', or 'flop' "
        "(benchmarks disputed, adoption poor, walked back)\n"
        "- why: the reason for that verdict (<=130 chars) - benchmark gaming "
        "accusations, real adoption numbers, developer sentiment, competitive leapfrog\n"
        "- src: outlet(s)\n"
        "8-14 launches, chronological. Judge verdicts from real contemporaneous "
        "coverage, not the company's own claims. Do NOT include stock-price reactions "
        "- computed separately from price data.\n"
        + (CHINA_HINT if tid in CHINA_TIDS else "")
        + "\nCRITICAL: reply with ONLY the JSON object:\n"
        '{"launches":[{"date":"...","model":"...","claim":"...","verdict":"...",'
        '"why":"...","src":"..."}]}'
    )
    return ask_and_parse(prompt)


def research_ecosystem():
    prompt = (
        f"Today is {datetime.date.today().isoformat()}. List the 8-12 AI model releases "
        "by PRIVATE labs (OpenAI, Anthropic, DeepSeek, xAI, Mistral...) over the last "
        "~2 years that MOVED PUBLIC AI/semi stocks - the DeepSeek-R1-in-Jan-2025 kind "
        "of event. For each:\n"
        "- date, model, lab\n"
        "- why_it_mattered: the transmission to public names (<=130 chars) - e.g. "
        "'training-efficiency claims hit the AI-capex thesis'\n"
        "- affected: 1-4 public tickers most moved, from: META GOOGL MSFT AMZN NVDA "
        "AMD AVGO TSM MU 9988 0700 (just ticker strings - moves computed separately)\n"
        "- src\n"
        "CRITICAL: reply with ONLY the JSON object:\n"
        '{"events":[{"date":"...","model":"...","lab":"...","why_it_mattered":"...",'
        '"affected":["NVDA"],"src":"..."}]}'
    )
    return ask_and_parse(prompt)


def move_for(bars_map, tid, date):
    bars = bars_map.get(tid) or []
    for i, b in enumerate(bars):
        if b["d"] >= date and i > 0:
            return round((b["c"] / bars[i - 1]["c"] - 1) * 100, 1)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers")
    ap.add_argument("--fresh-days", type=int, default=14)
    args = ap.parse_args()

    universe = json.loads(UNIVERSE.read_text())
    labels = {m["id"]: m["label"] for g in universe["groups"] for m in g["members"]}
    data = json.loads(DATA_JSON.read_text())
    bars_map = {i["id"]: i.get("bars") or [] for i in data.get("instruments", [])}

    today = datetime.date.today()
    out = {"tickers": {}, "ecosystem": {"events": [], "fetched": None}}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text())
            out["tickers"] = prev.get("tickers") or {}
            if isinstance(prev.get("ecosystem"), dict):
                out["ecosystem"] = prev["ecosystem"]
        except (json.JSONDecodeError, OSError):
            pass

    def fresh(block):
        try:
            return (block and block.get("fetched")
                    and (today - datetime.date.fromisoformat(block["fetched"])).days
                    < args.fresh_days)
        except ValueError:
            return False

    todo_ids = [t.strip() for t in args.tickers.split(",")] if args.tickers else AI_TICKERS
    todo = [t for t in todo_ids if not fresh(out["tickers"].get(t))]
    print(f"{len(todo)} tickers due: {todo}", flush=True)

    lock = threading.Lock()

    def save():
        payload = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
        OUT.write_text(payload)
        (ROOT / "site" / "model-launches.json").write_text(payload)
        (ROOT / "model-launches.json").write_text(payload)

    def run_one(tid):
        obj = research_launches(tid, labels.get(tid, tid))
        with lock:
            launches = [l for l in (obj or {}).get("launches") or []
                        if l.get("date") and l.get("model")]
            if launches:
                for l in launches:
                    l["move_pct"] = move_for(bars_map, tid, str(l["date"])[:10])
                    if l.get("verdict") not in ("success", "mixed", "flop"):
                        l["verdict"] = "mixed"
                launches.sort(key=lambda l: l["date"])
                out["tickers"][tid] = {"launches": launches, "fetched": today.isoformat()}
                print(f"  {tid:6} {len(launches)} launches", flush=True)
                save()
            else:
                print(f"  {tid:6} EMPTY - keeping previous", flush=True)

    jobs = [lambda t=tid: run_one(t) for tid in todo]
    if not fresh(out["ecosystem"]):
        def run_eco():
            obj = research_ecosystem()
            with lock:
                events = [e for e in (obj or {}).get("events") or []
                          if e.get("date") and e.get("model")]
                if events:
                    for e in events:
                        e["affected"] = [{"tid": t, "move_pct": move_for(bars_map, t, str(e["date"])[:10])}
                                         for t in (e.get("affected") or []) if t in bars_map][:4]
                    events.sort(key=lambda e: e["date"])
                    out["ecosystem"] = {"events": events, "fetched": today.isoformat()}
                    print(f"  ECO    {len(events)} events", flush=True)
                    save()
                else:
                    print("  ECO    EMPTY - keeping previous", flush=True)
        jobs.append(run_eco)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(lambda j: j(), jobs))
    save()
    print(f"model_launches: {len(out['tickers'])} tickers, "
          f"{len(out['ecosystem'].get('events', []))} ecosystem events -> {OUT}")


if __name__ == "__main__":
    main()
