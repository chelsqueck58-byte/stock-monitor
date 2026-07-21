"""Per-ticker news/catalyst tagging. Collects text from the available sources
(X digest, later Gmail + Telegram), asks Claude (subscription CLI, no API bill)
to map items to the universe tickers, and writes data/news.json which build.py
merges onto each instrument. Fundamentals move slowly; run this a few times/day.
"""
import os
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "universe.json"
OUT = ROOT / "data" / "news.json"
X_DIGEST = Path.home() / "x-reader" / "digest.json"


def collect_x():
    if not X_DIGEST.exists():
        return []
    try:
        posts = json.loads(X_DIGEST.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return [f"[X @{p['handle']}] {' '.join(p['text'].split())}"
            for p in posts if "error" not in p][:120]


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
    items = collect_x()
    if not items:
        OUT.write_text("{}")
        print("no source items")
        return

    ticker_list = "\n".join(f"{tid} = {label}" for tid, label in tickers.items())
    prompt = (
        "You map market news to a fixed ticker list. TICKERS (id = company):\n"
        f"{ticker_list}\n\n"
        "From the NEWS ITEMS below, for each ticker that is clearly the SUBJECT of an "
        "item (direct mention or unambiguous reference), write ONE concise catalyst/focus "
        "line (<=120 chars) with the source handle in brackets. Skip tickers with no real "
        "news. Ignore vague macro. Output ONLY JSON: {\"TICKER_ID\": \"line [src]\", ...}. "
        "No prose.\n\nNEWS ITEMS:\n" + "\n".join(items)
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
