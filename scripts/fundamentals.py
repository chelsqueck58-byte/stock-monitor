"""Fundamental snapshot per instrument via Yahoo quoteSummary: growth, valuation,
estimate revisions, next earnings. Cached to data/fundamentals.json and merged by
build.py. Fundamentals move slowly — refresh daily, not every price build.

Run:  .venv/bin/python scripts/fundamentals.py
"""
import datetime
import json
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "universe.json"
OUT = ROOT / "data" / "fundamentals.json"
MODULES = "defaultKeyStatistics,earningsTrend,financialData,calendarEvents,earningsHistory,quoteType"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def session_with_crumb():
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get("https://fc.yahoo.com", timeout=10)
    except requests.RequestException:
        pass
    crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10).text
    if not crumb or "<" in crumb:
        raise RuntimeError("could not get Yahoo crumb")
    return s, crumb


def raw(node, *path):
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key, {})
    return node.get("raw") if isinstance(node, dict) else None


def fmt(node, *path):
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key, {})
    return node.get("fmt") if isinstance(node, dict) else None


def fetch(session, crumb, symbol):
    url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
    resp = session.get(url, params={"modules": MODULES, "crumb": crumb}, timeout=15)
    resp.raise_for_status()
    result = (resp.json().get("quoteSummary") or {}).get("result")
    if not result:
        return None
    d = result[0]
    ks, fd = d.get("defaultKeyStatistics", {}), d.get("financialData", {})
    trend = d.get("earningsTrend", {}).get("trend", [])
    cal = d.get("calendarEvents", {})
    ce = cal.get("earnings", {})
    dates = ce.get("earningsDate") or []
    # Yahoo's calendar field sometimes lags a quarter and still shows a date
    # that's already passed. Never surface a stale "upcoming" earnings date.
    today = datetime.date.today()
    next_earn = None
    for entry in dates:
        if not isinstance(entry, dict) or not entry.get("fmt"):
            continue
        try:
            if datetime.date.fromisoformat(entry["fmt"]) >= today:
                next_earn = entry["fmt"]
                break
        except ValueError:
            continue

    # Last 4 quarters beat/miss.
    hist = []
    for q in d.get("earningsHistory", {}).get("history", []):
        surp = raw(q, "surprisePercent")
        hist.append({
            "q": fmt(q, "quarter"),
            "actual": raw(q, "epsActual"),
            "est": raw(q, "epsEstimate"),
            "surprise": round(surp * 100, 1) if surp is not None else None,
        })

    # Try multiple keys for market cap (Yahoo uses different names across modules)
    mkt_cap = raw(ks, "marketCap") or raw(ks, "market_cap")
    if not mkt_cap and "quoteType" in d:
        mkt_cap = raw(d.get("quoteType", {}), "marketCap")
    fwd_pe = raw(ks, "forwardPE")

    return {
        "rev_growth": raw(fd, "revenueGrowth"),
        "eps_growth": raw(fd, "earningsGrowth"),
        "market_cap": mkt_cap,
        "fwd_pe": fwd_pe,
        "peg": raw(ks, "pegRatio"),
        "rev_up": raw(trend[0], "epsRevisions", "upLast30days") if trend else None,
        "rev_down": raw(trend[0], "epsRevisions", "downLast30days") if trend else None,
        "next_earnings": next_earn,
        "ex_div": fmt(cal, "exDividendDate"),
        "curr_est": raw(trend[0], "earningsEstimate", "avg") if trend else None,
        "earnings_history": hist[:4],
    }


def main():
    config = json.loads(CONFIG.read_text())
    session, crumb = session_with_crumb()

    # Merge into whatever's already there — a transient Yahoo failure for one
    # ticker used to wipe its entry (P/E, next earnings, everything) until the
    # next successful run, since `out` started empty every time. Fall back to
    # the last-known-good snapshot instead, same pattern as build.py's price fetch.
    out = {}
    if OUT.exists():
        try:
            out = json.loads(OUT.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    ok, missing, stale = 0, [], []
    for group in config["groups"]:
        for member in group["members"]:
            symbol = member.get("yahoo")
            try:
                snap = fetch(session, crumb, symbol)
            except Exception:
                snap = None
            if snap and any(v is not None for v in snap.values()):
                out[member["id"]] = snap
                ok += 1
            elif member["id"] in out:
                stale.append(member["id"])  # kept last-known-good
            else:
                missing.append(member["id"])  # never fetched successfully at all
            time.sleep(0.4)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print(f"fundamentals: {ok} ok, {len(stale)} reused last-good, {len(missing)} never fetched -> {OUT}")
    if stale:
        print("  stale (kept prior):", ", ".join(stale))
    if missing:
        print("  missing:", ", ".join(missing))


if __name__ == "__main__":
    main()
