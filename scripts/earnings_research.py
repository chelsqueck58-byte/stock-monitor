"""For names with earnings within ~35 days, research (Fable + web, grounded):
the market's focus for the upcoming print, how the stock typically reacts, and
last-quarter guidance. Writes data/earnings.json {id: "line [src]"} which build.py
merges as inst.earn. Grounded only — blank if no source.
"""
import os
import json
import datetime
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FUND = ROOT / "data" / "fundamentals.json"
CONFIG = ROOT / "config" / "universe.json"
OUT = ROOT / "data" / "earnings.json"
WINDOW = 35


def ask_fable(prompt):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run(
            ["claude", "-p", prompt, "--model", "claude-fable-5",
             "--allowedTools", "WebSearch,WebFetch"],
            capture_output=True, text=True, env=env, timeout=500)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def main():
    fund = json.loads(FUND.read_text())
    labels = {m["id"]: m["label"] for g in json.loads(CONFIG.read_text())["groups"] for m in g["members"]}
    today = datetime.date.today()
    out = {}
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
        label = labels.get(tid, tid)
        prompt = (
            f"{label} ({tid}) reports earnings on {ne}. Use web search. In ONE line (<=170 chars): "
            "what is the market's KEY focus/expectation for THIS upcoming print, and how has the stock "
            "typically REACTED to its recent earnings (e.g. 'jumped/fell X%')? Ground it in real, recent "
            "sources. If you cannot find credible info, reply with exactly: NONE. "
            "End the line with the source outlet in brackets, e.g. [Bloomberg]. No preamble."
        )
        line = " ".join(ask_fable(prompt).split())
        if line and line.upper() != "NONE" and "[" in line:
            out[tid] = line[:220]
            print(f"  {tid}: {line[:90]}")
        else:
            print(f"  {tid}: (no source)")
        OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    print(f"earnings focus: {len(out)} names -> {OUT}")


if __name__ == "__main__":
    main()
