"""For names with earnings within ~35 days, research (web search, grounded): the
market's focus for the upcoming print and how the stock typically reacts.
Writes data/earnings.json {id: {"line": "... [src]", "fetched": "YYYY-MM-DD"}};
build.py unwraps to a plain string as inst.earn. Grounded only — blank if no
source.

Efficiency:
- Batches multiple tickers into ONE Claude call (was one full CLI subprocess per
  ticker — ~20-30 separate processes for one refresh).
- MERGES into existing earnings.json instead of overwriting from empty (used to
  silently discard prior results every run).
- FRESHNESS TTL: a ticker already researched within FRESH_DAYS is skipped, not
  re-searched. This is the daily-refresh script that was re-spending tokens on
  the same ~20-30 tickers every single day for no reason — this is the fix.
"""
import os
import re
import json
import datetime
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FUND = ROOT / "data" / "fundamentals.json"
CONFIG = ROOT / "config" / "universe.json"
OUT = ROOT / "data" / "earnings.json"
WINDOW = 35
BATCH = 8
FRESH_DAYS = 4


def ask_claude(prompt, timeout=500):
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


def clean_line(line):
    line = re.split(r"\s*Sources?:", line, flags=re.I)[0].strip()
    return re.sub(r"\((https?://[^)]+)\)", "", line).strip()


def main():
    fund = json.loads(FUND.read_text())
    labels = {m["id"]: m["label"] for g in json.loads(CONFIG.read_text())["groups"] for m in g["members"]}
    today = datetime.date.today()

    out = {}
    if OUT.exists():
        try:
            out = json.loads(OUT.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    due = []
    skipped_fresh = 0
    for tid, f in fund.items():
        ne = f.get("next_earnings")
        if not ne:
            continue
        try:
            days = (datetime.date.fromisoformat(ne) - today).days
        except ValueError:
            continue
        if not (0 <= days <= WINDOW):
            continue
        cached = out.get(tid)
        if cached and cached.get("fetched"):
            age = (today - datetime.date.fromisoformat(cached["fetched"])).days
            if age < FRESH_DAYS:
                skipped_fresh += 1
                continue
        due.append((tid, labels.get(tid, tid), ne))

    print(f"{len(due)} tickers due (skipped {skipped_fresh} still-fresh)")

    for i in range(0, len(due), BATCH):
        batch = due[i:i + BATCH]
        listing = "\n".join(f"- {tid} ({label}) reports {ne}" for tid, label, ne in batch)
        prompt = (
            "For EACH stock below, use web search. In ONE line per stock (<=170 chars): what is "
            "the market's KEY focus/expectation for its upcoming earnings print, and how has the "
            "stock typically REACTED to its recent earnings (e.g. jumped/fell X%)? Prioritize sources "
            "from the last 7 days for the focus half; the reaction history can cite older quarters "
            "since that's inherently historical. End each line with the source outlet in brackets, "
            "e.g. [Bloomberg]. If you cannot find credible info for a stock, omit it entirely.\n\n"
            f"STOCKS:\n{listing}\n\n"
            'Output ONLY JSON: {"TICKER_ID": "line [src]"}. No prose.'
        )
        result = parse_json(ask_claude(prompt))
        for tid, _, _ in batch:
            line = result.get(tid)
            if isinstance(line, str) and line.strip():
                line = clean_line(" ".join(line.split()))
                if line and "[" in line:
                    out[tid] = {"line": line[:200], "fetched": today.isoformat()}
                    print(f"  {tid}: {line[:90]}")
        OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    # Prune entries for tickers whose earnings date has passed or moved outside
    # the window — otherwise a stale "upcoming" focus line lingers forever.
    still_due = set()
    for tid, f in fund.items():
        ne = f.get("next_earnings")
        if not ne:
            continue
        try:
            days = (datetime.date.fromisoformat(ne) - today).days
        except ValueError:
            continue
        if 0 <= days <= WINDOW:
            still_due.add(tid)
    dropped = [tid for tid in out if tid not in still_due]
    for tid in dropped:
        del out[tid]
    if dropped:
        OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
        print(f"pruned {len(dropped)} stale entries: {dropped}")

    print(f"earnings focus: {len(out)} names total -> {OUT}")


if __name__ == "__main__":
    main()
