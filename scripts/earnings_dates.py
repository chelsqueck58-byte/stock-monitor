"""Fill in the exact report DATE (not just quarter-end) for each ticker's most
recently reported quarter, via web search — grounded, blank if no source.

Why this exists: Yahoo's free quoteSummary earningsHistory module only gives a
quarter-END date (e.g. "2026-06-30"), never the day the company actually
announced. movements.py/movements_research.py already capture the true date
for any earnings reaction that moved the stock >=5% (read off the real price
bar). This script closes the remaining gap: a quarter that already reported
but whose reaction was UNDER 5% has no date anywhere in the pipeline, so it
silently disappears from both the upcoming (>30d away, wrong bucket) and past
(no >=5% move to log) catalyst views. Confirmed live on Tencent's Q2 2026
beat (actual 7.433 vs est 7.235, +2.7%) - too small to trip movements.py, so
nothing recorded its report date.

Skips a ticker's latest quarter if a move within +/-4 days of any already-
researched earnings-tagged move exists for that ticker in moves.json (already
covered, no need to re-search). Writes data/earnings_dates.json:
{tid: {"quarter": "...", "report_date": "...", "reaction_pct": float|null,
       "reason": "...", "source": "...", "fetched": "YYYY-MM-DD"}}

Run:  .venv/bin/python scripts/earnings_dates.py
"""
import datetime
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FUND = ROOT / "data" / "fundamentals.json"
MOVES = ROOT / "data" / "moves.json"
UNIVERSE = ROOT / "config" / "universe.json"
OUT = ROOT / "data" / "earnings_dates.json"
WORKERS = 6
FRESH_DAYS = 3  # a ticker's latest reported quarter doesn't change often


def _opens_with_earnings_ref(reason):
    words = re.findall(r"[a-zA-Z0-9]+", reason or "")[:3]
    head = " ".join(words).lower()
    return bool(re.search(r"\bq[1-4]\b", head)) or "earnings" in head


def already_covered(tid, quarter_end, moves_by_id):
    """True if an already-researched earnings move exists near a plausible
    report window for this quarter (quarter-end + 0-70 days, the normal
    reporting lag) - within +/-4 days of any such move counts as covered."""
    entry = moves_by_id.get(tid)
    if not entry:
        return False
    qend = datetime.date.fromisoformat(quarter_end)
    window_start, window_end = qend, qend + datetime.timedelta(days=75)
    for mv in entry.get("moves", []):
        if not mv.get("reason") or not _opens_with_earnings_ref(mv["reason"]):
            continue
        try:
            d = datetime.date.fromisoformat(mv["d"])
        except ValueError:
            continue
        if window_start <= d <= window_end:
            return True
    return False


def ask_claude(prompt):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run(
            ["claude", "-p", prompt, "--allowedTools", "WebSearch,WebFetch"],
            capture_output=True, text=True, env=env, timeout=300)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception as exc:
        return f"[err {exc}]"


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


def research_one(item):
    tid, label, quarter_end, surprise = item
    q_label = f"quarter ended {quarter_end}"
    surprise_note = f" (EPS surprise was {surprise:+.1f}% vs estimate)" if surprise is not None else ""
    prompt = (
        f"Find the exact date {label} ({tid}) reported its earnings results for the "
        f"{q_label}{surprise_note}. Use web search. Then find how the stock reacted "
        "on that trading day or the next one (percent move) and a one-line reason "
        "investors gave for that reaction (or note if the move was minor/in-line).\n"
        'RULES: report_date must be a real date you found, format "YYYY-MM-DD". If you '
        'cannot find the exact report date, set report_date to "". reason <=140 chars, '
        "grounded in a real source; never invent. If the stock barely moved, say so "
        "plainly (e.g. \"shares little changed on in-line results\"). source = outlet name.\n"
        "CRITICAL: your reply must contain NOTHING but the JSON object - no prose, no "
        "markdown fencing, before or after.\n"
        'Format: {"report_date":"YYYY-MM-DD","reaction_pct":0.0,"reason":"...","source":"..."}'
    )
    raw = ask_claude(prompt)
    parsed = parse_obj(raw)
    if not parsed and raw.strip():
        fixup = (
            "Extract ONLY the JSON object from this text, matching "
            '{"report_date":"YYYY-MM-DD","reaction_pct":0.0,"reason":"...","source":"..."}. '
            "Reply with ONLY the JSON object.\n\n" + raw
        )
        parsed = parse_obj(ask_claude(fixup))
    return tid, label, quarter_end, parsed


def main():
    fund = json.loads(FUND.read_text())
    moves = json.loads(MOVES.read_text()) if MOVES.exists() else {}
    universe = json.loads(UNIVERSE.read_text())
    labels = {m["id"]: m["label"] for g in universe["groups"] for m in g["members"]}

    out = json.loads(OUT.read_text()) if OUT.exists() else {}
    today = datetime.date.today()

    todo = []
    for tid, f in fund.items():
        hist = f.get("earnings_history") or []
        reported = [q for q in hist if q.get("actual") is not None and q.get("q")]
        if not reported:
            continue
        latest = max(reported, key=lambda q: q["q"])
        qend = latest["q"]

        cached = out.get(tid)
        if cached and cached.get("quarter") == qend:
            fetched = cached.get("fetched")
            if fetched and (today - datetime.date.fromisoformat(fetched)).days < FRESH_DAYS:
                continue

        if already_covered(tid, qend, moves):
            out[tid] = {"quarter": qend, "report_date": None, "reaction_pct": None,
                        "reason": None, "source": None, "covered_by_move": True,
                        "fetched": today.isoformat()}
            continue

        todo.append((tid, labels.get(tid, tid), qend, latest.get("surprise")))

    print(f"{len(todo)} tickers need report-date research (of {len(fund)} total)")

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for tid, label, qend, parsed in ex.map(research_one, todo):
            entry = {"quarter": qend, "covered_by_move": False, "fetched": today.isoformat()}
            if parsed:
                rd = (parsed.get("report_date") or "").strip()
                entry["report_date"] = rd or None
                entry["reaction_pct"] = parsed.get("reaction_pct")
                entry["reason"] = (parsed.get("reason") or "").strip() or None
                entry["source"] = (parsed.get("source") or "").strip() or None
            else:
                entry.update(report_date=None, reaction_pct=None, reason=None, source=None)
            out[tid] = entry
            print(f"  {tid:7} {qend} -> {entry.get('report_date')}: {entry.get('reason')}")
            OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    found = sum(1 for v in out.values() if v.get("report_date"))
    print(f"earnings_dates: {found}/{len(out)} with a found report date -> {OUT}")


if __name__ == "__main__":
    main()
