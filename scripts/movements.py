"""Detect significant (>=5%) daily moves over the last year for every name, and
classify each as market-wide (SPY/QQQ moved the same way) or stock-specific.
MERGES into data/moves.json — existing moves keep their researched reason/source/
checked status; only genuinely new moves (new >=5% days) are added unresearched.
Run every ~2 weeks; movements_research.py then only has to research what's new.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "universe.json"
OUT = ROOT / "data" / "moves.json"
BIG = 5.0        # research threshold — every move this size or bigger
CAP = 999        # comprehensive: keep them all
MARKET_MOVE = 1.5  # if the index moved >= this same-direction, tag market-wide


def daily_moves(bars):
    out = []
    for i in range(1, len(bars)):
        c, pc = bars[i]["c"], bars[i - 1]["c"]
        if c and pc:
            out.append({"d": bars[i]["d"], "pct": round((c / pc - 1) * 100, 1), "px": round(c, 2)})
    return out


def main():
    data = json.loads((ROOT / "site" / "data.json").read_text())
    by_id = {x["id"]: x for x in data["instruments"]}

    existing = {}
    if OUT.exists():
        try:
            existing = json.loads(OUT.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # index moves by date, to flag market-wide days
    index_by_date = {}
    for idx in ("SPY", "QQQ"):
        if idx in by_id:
            for m in daily_moves(by_id[idx]["bars"]):
                index_by_date.setdefault(m["d"], []).append(m["pct"])

    out = {}
    new_count = 0
    for inst in data["instruments"]:
        if inst["group"] == "Index ETF":
            continue
        detected = [m for m in daily_moves(inst["bars"]) if abs(m["pct"]) >= BIG]
        prior_by_date = {m["d"]: m for m in existing.get(inst["id"], {}).get("moves", [])}

        moves = []
        for m in detected:
            if m["d"] in prior_by_date:
                moves.append(prior_by_date[m["d"]])  # keep researched reason/source/checked
                continue
            idx_moves = index_by_date.get(m["d"], [])
            m["market_wide"] = any((ip > 0) == (m["pct"] > 0) and abs(ip) >= MARKET_MOVE for ip in idx_moves)
            m["reason"] = None
            m["source"] = None
            m["checked"] = False
            moves.append(m)
            new_count += 1

        moves.sort(key=lambda m: m["d"], reverse=True)
        moves = moves[:CAP]
        if moves:
            out[inst["id"]] = {"label": inst["label"], "moves": moves}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    total = sum(len(v["moves"]) for v in out.values())
    print(f"moves: {len(out)} names, {total} total (>= {BIG}%), {new_count} new unresearched -> {OUT}")


if __name__ == "__main__":
    main()
