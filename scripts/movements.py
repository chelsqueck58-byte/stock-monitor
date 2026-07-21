"""Detect significant (>=5%) daily moves over the last year for every name, and
classify each as market-wide (SPY/QQQ moved the same way) or stock-specific.
Writes data/moves.json — the reason field is left blank here and filled by the
Fable research pass (research grounded to real sources; blank if none found).
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

    # index moves by date, to flag market-wide days
    index_by_date = {}
    for idx in ("SPY", "QQQ"):
        if idx in by_id:
            for m in daily_moves(by_id[idx]["bars"]):
                index_by_date.setdefault(m["d"], []).append(m["pct"])

    out = {}
    for inst in data["instruments"]:
        if inst["group"] == "Index ETF":
            continue
        moves = [m for m in daily_moves(inst["bars"]) if abs(m["pct"]) >= BIG]
        moves.sort(key=lambda m: -abs(m["pct"]))
        moves = moves[:CAP]
        moves.sort(key=lambda m: m["d"], reverse=True)
        for m in moves:
            idx_moves = index_by_date.get(m["d"], [])
            same_way = any((ip > 0) == (m["pct"] > 0) and abs(ip) >= MARKET_MOVE for ip in idx_moves)
            m["market_wide"] = same_way
            m["reason"] = None   # filled by the Fable research pass
            m["source"] = None
        if moves:
            out[inst["id"]] = {"label": inst["label"], "moves": moves}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    total = sum(len(v["moves"]) for v in out.values())
    print(f"moves: {len(out)} names, {total} moves >= {BIG}% (cap {CAP}/name) -> {OUT}")


if __name__ == "__main__":
    main()
