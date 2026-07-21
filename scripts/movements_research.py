"""Fill the 'reason' for each >=5% move in data/moves.json using the Fable model
+ web search (subscription CLI, no API bill). Grounded only — blank if no credible
source. Writes back incrementally so partial progress is saved.

Run:  .venv/bin/python scripts/movements_research.py
"""
import os
import re
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOVES = ROOT / "data" / "moves.json"


def ask_fable(prompt):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run(
            ["claude", "-p", prompt, "--model", "claude-fable-5",
             "--allowedTools", "WebSearch,WebFetch"],
            capture_output=True, text=True, env=env, timeout=600)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception as exc:
        print(f"[fable err] {exc}")
        return ""


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


def main():
    data = json.loads(MOVES.read_text())
    for tid, entry in data.items():
        todo = [m for m in entry["moves"] if not m.get("reason")]
        if not todo:
            continue
        prompt = (
            f"You research why a stock moved on specific days. Stock: {entry['label']} ({tid}).\n"
            "For EACH dated move below, use web search to find the specific reason it moved that day.\n"
            "RULES: the reason must be grounded in a real dated article. If you cannot find a "
            'credible source for a date, set its reason to "". Never invent. reason <=140 chars.\n'
            "market_wide=true ONLY if it was a sector/index-wide move (e.g. broad China selloff), "
            "not company-specific. source = outlet name (e.g. Bloomberg).\n"
            'Output ONLY a JSON array: '
            '[{"date":"YYYY-MM-DD","reason":"...","source":"...","market_wide":false}]\n\n'
            "MOVES:\n" + "\n".join(f"{m['d']} {m['pct']:+}%" for m in todo)
        )
        researched = {r.get("date"): r for r in parse_array(ask_fable(prompt)) if isinstance(r, dict)}
        for m in entry["moves"]:
            r = researched.get(m["d"])
            if r:
                m["reason"] = (r.get("reason") or "").strip() or None
                m["source"] = (r.get("source") or "").strip() or None
                if isinstance(r.get("market_wide"), bool):
                    m["market_wide"] = r["market_wide"]
        MOVES.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        done = sum(1 for m in entry["moves"] if m.get("reason"))
        print(f"  {tid:7} researched {done}/{len(entry['moves'])}")

    total = sum(len(v["moves"]) for v in data.values())
    filled = sum(1 for v in data.values() for m in v["moves"] if m.get("reason"))
    print(f"done: {filled}/{total} moves have a sourced reason")


if __name__ == "__main__":
    main()
