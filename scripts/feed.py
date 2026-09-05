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

# Shared across catalysts.py / earnings_research.py / movements_research.py so
# all three suggest the same source pool without three copies drifting apart.
SOURCE_HINT = (
    " Beyond mainstream English-language outlets, also consider: official filings/IR pages "
    "(sec.gov 8-K/10-Q, company investor-relations sites) as the most authoritative when "
    "available; Chinese-language tech/financial media - 36kr.com, LatePost (latepost.com / "
    "晚点), Caixin, ijiwei.com/JW Insights for semis, plus analyst Poe Zhao's China tech "
    "coverage - for China AI/internet names; Chinese-language financial media (qq.com, stcn.com, aastocks.com, sina.com.cn, "
    "toutiao.com, ainvest.com, futunn.com, news.cn, 10jqka.com, 163.com, eastmoney.com, "
    "stockstar.com) for China/HK-listed names; major wire/financial media (reuters.com, "
    "bloomberg.com, wsj.com, marketwatch.com, nasdaq.com, scmp.com for HK/China regional); "
    "analyst/data aggregators (tipranks.com, marketscreener.com, seekingalpha.com, "
    "investing.com, benzinga.com, zacks.com, fool.com, 247wallst.com); and sector-specific "
    "outlets (techcrunch.com, electrek.co for EV/tech) or secondary sites (biggo.com finance, "
    "moomoo, marketbeat.com, quiverquant.com, tradingkey.com) where relevant — weight official "
    "filings and established outlets above single-author blogs when accounts differ."
)


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


@lru_cache(maxsize=1)
def _week_lines():
    """All lines from the rolling 7-day archive (newest day first), falling
    back to today's feed-raw.txt when the archive doesn't exist yet."""
    archive = ROOT / "data" / "feed-archive"
    files = sorted(archive.glob("feed-*.txt"), reverse=True) if archive.exists() else []
    if not files:
        return _feed_lines()
    lines, seen = [], set()
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            if line.strip() and line not in seen:
                seen.add(line)
                lines.append(line)
    return tuple(lines)


def relevant_excerpt_week(ticker_id, label, max_chars=3000):
    """relevant_excerpt over the last ~7 days of feed instead of just today -
    for consumers with a longer horizon (catalyst_calendar.py), where e.g. a
    conference mention from Tuesday's X posts still matters on Thursday."""
    lines = _week_lines()
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
