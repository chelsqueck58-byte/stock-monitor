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
    # Same delivery-report exclusion as build.py's copy of this heuristic -
    # "Q2 2026 deliveries ~62,682 units..." (TSLA/XPeng/NIO/Li Auto) is a
    # different event from a quarterly earnings report despite the Q[1-4]
    # opener, and must not satisfy an earnings-window match.
    words6 = re.findall(r"[a-zA-Z0-9]+", reason or "")[:6]
    head6 = " ".join(words6).lower()
    head3 = " ".join(words6[:3]).lower()
    has_ref = bool(re.search(r"\bq[1-4]\b", head3)) or "earnings" in head3
    if not has_ref:
        return False
    if "deliver" in head6 and "earnings" not in head6:
        return False
    return True


def already_covered(tid, anchor_iso, moves_by_id):
    """True if an already-researched earnings move exists within a wide
    window around the anchor date - wide enough to cover both anchor flavors
    (an estimated report date, or a raw quarter-end awaiting its ~0-90d
    reporting lag)."""
    entry = moves_by_id.get(tid)
    if not entry:
        return False
    anchor = datetime.date.fromisoformat(anchor_iso)
    window_start = anchor - datetime.timedelta(days=15)
    window_end = anchor + datetime.timedelta(days=95)
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
    tid, label, anchor_iso, qkey, surprise = item
    q_label = f"the quarter ending closest to {qkey}" if qkey else f"its most recent quarter (expected around {anchor_iso})"
    surprise_note = f" (EPS surprise was previously estimated at {surprise:+.1f}% vs consensus)" if surprise is not None else ""
    prompt = (
        f"Find the exact date {label} ({tid}) most recently reported quarterly earnings "
        f"results - {q_label}{surprise_note}. This company's data feed suggests they "
        f"reported on or close to {anchor_iso}; confirm the real date via web search "
        "rather than assuming that estimate is exact. Then find how the stock reacted "
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
    return tid, label, anchor_iso, qkey, parsed


def latest_reported_anchor(f, today):
    """The best available signal for 'what quarter did this company most
    recently report, and around what date' - NOT trusted from earnings_history
    alone, since Yahoo's quoteSummary earningsHistory module lags: it can take
    days-to-weeks after a real report before that quarter's actual EPS shows
    up there. Confirmed live on Tencent, which reported Q2 2026 on 2026-08-12
    (per user) - earnings_history still listed Q1 (2026-03-31) as the latest
    ACTUALIZED entry, so the quarter-list approach missed it completely.

    Better signal: next_earnings itself. Once Yahoo's calendar has advanced
    next_earnings to a date >1 quarter out, that update only happens AFTER a
    report lands - so the true report already occurred, roughly one quarterly
    cadence (~91d) before next_earnings. Falls back to the earnings_history
    quarter-end when next_earnings isn't usefully in the future (e.g. missing,
    or still <=35d out - that one belongs to the *upcoming* catalysts view,
    not this past-report gap-fill).
    """
    next_earn = f.get("next_earnings")
    if next_earn:
        try:
            ne = datetime.date.fromisoformat(next_earn)
            days_out = (ne - today).days
            if days_out > 35:
                approx_report = ne - datetime.timedelta(days=91)
                if approx_report <= today:
                    return approx_report, "~" + approx_report.isoformat()
        except ValueError:
            pass
    hist = f.get("earnings_history") or []
    reported = [q for q in hist if q.get("actual") is not None and q.get("q")]
    if not reported:
        return None, None
    latest = max(reported, key=lambda q: q["q"])
    try:
        qend = datetime.date.fromisoformat(latest["q"])
    except ValueError:
        return None, None
    return qend, latest["q"]


def main():
    fund = json.loads(FUND.read_text())
    moves = json.loads(MOVES.read_text()) if MOVES.exists() else {}
    universe = json.loads(UNIVERSE.read_text())
    labels = {m["id"]: m["label"] for g in universe["groups"] for m in g["members"]}

    out = json.loads(OUT.read_text()) if OUT.exists() else {}
    today = datetime.date.today()

    todo = []
    for tid, f in fund.items():
        anchor_date, qkey = latest_reported_anchor(f, today)
        if anchor_date is None:
            continue
        anchor_iso = anchor_date.isoformat()

        cached = out.get(tid)
        if cached and cached.get("anchor") == anchor_iso:
            fetched = cached.get("fetched")
            if fetched and (today - datetime.date.fromisoformat(fetched)).days < FRESH_DAYS:
                continue

        if already_covered(tid, anchor_iso, moves):
            out[tid] = {"anchor": anchor_iso, "quarter": qkey, "report_date": None,
                        "reaction_pct": None, "reason": None, "source": None,
                        "covered_by_move": True, "fetched": today.isoformat()}
            continue

        surprise = None
        hist = f.get("earnings_history") or []
        matching = [q for q in hist if q.get("q") == qkey]
        if matching:
            surprise = matching[0].get("surprise")
        todo.append((tid, labels.get(tid, tid), anchor_iso, qkey, surprise))

    print(f"{len(todo)} tickers need report-date research (of {len(fund)} total)")

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for tid, label, anchor_iso, qkey, parsed in ex.map(research_one, todo):
            entry = {"anchor": anchor_iso, "quarter": qkey, "covered_by_move": False,
                     "fetched": today.isoformat()}
            if parsed:
                rd = (parsed.get("report_date") or "").strip()
                entry["report_date"] = rd or None
                entry["reaction_pct"] = parsed.get("reaction_pct")
                entry["reason"] = (parsed.get("reason") or "").strip() or None
                entry["source"] = (parsed.get("source") or "").strip() or None
            else:
                entry.update(report_date=None, reaction_pct=None, reason=None, source=None)
            out[tid] = entry
            print(f"  {tid:7} {qkey or anchor_iso} -> {entry.get('report_date')}: {entry.get('reason')}")
            OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    found = sum(1 for v in out.values() if v.get("report_date"))
    print(f"earnings_dates: {found}/{len(out)} with a found report date -> {OUT}")


if __name__ == "__main__":
    main()
