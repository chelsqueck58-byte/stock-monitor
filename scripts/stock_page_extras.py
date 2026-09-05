"""Per-stock-page research extras (Stock Pages tab), one combined call per name:

- past_events: notable events last ~6 months (launches, deals, guides, policy),
  INCLUDING ones the market shrugged off. The LLM supplies date/title/why-it-
  mattered; the ACTUAL 1-day move is computed afterwards from our own price
  bars (data.json) - objective, not researched - so "should have moved but
  didn't" is evidence, not opinion.
- pe_narrative: how the P/E has re-rated over ~2 years and the market's reason
  (interpretive - labeled as narrative on the page).
- guidance_revisions: how the company's own FY guides (capex for hyperscalers,
  revenue/margin elsewhere) moved print-over-print, cited to releases.
- earnings_preview: expects / watch / read for the next print.

Writes data/stock-page-extras.json (3-way synced):
{tid: {"past_events":[{"date","title","why","move_pct"(computed),"src"}],
       "pe_narrative":"...", "guidance_revisions":[{"when","change","src"}],
       "preview":{"expects","watch":[],"read"}, "fetched":"YYYY-MM-DD"}}

Run: .venv/bin/python scripts/stock_page_extras.py [--tickers ...] [--fresh-days N]
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
OUT = ROOT / "data" / "stock-page-extras.json"
WORKERS = 3
TICKERS = ["META", "NVDA", "9988", "6181", "AAPL", "MSFT", "GOOGL", "AMZN",
           "0700", "AMD", "AVGO", "INTC", "TSM", "ASML", "MU", "MRVL",
           "3690", "9618"]
# HK/China-listed names: research MUST lean on Chinese-language sources -
# product dates, delivery-war economics and policy moves appear there first.
CHINA_TIDS = {"9988", "0700", "3690", "9618", "1810", "6181", "SMI", "688256"}
CHINA_HINT = (
    "\nThis is a China/HK-listed company: actively search CHINESE-LANGUAGE sources "
    "and cite them - LatePost/晚点, 36kr, Caixin/财新, Jiemian/界面, Sina Finance/"
    "新浪财经, ifeng/凤凰网, 21jingji, eastmoney/东方财富, IT之家, ijiwei/爱集微 for "
    "semis, China Gold Association for gold - alongside HKEX filings and company IR. "
    "English wires alone are not sufficient sourcing for this name.\n")


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


def research(tid, label, next_earn):
    fx = feed.relevant_excerpt_week(tid, label, max_chars=2000)
    feed_block = (f"\nTHIS WEEK'S FEED (Telegram/Gmail/X analyst accounts) on this name - "
                  f"check it FIRST for chatter-sourced events and watch items, cite "
                  f"[X @handle]/[Telegram]/[Gmail]:\n{fx}\n" if fx else "")
    prompt = (
        f"Today is {datetime.date.today().isoformat()}. You are an event-driven trader "
        f"building a reference page for {label} ({tid}). Research (web search; official "
        "filings/IR and major outlets preferred) and produce FOUR blocks:\n\n"
        "1. past_events: the 8-14 most notable dated events of the LAST ~6 MONTHS - "
        "product launches, big deals/partnerships, guidance changes, regulatory/policy "
        "actions, major analyst actions, capital raises. INCLUDE events that seemed "
        "important but did NOT move the stock (do not filter to movers - the point is to "
        "see what the market shrugged off). Each: {\"date\":\"YYYY-MM-DD\", \"title\":"
        "\"<=110 chars\", \"why\":\"why it mattered / was expected to matter, <=110 chars\", "
        "\"src\":\"outlet\"}. Do NOT include the stock's price reaction - that is computed "
        "separately from price data.\n\n"
        "2. pe_narrative: 2-3 sentences (<=420 chars) on how the market's multiple for "
        "this name has re-rated over the last ~2 years and WHY (the market's stated "
        "reasons: rate cycle, AI narrative, capex fear, policy risk...), grounded in "
        "actual analyst/press commentary.\n\n"
        "3. guidance_revisions: how the company's OWN guidance moved print-over-print "
        "over the last ~4 quarters - for hyperscalers use the FY capex guide; otherwise "
        "the most-watched guide (FY revenue/GM/segment). 2-5 items, each {\"when\":"
        "\"e.g. Q2'26 call (Jul 2026)\", \"change\":\"e.g. FY26 capex raised to "
        "$125-145B from $115-135B\", \"src\":\"...\"}. Only real disclosed guides.\n\n"
        f"4. preview: for the next earnings ({next_earn or 'date TBC'}): {{\"expects\":"
        "\"consensus rev/EPS + the bar, <=220 chars\", \"watch\":[\"2-4 items, <=90 chars "
        "each\"], \"read\":\"one-line trader read on the asymmetry, <=160 chars\"}}.\n\n"
        "5. segment_explainers: for each CURRENT reportable revenue segment, what it "
        "actually sells, in investor terms with the flagship products/customers named - "
        "e.g. Broadcom Semiconductor Solutions = 'networking/custom AI chips (Tomahawk "
        "switches, XPUs for Google/Meta), broadband, wireless'. Each {\"segment\":"
        "\"exact segment name as reported\", \"sells\":\"<=140 chars\"}.\n\n"
        f"{feed_block}"
        f"{CHINA_HINT if tid in CHINA_TIDS else ''}"
        f"{feed.SOURCE_HINT}\n"
        "RULES: never invent dates/numbers; '(est.)' for estimates; every block cites "
        "sources. CRITICAL: reply with ONLY the JSON object:\n"
        '{"past_events":[...],"pe_narrative":"...","guidance_revisions":[...],'
        '"preview":{...},"segment_explainers":[...]}'
    )
    raw = ask_claude(prompt)
    obj = parse_obj(raw)
    if not obj and raw.strip():
        obj = parse_obj(ask_claude(
            "Extract ONLY the JSON object. Reply ONLY the JSON.\n\n" + raw))
    return obj


def attach_moves(events, bars):
    """Attach the actual next-trading-day close-to-close move for each event
    date, from our own daily bars - objective 'did it move' evidence."""
    dates = [b["d"] for b in bars]
    closes = [b["c"] for b in bars]
    for e in events:
        e["move_pct"] = None
        d = str(e.get("date") or "")[:10]
        # find first bar ON or AFTER the event date, compare with prior close
        for i, bd in enumerate(dates):
            if bd >= d and i > 0:
                e["move_pct"] = round((closes[i] / closes[i - 1] - 1) * 100, 1)
                break
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers")
    ap.add_argument("--fresh-days", type=int, default=5)
    args = ap.parse_args()

    universe = json.loads(UNIVERSE.read_text())
    labels = {m["id"]: m["label"] for g in universe["groups"] for m in g["members"]}
    data = json.loads(DATA_JSON.read_text())
    inst_by_id = {i["id"]: i for i in data.get("instruments", [])}

    today = datetime.date.today()
    out = {}
    if OUT.exists():
        try:
            out = json.loads(OUT.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    def fresh(tid):
        e = out.get(tid)
        if not e or not e.get("past_events"):
            return False
        try:
            return (today - datetime.date.fromisoformat(e["fetched"])).days < args.fresh_days
        except (KeyError, ValueError):
            return False

    todo_ids = [t.strip() for t in args.tickers.split(",")] if args.tickers else TICKERS
    todo = [t for t in todo_ids if not fresh(t)]
    print(f"{len(todo)} tickers due (skipped {len(todo_ids) - len(todo)} fresh): {todo}",
          flush=True)

    lock = threading.Lock()

    def save():
        payload = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
        OUT.write_text(payload)
        (ROOT / "site" / "stock-page-extras.json").write_text(payload)
        (ROOT / "stock-page-extras.json").write_text(payload)

    def run_one(tid):
        inst = inst_by_id.get(tid) or {}
        next_earn = (inst.get("fund") or {}).get("next_earnings")
        obj = research(tid, labels.get(tid, tid), next_earn)
        with lock:
            if obj and obj.get("past_events"):
                obj["past_events"] = attach_moves(
                    [e for e in obj["past_events"] if e.get("date") and e.get("title")],
                    inst.get("bars") or [])
                obj["fetched"] = today.isoformat()
                obj["segment_explainers"] = [
                    s for s in obj.get("segment_explainers") or []
                    if s.get("segment") and s.get("sells")]
                out[tid] = obj
                print(f"  {tid:8} {len(obj['past_events'])} events, "
                      f"{len(obj.get('guidance_revisions') or [])} guide revisions, "
                      f"preview={'y' if obj.get('preview') else 'n'}", flush=True)
                save()
            else:
                print(f"  {tid:8} EMPTY - keeping previous", flush=True)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(run_one, todo))
    save()
    print(f"stock_page_extras: {len(out)} tickers -> {OUT}")


if __name__ == "__main__":
    main()
