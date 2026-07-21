"""Per-ticker news/catalyst tagging. Collects text from the available sources
(X digest, later Gmail + Telegram), asks Claude (subscription CLI, no API bill)
to map items to the universe tickers, and writes data/news.json which build.py
merges onto each instrument. Fundamentals move slowly; run this a few times/day.
"""
import os
import base64
import json
import pickle
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "universe.json"
OUT = ROOT / "data" / "news.json"
X_DIGEST = Path.home() / "x-reader" / "digest.json"
GMAIL_TOKEN = Path.home() / "bots" / "evening-brief" / "tokens" / "token_chelsfinnews.pkl"
TG_SCRAPER = Path.home() / "telegram-reader" / "scrape_channel.py"
TG_CHANNELS = ["tradehaven", "Fin_Watch", "tech"]


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
    items = []
    for ch in TG_CHANNELS:
        try:
            r = subprocess.run(["python3", str(TG_SCRAPER), ch, "1"],
                               capture_output=True, text=True, timeout=90)
            if r.returncode == 0 and r.stdout.strip():
                items.append(f"[Telegram @{ch}] {r.stdout[:2500]}")
        except Exception:
            pass
    return items


def ask_claude(prompt):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run(["claude", "-p", prompt], capture_output=True,
                           text=True, env=env, timeout=300)
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


def main():
    config = json.loads(CONFIG.read_text())
    tickers = {m["id"]: m["label"] for g in config["groups"] for m in g["members"]}
    x, gm, tg = collect_x(), collect_gmail(), collect_telegram()
    items = x + gm + tg
    print(f"sources: X={len(x)} Gmail={len(gm)} Telegram={len(tg)}")
    if not items:
        OUT.write_text("{}")
        print("no source items")
        return

    ticker_list = "\n".join(f"{tid} = {label}" for tid, label in tickers.items())
    prompt = (
        "You map market news to a fixed ticker list for a trading dashboard. Accuracy "
        "is critical.\nTICKERS (id = company):\n"
        f"{ticker_list}\n\n"
        "RULES:\n"
        "- Include a ticker ONLY if a NEWS ITEM below explicitly names it or its company. "
        "No inference, no guessing.\n"
        "- The line must be fully supported by the source text. Do NOT add numbers, dates, "
        "prices or claims that are not in the source. Never fabricate.\n"
        "- Keep the source tag in brackets exactly as given, e.g. [X @jukan05], [TG @tradehaven], [Email].\n"
        "- One concise line per ticker, <=140 chars. If you are unsure a ticker is really "
        "the subject, OMIT it.\n"
        "Output ONLY JSON: {\"TICKER_ID\": \"line [src]\"}. No prose.\n\n"
        "NEWS ITEMS:\n" + "\n".join(items)
    )
    result = parse_json(ask_claude(prompt))
    clean = {k: v for k, v in result.items() if k in tickers and isinstance(v, str) and v.strip()}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(clean, ensure_ascii=False, separators=(",", ":")))
    print(f"news: tagged {len(clean)} tickers -> {OUT}")
    for tid, line in clean.items():
        print(f"  {tid}: {line[:90]}")


if __name__ == "__main__":
    main()
