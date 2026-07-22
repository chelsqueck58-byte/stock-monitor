"""For names with earnings within ~35 days, research (web search, grounded):
the market's focus for the upcoming print — forward-looking ONLY.
Writes data/earnings.json {id: {"focus": "... [src]", "fetched": "YYYY-MM-DD"}};
build.py unwraps to inst.earn. Grounded only — blank if no source.

Previously this ALSO asked the model to recall "how has the stock typically
reacted to recent earnings" — dropped 2026-07-23. That's now computed for
free in build.py by looking up the ticker's own researched move history in
data/moves.json (real dated moves with grounded reasons, not an LLM's recall
of history) instead of spending a web search on something we usually already
have a better, dated, sourced answer for.

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

import feed

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
        # A cached entry still holding the pre-2026-07-23 "line" key was
        # researched under the OLD prompt (forward focus blended with past-
        # earnings reaction) - force a re-research under the new forward-only
        # prompt regardless of freshness, or the stale blended content would
        # otherwise sit there for up to FRESH_DAYS more, unchanged.
        stale_schema = cached and "line" in cached and "focus" not in cached
        if cached and cached.get("fetched") and not stale_schema:
            age = (today - datetime.date.fromisoformat(cached["fetched"])).days
            if age < FRESH_DAYS:
                skipped_fresh += 1
                continue
        due.append((tid, labels.get(tid, tid), ne))

    print(f"{len(due)} tickers due (skipped {skipped_fresh} still-fresh)")

    for i in range(0, len(due), BATCH):
        batch = due[i:i + BATCH]
        blocks = []
        for tid, label, ne in batch:
            block = f"- {tid} ({label}) reports {ne}"
            excerpt = feed.relevant_excerpt(tid, label)
            if excerpt:
                block += "\n  TODAY'S FEED (Telegram/Gmail/X) mentioning this name:\n  " + excerpt.replace("\n", "\n  ")
            blocks.append(block)
        listing = "\n".join(blocks)
        prompt = (
            "For EACH stock below, in ONE line (<=170 chars): what is the market's KEY focus/"
            "expectation for its UPCOMING earnings print — what specifically will investors be "
            "watching for? Forward-looking only, do not discuss past quarters. Some stocks below "
            "have a TODAY'S FEED excerpt (Telegram/Gmail/X already collected today) — check it "
            "FIRST for earnings-preview chatter; use it if credible (still attribute a source/"
            "outlet if the excerpt names one), otherwise use web search. Prioritize sources from "
            f"the last 7 days.{feed.SOURCE_HINT} "
            "End each line with the source outlet in brackets, e.g. [Bloomberg] or [Telegram] "
            "if from the feed. If you cannot find credible info for a stock from either the feed or "
            "a search, omit it entirely.\n\n"
            f"STOCKS:\n{listing}\n\n"
            'Output ONLY JSON: {"TICKER_ID": "line [src]"}. No prose.'
        )
        result = parse_json(ask_claude(prompt))
        for tid, _, _ in batch:
            line = result.get(tid)
            if isinstance(line, str) and line.strip():
                line = clean_line(" ".join(line.split()))
                if line and "[" in line:
                    out[tid] = {"focus": line[:200], "fetched": today.isoformat()}
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
