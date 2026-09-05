"""Deep catalyst enrichment for the dedicated catalysts page (catalysts.html).

Two LLM research passes over the existing 3-month calendar:

1. EVENT ENRICHMENT - for every calendar event of a covered ticker:
   - spillover: which OTHER tickers this event realistically moves (the
     value-chain read: a TSMC print moves NVDA/AMD/ASML; a Meta capex guide
     moves NVDA/AVGO/MU)
   - signal: "high" (historically moves the stock / chain >2%), "med", or
     "noise" (headline that rarely prints) + a one-line why

2. EARNINGS PREVIEWS - for covered names reporting within PREVIEW_DAYS:
   {expects, watch[], prior_pattern, read} - what consensus needs, the 2-4
   watch items, how the stock has traded its recent prints, and the one-line
   event-trader read.

Writes data/catalyst-deep.json (3-way synced to site/ and root):
{"enrich": {"TID|YYYY-MM-DD|slug": {"spillover":["TID",...],
            "signal":"high|med|noise","why":"..."}},
 "previews": {tid: {"earnings_date":"...","expects":"...","watch":[...],
              "prior_pattern":"...","read":"...","fetched":"..."}},
 "fetched": "YYYY-MM-DD"}

Run:  .venv/bin/python scripts/catalyst_deep.py [--force]
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

ROOT = Path(__file__).resolve().parent.parent
CAL = ROOT / "data" / "catalyst-calendar.json"
UNIVERSE = ROOT / "config" / "universe.json"
DATA_JSON = ROOT / "site" / "data.json"
OUT = ROOT / "data" / "catalyst-deep.json"

COVERED = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA",
           "AMD", "AVGO", "MRVL", "INTC", "TSM", "SMI", "ASML", "AMAT",
           "LRCX", "KLAC", "MU", "000660", "005930", "2454", "688256", "SMCI",
           "TSLA", "9988", "0700", "3690", "PDD", "1810", "6181"]
PREVIEW_DAYS = 40   # research an earnings preview once the print is this close
FRESH_DAYS = 3
WORKERS = 3


def ask_claude(prompt, timeout=700):
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
    parsed = parse_obj(raw)
    if not parsed and raw.strip():
        parsed = parse_obj(ask_claude(
            "Extract ONLY the JSON object from this text. Reply with ONLY the JSON.\n\n" + raw))
    return parsed


def ev_key(tid, e):
    slug = re.sub(r"[^a-z0-9]+", "-", (e.get("title") or "").lower())[:40].strip("-")
    return f"{tid}|{e.get('date')}|{slug}"


def enrich_events(events_block, labels):
    """events_block: list of (key, tid, event-dict). One LLM call per batch."""
    listing = "\n".join(
        f"- KEY={k} | {tid} ({labels.get(tid, tid)}) | {e['date']} | {e.get('type')} | {e.get('title')}"
        for k, tid, e in events_block)
    prompt = (
        "You are an event-driven trader covering the AI supply chain (hyperscalers -> "
        "GPU/ASIC -> foundry -> memory -> equipment -> optics/power). For EACH catalyst "
        "event below, assess:\n"
        "1. spillover: which OTHER stock tickers (from this coverage set: "
        + ", ".join(COVERED) +
        ") the event realistically moves through the value chain - customers, suppliers, "
        "competitors. 0-5 tickers, only genuinely-affected ones, NEVER the event's own "
        "ticker.\n"
        "2. signal: 'high' = the kind of event that historically moves the stock or its "
        "chain >2% (earnings of a bellwether, FOMC, a binding regulatory date, a major "
        "product launch with revenue impact); 'med' = usually matters but rarely a big "
        "single-day mover (most conferences, mgmt appearances, monthly datapoints); "
        "'noise' = headline theater that rarely prints (ceremonial events, long-dated "
        "openings, PR-driven items).\n"
        "3. why: one clause (<=90 chars) justifying the signal rating.\n"
        "Use web search only if genuinely unsure about an event's nature.\n\n"
        f"EVENTS:\n{listing}\n\n"
        "CRITICAL: reply with ONLY the JSON object keyed by the exact KEY strings.\n"
        'Output ONLY: {"KEY": {"spillover":["TID"],"signal":"high|med|noise","why":"..."}}'
    )
    return ask_and_parse(prompt) or {}


def research_preview(tid, label, earn_date, prev_reactions):
    hist = "; ".join(f"{r['date']}: {r['pct']:+.1f}% ({r['reason'][:70]})"
                     for r in (prev_reactions or [])[:4]) or "none logged"
    prompt = (
        f"You are an event-driven trader preparing for {label} ({tid})'s earnings on "
        f"{earn_date}. Research CURRENT consensus and the live debate, then produce a "
        "tight preview:\n"
        "- expects: what consensus needs (revenue/EPS/segment numbers where findable, "
        "and the guidance bar) - <=220 chars\n"
        "- watch: the 2-4 specific line items / commentary the market will actually "
        "trade (e.g. capex guide, a segment's growth rate, a margin figure, a named "
        "product's ramp) - each <=90 chars\n"
        "- prior_pattern: how the stock has traded its recent prints, using this logged "
        f"history plus anything you find: {hist} - <=180 chars\n"
        "- read: the one-line event-trader read - where the asymmetry is - <=160 chars\n"
        "Ground everything in real, current sources (last ~30 days preferred). No "
        "invented numbers - vague-but-true beats precise-but-wrong.\n"
        "CRITICAL: reply with ONLY the JSON object.\n"
        'Output ONLY: {"expects":"...","watch":["..."],"prior_pattern":"...","read":"..."}'
    )
    return ask_and_parse(prompt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    cal = json.loads(CAL.read_text())
    universe = json.loads(UNIVERSE.read_text())
    labels = {m["id"]: m["label"] for g in universe["groups"] for m in g["members"]}
    data = json.loads(DATA_JSON.read_text()) if DATA_JSON.exists() else {"instruments": []}
    inst_by_id = {i["id"]: i for i in data.get("instruments", [])}

    today = datetime.date.today()
    out = {"enrich": {}, "previews": {}, "fetched": today.isoformat()}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text())
            out["enrich"] = prev.get("enrich") or {}
            out["previews"] = prev.get("previews") or {}
        except (json.JSONDecodeError, OSError):
            pass

    lock = threading.Lock()

    def save():
        payload = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
        OUT.write_text(payload)
        (ROOT / "site" / "catalyst-deep.json").write_text(payload)
        (ROOT / "catalyst-deep.json").write_text(payload)

    # ---- pass 1: event enrichment ----
    todo_events = []
    for tid in COVERED:
        block = (cal.get("tickers") or {}).get(tid)
        for e in (block or {}).get("events") or []:
            k = ev_key(tid, e)
            if args.force or k not in out["enrich"]:
                todo_events.append((k, tid, e))
    for e in (cal.get("macro") or {}).get("events") or []:
        k = ev_key("MACRO", e)
        if args.force or k not in out["enrich"]:
            todo_events.append((k, "MACRO", e))
    print(f"{len(todo_events)} events to enrich")

    def run_enrich(batch):
        result = enrich_events(batch, labels)
        with lock:
            n = 0
            for k, tid, e in batch:
                r = result.get(k)
                if isinstance(r, dict) and r.get("signal") in ("high", "med", "noise"):
                    out["enrich"][k] = {
                        "spillover": [t for t in (r.get("spillover") or [])
                                      if t in COVERED and t != tid][:5],
                        "signal": r["signal"],
                        "why": str(r.get("why") or "")[:110],
                    }
                    n += 1
            save()
            print(f"  enriched {n}/{len(batch)}", flush=True)

    # ---- pass 2: earnings previews ----
    def preview_fresh(tid):
        p = out["previews"].get(tid)
        if not p or args.force:
            return False
        try:
            age = (today - datetime.date.fromisoformat(p["fetched"])).days
        except (KeyError, ValueError):
            return False
        return age < FRESH_DAYS

    preview_todo = []
    for tid in COVERED:
        inst = inst_by_id.get(tid)
        earn = ((inst or {}).get("fund") or {}).get("next_earnings")
        if not earn:
            continue
        try:
            days = (datetime.date.fromisoformat(earn) - today).days
        except ValueError:
            continue
        if 0 <= days <= PREVIEW_DAYS and not preview_fresh(tid):
            preview_todo.append((tid, labels.get(tid, tid), earn,
                                 (inst or {}).get("prev_earnings")))
    print(f"{len(preview_todo)} earnings previews to research: {[t[0] for t in preview_todo]}")

    def run_preview(item):
        tid, label, earn, prev = item
        p = research_preview(tid, label, earn, prev)
        with lock:
            if p and p.get("expects"):
                out["previews"][tid] = {
                    "earnings_date": earn,
                    "expects": str(p.get("expects"))[:260],
                    "watch": [str(w)[:110] for w in (p.get("watch") or [])][:4],
                    "prior_pattern": str(p.get("prior_pattern") or "")[:220],
                    "read": str(p.get("read") or "")[:190],
                    "fetched": today.isoformat(),
                }
                print(f"  preview {tid} ok", flush=True)
            else:
                print(f"  preview {tid} EMPTY", flush=True)
            save()

    BATCH = 18
    jobs = [lambda b=todo_events[i:i + BATCH]: run_enrich(b)
            for i in range(0, len(todo_events), BATCH)]
    jobs += [lambda it=item: run_preview(it) for item in preview_todo]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(lambda j: j(), jobs))

    save()
    print(f"catalyst_deep: {len(out['enrich'])} enriched events, "
          f"{len(out['previews'])} previews -> {OUT}")


if __name__ == "__main__":
    main()
