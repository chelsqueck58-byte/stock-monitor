"""Per-ticker news/catalyst tagging + upcoming dated events, in ONE pass.

Efficiency: fetches X/Gmail/Telegram ONCE (previously news.py and events.py each
fetched independently — double Gmail/Telegram calls for no reason) and makes ONE
Claude call that both tags catalysts AND extracts dated events. Uses the session's
default model (Sonnet) — no hardcoded Fable calls here.

Also does TARGETED web search: only for "priority" tickers (an entry/take-profit
idea, or earnings within 10 days, or IV rank >= 80) that the feeds didn't already
cover. Searching all 50 tickers daily would burn credits for little gain; these
are the names where a catalyst actually matters right now.

Writes data/news.json {id: "line [src]"} and data/events.json {id: [{date,event}]}.
Grounded only — never invented; omitted if unsupported.
"""
import os
import base64
import datetime
import json
import pickle
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "universe.json"
DATA_JSON = ROOT / "site" / "data.json"
NEWS_OUT = ROOT / "data" / "news.json"
EVENTS_OUT = ROOT / "data" / "events.json"
X_DIGEST = Path.home() / "x-reader" / "digest.json"
GMAIL_TOKEN = Path.home() / "bots" / "evening-brief" / "tokens" / "token_chelsfinnews.pkl"
TG_SCRAPER = Path.home() / "telegram-reader" / "scrape_incremental.py"
TG_STATE = ROOT / "data" / "telegram-state.json"
TG_CHANNELS = ["tradehaven", "Fin_Watch", "tech", "infinityhedge"]
TELEGRAM_RAW_OUT = ROOT / "data" / "telegram-raw.txt"
FEED_RAW_OUT = ROOT / "data" / "feed-raw.txt"
EARNINGS_SOON_DAYS = 10


def collect_x():
    if not X_DIGEST.exists():
        return []
    try:
        posts = json.loads(X_DIGEST.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return [f"[X @{p['handle']}] {' '.join(p['text'].split())}"
            for p in posts if "error" not in p][:80]


def _email_body(payload):
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data).decode("utf-8", "ignore") if data else ""
    return "".join(_email_body(p) for p in payload.get("parts", []))


def collect_gmail():
    try:
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build as gbuild
        creds = pickle.load(open(GMAIL_TOKEN, "rb"))
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        svc = gbuild("gmail", "v1", credentials=creds)
        msgs = svc.users().messages().list(
            userId="me", q="newer_than:1d", maxResults=15).execute().get("messages", [])
        items = []
        for m in msgs:
            d = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
            hdr = {h["name"].lower(): h["value"] for h in d["payload"].get("headers", [])}
            body = " ".join(_email_body(d["payload"]).split())
            if body:
                items.append(f"[Email: {hdr.get('subject', '')[:80]}] {body[:1400]}")
        return items
    except Exception as exc:
        print(f"[gmail collect failed] {exc}")
        return []


def collect_telegram():
    """Incremental: only posts newer than the last check (any run, morning or
    evening) come back — state is a shared file keyed by channel -> last post
    ID, also written to by the evening market-brief run so neither pass
    reprocesses what the other already saw."""
    items = []
    for ch in TG_CHANNELS:
        try:
            r = subprocess.run(["python3", str(TG_SCRAPER), ch, str(TG_STATE), "2"],
                               capture_output=True, text=True, timeout=90)
            if r.returncode == 0 and r.stdout.strip():
                items.append(f"[Telegram @{ch}] {r.stdout[:2500]}")
        except Exception:
            pass
    return items


def ask_claude(prompt, web_search=False, timeout=300):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    cmd = ["claude", "-p", prompt]
    if web_search:
        cmd += ["--allowedTools", "WebSearch"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def parse_json(text):
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1].removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}


def priority_tickers(tickers):
    """Names worth an extra (costly) web search: an active idea, earnings soon,
    or elevated IV. Keeps web search targeted instead of blind-searching all 50."""
    if not DATA_JSON.exists():
        return []
    try:
        data = json.loads(DATA_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    today = datetime.date.today()
    out = []
    for inst in data.get("instruments", []):
        if inst["id"] not in tickers:
            continue
        idea = inst.get("idea")
        iv = inst.get("iv") or {}
        earn_soon = False
        next_earn = (inst.get("fund") or {}).get("next_earnings")
        if next_earn:
            try:
                days = (datetime.date.fromisoformat(next_earn) - today).days
                earn_soon = 0 <= days <= EARNINGS_SOON_DAYS
            except ValueError:
                pass
        if idea or iv.get("iv_rank", 0) >= 80 or earn_soon:
            out.append(inst["id"])
    return out[:15]  # hard cap — bounds the cost regardless of how many qualify


def main():
    config = json.loads(CONFIG.read_text())
    tickers = {m["id"]: m["label"] for g in config["groups"] for m in g["members"]}
    x, gm, tg = collect_x(), collect_gmail(), collect_telegram()
    items = x + gm + tg
    print(f"sources: X={len(x)} Gmail={len(gm)} Telegram={len(tg)}")

    # Persist the raw Telegram scrape so the 09:00 market brief can reuse it
    # instead of re-scraping the same 4 channels ~90 minutes later for near-
    # identical content — one scrape per day, shared across consumers.
    TELEGRAM_RAW_OUT.parent.mkdir(parents=True, exist_ok=True)
    TELEGRAM_RAW_OUT.write_text("\n\n".join(tg))

    # Also persist the FULL combined feed (X+Gmail+Telegram) so catalysts.py /
    # earnings_research.py / movements_research.py can check it for a ticker
    # before spending a web search — one fetch, shared across every consumer
    # that wants to know what today's feeds already said about a name.
    FEED_RAW_OUT.write_text("\n\n".join(items))

    ticker_list = "\n".join(f"{tid} = {label}" for tid, label in tickers.items())
    priority = priority_tickers(tickers)
    priority_list = ", ".join(f"{t} ({tickers[t]})" for t in priority) or "(none)"

    prompt = (
        "You produce two things for a trading dashboard from the news items below: "
        "(1) per-ticker catalyst tags, (2) upcoming dated events. Accuracy is critical — "
        "never fabricate.\n\nTICKERS (id = company):\n" + ticker_list + "\n\n"
        "PART 1 — news tags:\n"
        "Include a ticker ONLY if a NEWS ITEM explicitly names it or its company. No inference. "
        "The line must be fully supported by the source text — no added numbers/dates/claims. "
        "Keep the source tag in brackets, e.g. [X @jukan05], [TG @tradehaven], [Email]. "
        "One line per ticker, <=140 chars.\n\n"
        "PART 2 — dated events:\n"
        "Extract UPCOMING, SPECIFICALLY-DATED catalysts (product launch, conference, investor day, "
        "regulatory deadline) with a concrete future date in the news. SKIP earnings dates. Do not "
        "invent dates.\n\n"
        "PART 3 — targeted web search (use the WebSearch tool):\n"
        f"For these PRIORITY tickers only: {priority_list}\n"
        "If a priority ticker has NO news tag from Part 1, do ONE web search for its most recent "
        "(<=48h) company-specific news and add a grounded tag if you find one. Do NOT search for "
        "every ticker — only priority ones with no existing coverage. If nothing credible, skip it.\n\n"
        'Output ONLY this JSON: {"news": {"TICKER_ID": "line [src]"}, '
        '"events": {"TICKER_ID": [{"date":"YYYY-MM-DD","event":"short desc"}]}}. '
        "Omit tickers with nothing to report in either part. No prose.\n\n"
        "NEWS ITEMS:\n" + "\n".join(items)
    )
    result = parse_json(ask_claude(prompt, web_search=True, timeout=420))

    news = result.get("news", {}) if isinstance(result.get("news"), dict) else {}
    events = result.get("events", {}) if isinstance(result.get("events"), dict) else {}

    # news.json is intentionally NOT merged - it's "what's notable today", and
    # a stale old tag lingering forever would be worse than resetting daily.
    clean_news = {k: v for k, v in news.items() if k in tickers and isinstance(v, str) and v.strip()}

    # events.json IS merged. A dated event only ever gets extracted from
    # whatever's in today's narrow feed window (X 24h / Gmail 1d / Telegram
    # incremental-since-last-check) - since Telegram no longer re-shows a post
    # once it's been seen, an event mentioned once would otherwise vanish from
    # this file the very next day even though it's still weeks away. Keep any
    # previously-found event whose date hasn't passed yet; add newly-found ones.
    today_iso = datetime.date.today().isoformat()
    existing_events = {}
    if EVENTS_OUT.exists():
        try:
            existing_events = json.loads(EVENTS_OUT.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    clean_events = {}
    for tid in tickers:
        found = events.get(tid)
        new_evs = [e for e in found if isinstance(e, dict) and e.get("date") and e.get("event")] if isinstance(found, list) else []
        old_evs = [e for e in existing_events.get(tid, []) if e.get("date", "") >= today_iso]
        seen, combined = set(), []
        for e in old_evs + new_evs:
            key = (e.get("date"), e.get("event"))
            if key not in seen:
                seen.add(key)
                combined.append(e)
        if combined:
            clean_events[tid] = combined[:4]

    NEWS_OUT.parent.mkdir(parents=True, exist_ok=True)
    NEWS_OUT.write_text(json.dumps(clean_news, ensure_ascii=False, separators=(",", ":")))
    EVENTS_OUT.write_text(json.dumps(clean_events, ensure_ascii=False, separators=(",", ":")))

    print(f"news: {len(clean_news)} tickers tagged (priority searched: {priority}) -> {NEWS_OUT}")
    for tid, line in clean_news.items():
        print(f"  {tid}: {line[:90]}")
    print(f"events: {len(clean_events)} tickers with dated catalysts -> {EVENTS_OUT}")


if __name__ == "__main__":
    main()
