"""Curated (not exhaustive) list of NVIDIA's most significant partnerships
and collaborations announced in the current calendar year, for the NVDA
Ecosystem tab's hub-and-spoke map + detail table.

This is a periodic manual-refresh dataset, not part of the daily pipeline -
NVIDIA's partnership cadence is roughly monthly/quarterly (GTC announcements,
earnings-call disclosures), not daily, and the research bar here (real,
significant, well-sourced deals only - not vague mentions) benefits from a
careful pass rather than a cheap automated one. Re-run manually every few
months, or whenever a major new NVDA partnership breaks.

Run:  .venv/bin/python scripts/nvda_partnerships.py
"""
import datetime
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_PATHS = [
    ROOT / "data" / "nvda-partnerships.json",
    ROOT / "site" / "nvda-partnerships.json",
    ROOT / "nvda-partnerships.json",
]
CATEGORIES = ["Chip/Hardware", "Cloud/Hyperscaler", "AI Model/Software",
              "Enterprise/Vertical", "Sovereign AI/Government", "Telecom/Networking"]


def ask_claude(prompt, timeout=420):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run(["claude", "-p", prompt, "--allowedTools", "WebSearch"],
                            capture_output=True, text=True, env=env, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except subprocess.TimeoutExpired:
        return ""


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


def main():
    year = datetime.date.today().year
    prompt = (
        f"Find NVIDIA's most significant, widely-covered partnerships and "
        f"collaborations announced in calendar year {year}, across these "
        f"categories: {', '.join(CATEGORIES)}.\n\n"
        "For each, verify with real sources (NVIDIA's own newsroom/investor "
        "site preferred, corroborated by trade press) - do not invent or "
        "guess. If a deal is reported/rumored but not confirmed/signed, "
        "mark status as 'unconfirmed' and say so in the description rather "
        "than presenting it as done.\n\n"
        "Aim for a curated list of the ~15-20 most notable partnerships, "
        "not an exhaustive sweep of every minor mention.\n\n"
        "CRITICAL: reply with ONLY JSON, nothing else, in this exact shape:\n"
        '{"partnerships":[{"partner":"...","category":"...","date":"YYYY-MM-DD",'
        '"status":"confirmed or unconfirmed","description":"...","source":"..."}]}'
    )
    raw = ask_claude(prompt)
    parsed = parse_obj(raw)
    if not parsed and raw.strip():
        parsed = parse_obj(ask_claude(
            "Extract ONLY the JSON object from this text. Reply with ONLY the JSON.\n\n" + raw,
            timeout=120))

    partnerships = (parsed or {}).get("partnerships") or []
    out = {"fetched": datetime.date.today().isoformat(), "note":
           "Curated, not exhaustive - the most significant/widely-covered NVDA "
           "partnerships announced this calendar year.", "partnerships": partnerships}

    for path in OUT_PATHS:
        path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"nvda_partnerships: {len(partnerships)} partnerships -> {OUT_PATHS[0]}")


if __name__ == "__main__":
    main()
