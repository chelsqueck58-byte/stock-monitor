"""Fill the 'reason' for every >=5% move in data/moves.json using Fable + web
search — grounded, blank if no source. Parallel + chunked: several stocks at once,
a focused call per ~12 moves, so each move gets real research (not 79 rushed in one
call). Writes moves.json incrementally under a lock, so progress is always saved.

Run:  .venv/bin/python scripts/movements_research.py
"""
import os
import re
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOVES = ROOT / "data" / "moves.json"
CHUNK = 12
WORKERS = 6
_lock = threading.Lock()


def ask_fable(prompt):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run(
            ["claude", "-p", prompt, "--model", "claude-fable-5",
             "--allowedTools", "WebSearch,WebFetch"],
            capture_output=True, text=True, env=env, timeout=700)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception as exc:
        return f"[err {exc}]"


def parse_array(text):
    if "```" in text:
        text = re.sub(r"```(json)?", "", text)
    s, e = text.find("["), text.rfind("]")
    if s < 0 or e < 0:
        return []
    try:
        return json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return []


def research_chunk(item):
    tid, label, moves = item
    prompt = (
        f"You research why {label} ({tid}) moved on specific days. For EACH dated move below, "
        "use web search to find the SPECIFIC reason it moved that day.\n"
        'RULES: the reason must be grounded in a real dated article. If no credible source, set '
        'reason to "". Never invent. reason <=140 chars. market_wide=true ONLY if it was a '
        "sector/index-wide move (e.g. broad China selloff), not company-specific. source = outlet.\n"
        'Output ONLY a JSON array: '
        '[{"date":"YYYY-MM-DD","reason":"...","source":"...","market_wide":false}]\n\n'
        "MOVES:\n" + "\n".join(f"{m['d']} {m['pct']:+}%" for m in moves)
    )
    researched = {r.get("date"): r for r in parse_array(ask_fable(prompt)) if isinstance(r, dict)}
    chunk_dates = {m["d"] for m in moves}
    with _lock:
        data = json.loads(MOVES.read_text())
        for m in data[tid]["moves"]:
            if m["d"] not in chunk_dates:
                continue
            r = researched.get(m["d"])
            # Mark checked regardless of outcome — an unsourced move stays blank
            # but is never re-searched (re-searching a dead end wastes credits).
            m["checked"] = True
            if r:
                m["reason"] = (r.get("reason") or "").strip() or None
                m["source"] = (r.get("source") or "").strip() or None
                if isinstance(r.get("market_wide"), bool):
                    m["market_wide"] = r["market_wide"]
        MOVES.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return tid, sum(1 for v in researched.values() if (v.get("reason") or "").strip())


def main(only=None):
    data = json.loads(MOVES.read_text())
    ids = only if only else list(data.keys())
    items = []
    for tid in ids:
        entry = data.get(tid)
        if not entry:
            continue
        todo = [m for m in entry["moves"] if not m.get("checked")]
        for i in range(0, len(todo), CHUNK):
            items.append((tid, entry["label"], todo[i:i + CHUNK]))
    print(f"{len(items)} chunks across {len(ids)} names, {WORKERS} in parallel")

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for tid, n in ex.map(research_chunk, items):
            done = sum(1 for m in json.loads(MOVES.read_text())[tid]["moves"] if m.get("reason"))
            print(f"  {tid:7} chunk +{n}")

    filled = sum(1 for v in json.loads(MOVES.read_text()).values() for m in v["moves"] if m.get("reason"))
    total = sum(len(v["moves"]) for v in json.loads(MOVES.read_text()).values())
    print(f"done: {filled}/{total} moves sourced")


if __name__ == "__main__":
    import sys
    scope = sys.argv[1].split(",") if len(sys.argv) > 1 else None
    main(scope)
