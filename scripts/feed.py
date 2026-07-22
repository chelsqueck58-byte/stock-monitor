"""Shared helper: pull today's combined Telegram/Gmail/X feed (written once by
news.py at 07:30, data/feed-raw.txt) and filter to lines mentioning a given
ticker — so catalysts.py, earnings_research.py, and movements_research.py can
check what today's feeds already said about a name before spending a web
search on it, without each script doing its own independent X/Gmail/Telegram
pull. One fetch (news.py's), shared by every consumer.
"""
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEED_RAW = ROOT / "data" / "feed-raw.txt"
MAX_CHARS = 1500


@lru_cache(maxsize=1)
def _feed_lines():
    """Read+split the feed once per process, not once per ticker checked - a
    single script run can check 20-30 tickers against the same static file."""
    if not FEED_RAW.exists():
        return ()
    try:
        text = FEED_RAW.read_text(errors="ignore")
    except OSError:
        return ()
    return tuple(line for line in text.splitlines() if line.strip())


def relevant_excerpt(ticker_id, label, max_chars=MAX_CHARS):
    """Feed lines that mention this ticker's ID or full company name (word-
    boundary match on the ID and on the label as a whole phrase, not just its
    first word — avoids e.g. "Meta" or "Super" false-positiving on unrelated
    text). Cheap substring filtering, no LLM cost, so this stays small and
    targeted instead of stuffing the whole day's feed into every prompt."""
    lines = _feed_lines()
    if not lines:
        return ""

    base_id = ticker_id.split(".")[0]
    needles = [re.escape(base_id)]
    if label:
        needles.append(re.escape(label))
    pattern = re.compile(r"\b(?:" + "|".join(needles) + r")\b", re.IGNORECASE)

    hits = [line for line in lines if pattern.search(line)]
    if not hits:
        return ""
    return "\n".join(hits)[:max_chars]
