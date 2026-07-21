"""Extract UPCOMING dated catalysts (next ~30 days) per ticker from the news
sources — conferences, product launches, investor days, regulatory dates. Earnings
are handled separately. Writes data/events.json {id: [{date, event}]}. Grounded to
the source text; blank if nothing dated.
"""
import json
from pathlib import Path

from news import collect_x, collect_gmail, collect_telegram, ask_claude, parse_json

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "universe.json"
OUT = ROOT / "data" / "events.json"


def main():
    tickers = {m["id"]: m["label"] for g in json.loads(CONFIG.read_text())["groups"] for m in g["members"]}
    items = collect_x() + collect_gmail() + collect_telegram()
    if not items:
        OUT.write_text("{}")
        print("no source items")
        return

    ticker_list = "\n".join(f"{tid} = {label}" for tid, label in tickers.items())
    prompt = (
        "Extract UPCOMING, SPECIFICALLY-DATED catalysts for the tickers below from the news items. "
        "Only include an event if the news gives a concrete future date (e.g. a product launch, "
        "conference, investor day, regulatory deadline). SKIP earnings dates (handled elsewhere). "
        "Do not invent dates. Ground everything in the source.\n"
        f"TICKERS:\n{ticker_list}\n\n"
        'Output ONLY JSON: {"TICKER_ID": [{"date":"YYYY-MM-DD","event":"short desc"}]}. '
        "Omit tickers with no dated event. No prose.\n\nNEWS:\n" + "\n".join(items)
    )
    result = parse_json(ask_claude(prompt))
    clean = {}
    for tid, evs in result.items():
        if tid in tickers and isinstance(evs, list):
            good = [e for e in evs if isinstance(e, dict) and e.get("date") and e.get("event")]
            if good:
                clean[tid] = good[:4]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(clean, ensure_ascii=False, separators=(",", ":")))
    print(f"events: {len(clean)} tickers with dated catalysts -> {OUT}")
    for tid, evs in clean.items():
        print(f"  {tid}: {evs}")


if __name__ == "__main__":
    main()
