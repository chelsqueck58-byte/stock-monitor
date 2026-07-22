"""Broader forward catalysts, NOT limited to earnings or to what a news feed
happens to mention with an exact date. One web-search-backed line per priority
ticker covering the real near-term story: product launches, buybacks/capital
returns, regulatory/legal decisions, capacity/supply-chain moves, guidance,
M&A — whatever is actually the market's live focus for that name right now.

Scoped to priority tickers (active idea, earnings soon, or IV rank >=80) to keep
cost bounded — same targeting logic as news.py's web-search gap-fill. Writes
data/catalysts.json {id: {"line": "... [src]", "fetched": "YYYY-MM-DD"}};
build.py unwraps to a plain string as inst.catalyst. Grounded only — omitted if
no credible source.

FRESHNESS TTL: a ticker researched within FRESH_DAYS is skipped, not re-searched
— a priority name that hasn't changed doesn't need daily re-spend.
"""
import os
import re
import json
import datetime
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "universe.json"
DATA_JSON = ROOT / "site" / "data.json"
OUT = ROOT / "data" / "catalysts.json"
BATCH = 6
FRESH_DAYS = 4


def priority_tickers(labels):
    if not DATA_JSON.exists():
        return list(labels.items())[:15]
    try:
        data = json.loads(DATA_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    today = datetime.date.today()
    out = []
    for inst in data.get("instruments", []):
        if inst["id"] not in labels:
            continue
        idea = inst.get("idea")
        iv = inst.get("iv") or {}
        f = inst.get("fund") or {}
        earn_soon = False
        if f.get("next_earnings"):
            try:
                days = (datetime.date.fromisoformat(f["next_earnings"]) - today).days
                earn_soon = 0 <= days <= 21
            except ValueError:
                pass
        if idea or iv.get("iv_rank", 0) >= 80 or earn_soon:
            out.append((inst["id"], labels[inst["id"]]))
    return out[:20]


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


def main(force=None):
    config = json.loads(CONFIG.read_text())
    labels = {m["id"]: m["label"] for g in config["groups"] for m in g["members"]}
    candidates = priority_tickers(labels)
    if force:
        have = {t[0] for t in candidates}
        candidates += [(t, labels[t]) for t in force if t in labels and t not in have]

    out = {}
    if OUT.exists():
        try:
            out = json.loads(OUT.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    today = datetime.date.today()
    targets, skipped_fresh = [], 0
    for tid, label in candidates:
        cached = out.get(tid)
        if cached and cached.get("fetched") and not (force and tid in force):
            age = (today - datetime.date.fromisoformat(cached["fetched"])).days
            if age < FRESH_DAYS:
                skipped_fresh += 1
                continue
        targets.append((tid, label))
    print(f"{len(targets)} tickers due (skipped {skipped_fresh} still-fresh): {[t[0] for t in targets]}")

    for i in range(0, len(targets), BATCH):
        batch = targets[i:i + BATCH]
        listing = "\n".join(f"- {tid} ({label})" for tid, label in batch)
        prompt = (
            "For EACH stock below, use web search to find its single most important NEAR-TERM "
            "catalyst or storyline right now — over the next 1-3 months. This is NOT limited to "
            "earnings: consider product launches, capital returns (buybacks/dividends), regulatory "
            "or legal decisions, capacity/supply-chain moves, M&A, guidance changes, major analyst "
            "calls, or competitive dynamics — whatever is genuinely the market's live focus for "
            "that name. Prioritize sources from the last 7 days — older is only acceptable if it's "
            "still the single most relevant near-term catalyst (e.g. a still-pending earnings date). "
            "One line per stock, <=170 chars, grounded in a real source, ending with the source in "
            "brackets e.g. [Reuters]. If nothing credible, omit that stock.\n\n"
            f"STOCKS:\n{listing}\n\n"
            "CRITICAL: after searching, your reply must be NOTHING but the JSON object — no "
            "explanation before or after.\n"
            'Output ONLY: {"TICKER_ID": "line [src]"}'
        )
        raw = ask_claude(prompt)
        result = parse_json(raw)
        if not result and raw.strip():
            fixup = ('Extract ONLY the JSON object from this text, format {"TICKER_ID":"line [src]"}. '
                      "Reply with ONLY the JSON, nothing else.\n\n" + raw)
            result = parse_json(ask_claude(fixup))
        for tid, _ in batch:
            line = result.get(tid)
            if isinstance(line, str) and line.strip() and "[" in line:
                clean = " ".join(line.split())[:220]
                out[tid] = {"line": clean, "fetched": today.isoformat()}
                print(f"  {tid}: {clean[:90]}")
        OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    print(f"catalysts: {len(out)} names total -> {OUT}")


if __name__ == "__main__":
    import sys
    forced = sys.argv[1].split(",") if len(sys.argv) > 1 else None
    main(forced)
