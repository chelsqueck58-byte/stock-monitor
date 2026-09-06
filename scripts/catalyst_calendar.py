"""Dated catalyst calendar for the ~20 key liquid names, next 6 months.

Unlike catalysts.py (one-line "live storyline" per priority ticker), this
produces MULTIPLE dated events per stock - earnings dates, product launches/
events, regulatory/legal decision dates, plus a shared macro/industry list
(FOMC, CPI, GTC, China policy meetings...). Rendered as the date-sorted
calendar on the Summary tab and, filtered, on each category tab.

Writes data/catalyst-calendar.json:
{"tickers": {tid: {"events": [{"date":"YYYY-MM-DD",
                               "precision":"day"|"month",
                               "type":"Earnings"|"Product"|"Regulatory"|"Industry",
                               "title":"...", "source":"..."}],
                   "fetched":"YYYY-MM-DD"}},
 "macro":   {"events": [... type:"Macro"|"Industry" ...], "fetched":"..."}}

precision "month" means the exact day isn't set - date holds a best-guess
placeholder inside that month and the frontend renders it as "~Oct".

FRESHNESS TTL: a ticker (or the macro block) refreshed within FRESH_DAYS is
skipped - a 3-month calendar doesn't change daily.

Run:  .venv/bin/python scripts/catalyst_calendar.py [--tickers TID,TID,...] [--force]
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
OUT = ROOT / "data" / "catalyst-calendar.json"
BATCH = 4
WORKERS = 4  # batches run in parallel claude calls; results written
             # incrementally under a lock, so a killed run keeps whatever
             # finished and the TTL lets a re-run resume the remainder
FRESH_DAYS = 3
KEY_TICKERS = ["META", "NVDA", "MSFT", "GOOGL", "AMZN", "AAPL", "TSLA",
               "TSM", "AVGO", "AMD", "ASML", "MU", "000660", "005930",
               "INTC", "SMI", "688256", "2454",
               "9988", "0700", "3690", "PDD", "1810", "6181"]

EVENT_SCHEMA = ('{"date":"YYYY-MM-DD","precision":"day" or "month",'
                '"type":"Earnings"|"Product"|"Regulatory"|"Mgmt"|"Industry",'
                '"title":"<=110 chars","source":"e.g. Reuters / company IR"}')


def ask_claude(prompt, timeout=600):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run(["claude", "-p", prompt, "--allowedTools", "WebSearch"],
                           capture_output=True, text=True, env=env, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def parse_json(text):
    if "```" in text:
        text = re.sub(r"```(json)?", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        return {}
    try:
        return json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return {}


def ask_and_parse(prompt):
    raw = ask_claude(prompt)
    result = parse_json(raw)
    if not result and raw.strip():
        fixup = ("Extract ONLY the JSON object from this text. "
                 "Reply with ONLY the JSON, nothing else.\n\n" + raw)
        result = parse_json(ask_claude(fixup))
    return result


def valid_events(events, today, horizon):
    out = []
    for e in events if isinstance(events, list) else []:
        try:
            d = datetime.date.fromisoformat(str(e.get("date", ""))[:10])
        except ValueError:
            continue
        if not (today <= d <= horizon):
            continue
        title = str(e.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "date": d.isoformat(),
            "precision": "month" if e.get("precision") == "month" else "day",
            "type": e.get("type") if e.get("type") in
                    ("Earnings", "Product", "Regulatory", "Mgmt", "Industry", "Macro") else "Industry",
            "title": title[:130],
            "source": str(e.get("source") or "").strip()[:60] or None,
        })
    out.sort(key=lambda e: e["date"])
    return out


def research_batch(batch, today, horizon):
    blocks = []
    for tid, label in batch:
        block = f"- {tid} ({label})"
        excerpt = feed.relevant_excerpt_week(tid, label)
        if excerpt:
            block += ("\n  THIS WEEK'S FEED (Telegram/Gmail/X) mentioning this name:\n  "
                      + excerpt.replace("\n", "\n  "))
        blocks.append(block)
    listing = "\n".join(blocks)
    prompt = (
        f"Today is {today.isoformat()}. For EACH stock below, find its dated upcoming "
        f"CATALYSTS between now and {horizon.isoformat()} (next ~6 months). Cover, where they "
        "exist:\n"
        "- next EARNINGS report date (confirmed from company IR if possible; if only an "
        "estimated date exists, still include it with '(est.)' in the title and "
        "precision 'month' unless the day is confirmed)\n"
        "- PRODUCT launches / company events (keynotes, developer conferences, model or "
        "chip launches, phone launches, capacity ramps with a stated date)\n"
        "- REGULATORY / LEGAL dates (rulings, remedy decisions, export-control deadlines, "
        "review conclusions) with a real expected timeframe\n"
        "- MGMT appearances: CEO/CFO fireside chats or keynotes at broker/industry "
        "conferences (Citi Global Tech, Goldman Communacopia, UBS/Morgan Stanley tech "
        "conferences, analyst days, investor days). For these, the title should name the "
        "executive + venue AND what the market is listening for, e.g. 'Lip-Bu Tan at Citi "
        "conf - watch 14A capex commitment'. Search recent market chatter/analyst previews "
        "to identify the watch item; if you can't find what the market is watching, still "
        "include the appearance if the event itself is confirmed\n"
        "- company-specific INDUSTRY dates that materially matter for the name (e.g. a key "
        "customer's launch, a monthly revenue release cadence worth flagging once)\n"
        "- recurring SALES/SHIPMENT datapoints with known release dates: monthly revenue "
        "releases (Taiwan-listed names), EV delivery numbers, smartphone sell-through "
        "reports, memory contract-price prints - the scheduled numbers the market trades\n\n"
        "3-10 events per stock (6 months has more room than 3 - two earnings prints, not "
        "just one, plus the full conference/product calendar), only events with a credible dated source from the last ~30 "
        "days of reporting or an official calendar. Never invent a date: if a real event has "
        "no firmer timing than a month, use that month's mid-point as the date and set "
        "precision 'month'. If timing is vaguer than a month or the event is stale rumor, "
        "omit it. Do NOT include broad macro events (Fed, CPI) here - company-relevant "
        "events only.\n"
        f"{feed.SOURCE_HINT} For China/HK-listed names in particular, actively search "
        "Chinese-language tech media (36kr.com, LatePost/晚点, Caixin, ijiwei.com) and "
        "analyst Poe Zhao's China tech coverage - product/event dates for China internet "
        "and China semi names often appear there before English-language press.\n\n"
        "Some stocks below carry a THIS WEEK'S FEED excerpt (Telegram channels, Gmail "
        "newsletters, X posts collected over the last ~7 days) - check it FIRST for dated "
        "upcoming events and watch items, then use web search to confirm dates and fill "
        "the rest. Feed-sourced events cite Telegram/Gmail/X as the source.\n\n"
        f"STOCKS:\n{listing}\n\n"
        "CRITICAL: reply with NOTHING but the JSON object.\n"
        'Output ONLY: {"TICKER_ID":{"events":[' + EVENT_SCHEMA + "]}}"
    )
    return ask_and_parse(prompt)


def research_macro(today, horizon):
    prompt = (
        f"Today is {today.isoformat()}. List the dated MACRO and INDUSTRY-WIDE events "
        f"between now and {horizon.isoformat()} that matter most for a portfolio of US "
        "mega-cap tech, global semiconductors/AI supply chain, China internet, and gold/"
        "jewelry stocks. Cover: FOMC decision dates, US CPI release dates, key China policy "
        "events (politburo/economic work meetings, stimulus decision windows), major "
        "industry conferences (e.g. Nvidia GTC events, CES if in window, major semi "
        "industry events), TSMC monthly revenue release cadence (flag once), and any "
        "US-China trade/export-control deadlines with a real date. 12-20 events, each with "
        "a credible source. Use type 'Macro' for economic/policy events and 'Industry' for "
        "conferences/industry datapoints. Never invent a date; precision 'month' with a "
        "mid-month placeholder date if only the month is known.\n\n"
        "CRITICAL: reply with NOTHING but the JSON object.\n"
        'Output ONLY: {"events":[' + EVENT_SCHEMA + "]}"
    )
    return ask_and_parse(prompt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", help="comma-separated ticker ids; default the key-name list")
    ap.add_argument("--force", action="store_true", help="ignore freshness TTL")
    args = ap.parse_args()

    universe = json.loads(UNIVERSE.read_text())
    labels = {m["id"]: m["label"] for g in universe["groups"] for m in g["members"]}

    todo_ids = [t.strip() for t in args.tickers.split(",")] if args.tickers else KEY_TICKERS
    today = datetime.date.today()
    horizon = today + datetime.timedelta(days=183)

    out = {"tickers": {}, "macro": {"events": [], "fetched": None}}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text())
            if isinstance(prev.get("tickers"), dict):
                out["tickers"] = prev["tickers"]
            if isinstance(prev.get("macro"), dict):
                out["macro"] = prev["macro"]
        except (json.JSONDecodeError, OSError):
            pass

    def fresh(block):
        if args.force or not block or not block.get("fetched"):
            return False
        try:
            age = (today - datetime.date.fromisoformat(block["fetched"])).days
        except ValueError:
            return False
        return age < FRESH_DAYS and bool(block.get("events"))

    targets = [(tid, labels.get(tid, tid)) for tid in todo_ids
               if not fresh(out["tickers"].get(tid))]
    print(f"{len(targets)} tickers due: {[t[0] for t in targets]}", flush=True)

    lock = threading.Lock()

    def save():
        # data/ is the source of truth; site/ and root are what the deployed
        # page and local preview actually fetch (same 3-way sync as
        # deep-financials.json)
        payload = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
        OUT.write_text(payload)
        (ROOT / "site" / "catalyst-calendar.json").write_text(payload)
        (ROOT / "catalyst-calendar.json").write_text(payload)

    def run_batch(batch):
        result = research_batch(batch, today, horizon)
        with lock:
            for tid, _ in batch:
                block = result.get(tid) if isinstance(result, dict) else None
                events = valid_events((block or {}).get("events"), today, horizon)
                if events:
                    out["tickers"][tid] = {"events": events, "fetched": today.isoformat()}
                    print(f"  {tid:8} {len(events)} events", flush=True)
                else:
                    print(f"  {tid:8} no events found - keeping any previous entry", flush=True)
            save()

    def run_macro():
        events = valid_events((research_macro(today, horizon) or {}).get("events"),
                              today, horizon)
        with lock:
            if events:
                out["macro"] = {"events": events, "fetched": today.isoformat()}
                print(f"  MACRO    {len(events)} events", flush=True)
            else:
                print("  MACRO    no events found - keeping previous", flush=True)
            save()

    jobs = [lambda b=targets[i:i + BATCH]: run_batch(b) for i in range(0, len(targets), BATCH)]
    if not fresh(out["macro"]):
        jobs.append(run_macro)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(lambda j: j(), jobs))

    n = sum(len(v.get("events", [])) for v in out["tickers"].values())
    print(f"catalyst_calendar: {len(out['tickers'])} tickers, {n} ticker events, "
          f"{len(out['macro'].get('events', []))} macro events -> {OUT}")


if __name__ == "__main__":
    main()
