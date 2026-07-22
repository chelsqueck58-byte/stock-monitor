"""Per-instrument analytics. Entry/exit logic implements the user's explicit rule
table (5 entry types, 5 exit conditions) — see trend_signal()/entry_exit_signal().
Also computes S/R levels (swing-pivot clusters, for the chart), RSI, CTA regime.
"""
import datetime


def rsi_series(closes, period=14):
    """Wilder's RSI as a full series, aligned to `closes` (None until warmed up)."""
    n = len(closes)
    if n < period + 1:
        return [None] * n
    out = [None] * period
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0)
        losses += max(-change, 0)
    avg_gain, avg_loss = gains / period, losses / period
    out.append(100.0 if avg_loss == 0 else round(100 - 100 / (1 + avg_gain / avg_loss), 1))
    for i in range(period + 1, n):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0)) / period
        out.append(100.0 if avg_loss == 0 else round(100 - 100 / (1 + avg_gain / avg_loss), 1))
    return out


def rsi(closes, period=14):
    """Latest RSI value."""
    series = rsi_series(closes, period)
    return series[-1] if series else None


def weekly_closes(bars):
    """Aggregate real Mon-Fri calendar weeks into a weekly close series (last
    trading day's close of each ISO week) — correct around holidays, unlike a
    fixed 5-trading-day step."""
    weeks = {}
    order = []
    for bar in bars:
        if bar["close"] is None:
            continue
        key = datetime.date.fromisoformat(bar["date"]).isocalendar()[:2]  # (iso_year, iso_week)
        if key not in weeks:
            order.append(key)
        weeks[key] = bar["close"]
    return [weeks[k] for k in order]


def weekly_rsi(bars, period=14):
    """Latest weekly RSI(14) from true calendar-week closes."""
    return rsi(weekly_closes(bars), period)


def sma_at(closes, period, offset=0):
    """SMA of `period` closes ending `offset` bars from the latest."""
    end = len(closes) - offset
    return sum(closes[end - period:end]) / period if end >= period else None


def crossed_above_ma(closes, period):
    """True on the exact day the close crosses from below its own SMA to above it."""
    if len(closes) < period + 2:
        return False
    today_ma, yday_ma = sma_at(closes, period, 0), sma_at(closes, period, 1)
    if today_ma is None or yday_ma is None:
        return False
    return closes[-2] < yday_ma and closes[-1] >= today_ma


def crossed_below_ma(closes, period):
    if len(closes) < period + 2:
        return False
    today_ma, yday_ma = sma_at(closes, period, 0), sma_at(closes, period, 1)
    if today_ma is None or yday_ma is None:
        return False
    return closes[-2] >= yday_ma and closes[-1] < today_ma


def prior_extreme(bars, period, field, agg):
    """Highest/lowest `field` over the `period` bars strictly BEFORE the given
    end (used to test a cross against a level that existed before today)."""
    window = bars[-period - 1:-1]
    vals = [b[field] for b in window if b.get(field) is not None]
    return agg(vals) if vals else None


def crossed_above_prior_high(bars, period, field="close"):
    """True the day price first closes above the prior `period`-bar high — a
    fresh breakout, not every day afterward while price stays elevated."""
    if len(bars) < period + 2:
        return False
    today_hi = prior_extreme(bars, period, "high", max)
    yday_hi = prior_extreme(bars[:-1], period, "high", max)
    if today_hi is None or yday_hi is None:
        return False
    return bars[-2][field] <= yday_hi and bars[-1][field] > today_hi


def at_or_above_rolling_high(bars, period, near_pct):
    """3M-resistance exit condition: today's close IS the trailing-`period` high,
    or within `near_pct` of it (a touch/tag of the 3-month high — take-profit)."""
    window = bars[-period:]
    highs = [b["high"] for b in window if b.get("high") is not None]
    if not highs or bars[-1]["close"] is None:
        return False
    hi = max(highs)
    return bars[-1]["close"] >= hi or abs(bars[-1]["close"] / hi - 1) * 100 <= near_pct


def at_or_below_rolling_low(bars, period, near_pct=0.0):
    """3M-support condition (also used for the exit's 63d-low break)."""
    window = bars[-period:]
    lows = [b["low"] for b in window if b.get("low") is not None]
    if not lows or bars[-1]["close"] is None:
        return False
    lo = min(lows)
    return bars[-1]["close"] <= lo or abs(bars[-1]["close"] / lo - 1) * 100 <= near_pct


def volume_ratio(bars):
    """Latest day's volume vs the trailing-20-day average."""
    vols = [b.get("volume") or 0 for b in bars]
    if len(vols) < 21 or sum(vols[-21:-1]) == 0:
        return None
    avg20 = sum(vols[-21:-1]) / 20
    return vols[-1] / avg20 if avg20 else None


def bullish_divergence(bars, closes, rsis, window=63, pivot=5):
    """Classic bullish divergence: the two most recent swing lows in `window`
    bars show price making a LOWER low while RSI makes a HIGHER low."""
    segment = bars[-window:]
    seg_closes = closes[-window:]
    seg_rsi = rsis[-window:]
    lows = []
    for i in range(pivot, len(segment) - pivot):
        span = segment[i - pivot:i + pivot + 1]
        lo = segment[i].get("low")
        if lo is not None and lo == min(b["low"] for b in span if b.get("low") is not None):
            lows.append(i)
    if len(lows) < 2:
        return False
    i2, i1 = lows[-1], lows[-2]  # i2 = more recent
    p2, p1 = segment[i2]["low"], segment[i1]["low"]
    r2, r1 = seg_rsi[i2], seg_rsi[i1]
    if None in (p1, p2, r1, r2):
        return False
    return p2 < p1 and r2 > r1


def trend_signal(bars, settings):
    """Base state shared by every entry/exit rule: price vs 20/50DMA."""
    closes = [bar["close"] for bar in bars]
    last = closes[-1]
    s20, s50 = sma_at(closes, 20), sma_at(closes, 50)
    above50 = s50 is not None and last > s50
    trend = "up" if above50 else ("down" if s50 is not None else "flat")
    return {
        "sma20": round(s20, 4) if s20 else None,
        "sma50": round(s50, 4) if s50 else None,
        "trend": trend,
        "above50": above50,
    }


ENTRY_LABELS = {
    "high_conviction": "High-conviction",
    "breakout": "Breakout",
    "oversold_dip": "Oversold dip",
    "pullback_bounce": "Pullback bounce",
    "divergence": "Divergence",
}
EXIT_LABELS = {
    "below_50ma": "Closed below 50MA",
    "broke_3m_support": "Broke below 3M support (63d low)",
    "at_3m_resistance": "At/above 3M resistance (63d high)",
    "overbought": "RSI > 70",
    "false_breakout": "Broke 1M resistance on weak volume",
}


def entry_exit_signal(bars, settings, sma20, sma50, above50):
    """The user's rule table, implemented literally. Returns which entry TYPES
    fired (a stock can match several at once) and which exit CONDITIONS fired."""
    closes = [bar["close"] for bar in bars]
    last = closes[-1]
    rsis = rsi_series(closes)
    rsi_val = rsis[-1] if rsis else None
    wk_rsi = weekly_rsi(bars)
    near_pct = settings["proximity_alert_pct"]  # 1.5%, reused per the locked decision

    near_3m_support = at_or_below_rolling_low(bars, 63, near_pct)
    vol_ratio = volume_ratio(bars)

    entries = []
    if (above50 and rsi_val is not None and near_3m_support
            and rsi_val < 40 and (wk_rsi is None or wk_rsi <= 70)):
        entries.append("high_conviction")
    if (above50 and vol_ratio is not None and vol_ratio > 1
            and crossed_above_prior_high(bars, 20)):
        entries.append("breakout")
    if above50 and rsi_val is not None and rsi_val < 30:
        entries.append("oversold_dip")
    if (above50 and rsi_val is not None and 40 <= rsi_val <= 55
            and crossed_above_ma(closes, 20)):
        entries.append("pullback_bounce")
    if at_or_below_rolling_low(bars, 63, near_pct) and bullish_divergence(bars, closes, rsis):
        entries.append("divergence")

    exits = []
    if crossed_below_ma(closes, 50):
        exits.append("below_50ma")
    if at_or_below_rolling_low(bars, 63):
        exits.append("broke_3m_support")
    if at_or_above_rolling_high(bars, 63, near_pct):
        exits.append("at_3m_resistance")
    if rsi_val is not None and rsi_val > 70:
        exits.append("overbought")
    if (vol_ratio is not None and vol_ratio < 1
            and crossed_above_prior_high(bars, 20)):
        exits.append("false_breakout")

    return entries, exits, rsi_val, wk_rsi


def donchian_signal(bars, period):
    """CTA-style trend-follower state from a Donchian breakout of the prior `period`.

    'long' = close breaks the prior N-day high · 'short' = breaks the N-day low ·
    'neutral' = inside the channel. This is where systematic trend money flips.
    """
    if len(bars) < period + 1:
        return "neutral"
    prior = bars[-period - 1:-1]
    highs = [bar["high"] for bar in prior if bar["high"] is not None]
    lows = [bar["low"] for bar in prior if bar["low"] is not None]
    if not highs or not lows:
        return "neutral"
    last = bars[-1]["close"]
    if last >= max(highs):
        return "long"
    if last <= min(lows):
        return "short"
    return "neutral"


def swing_points(bars, window):
    """Pivot highs and lows: extremes within +/- window bars."""
    highs = []
    lows = []
    for index in range(window, len(bars) - window):
        span = bars[index - window: index + window + 1]
        bar = bars[index]
        if bar["high"] is not None and bar["high"] == max(
            candidate["high"] for candidate in span if candidate["high"] is not None
        ):
            highs.append({"date": bar["date"], "price": bar["high"]})
        if bar["low"] is not None and bar["low"] == min(
            candidate["low"] for candidate in span if candidate["low"] is not None
        ):
            lows.append({"date": bar["date"], "price": bar["low"]})
    return highs, lows


def cluster(points, tolerance_pct):
    """Merge nearby pivots into single levels, weighted by touch count."""
    if not points:
        return []

    ordered = sorted(points, key=lambda point: point["price"])
    clusters = [[ordered[0]]]
    for point in ordered[1:]:
        anchor = clusters[-1][0]["price"]
        if anchor and abs(point["price"] - anchor) / anchor * 100 <= tolerance_pct:
            clusters[-1].append(point)
        else:
            clusters.append([point])

    levels = []
    for group in clusters:
        prices = [point["price"] for point in group]
        levels.append({
            "price": round(sum(prices) / len(prices), 4),
            "touches": len(group),
            "last_touch": max(point["date"] for point in group),
        })
    return sorted(levels, key=lambda level: level["price"])


def compute_levels(bars, settings):
    """Support/resistance per lookback window (swing-pivot clusters, for the
    chart lines) — a different, more nuanced measure than the plain rolling
    63d/20d high/low used by the entry/exit rules above."""
    if not bars:
        return {}

    last_close = bars[-1]["close"]
    window = settings["swing_window"]
    tolerance = settings["level_cluster_pct"]
    out = {}

    for label, length in settings["lookbacks"].items():
        segment = bars[-length:] if len(bars) >= length else bars
        highs, lows = swing_points(segment, window)
        all_levels = cluster(highs + lows, tolerance)

        support = [level for level in all_levels if level["price"] < last_close]
        resistance = [level for level in all_levels if level["price"] >= last_close]

        out[label] = {
            "support": sorted(support, key=lambda level: -level["price"])[:3],
            "resistance": sorted(resistance, key=lambda level: level["price"])[:3],
        }

    return out


def proximity_flags(last_close, levels, threshold_pct):
    """Levels the price is currently sitting on, within threshold (for alerts)."""
    flags = []
    for window, data in levels.items():
        for kind in ("support", "resistance"):
            for level in data[kind]:
                if not level["price"]:
                    continue
                distance = (last_close - level["price"]) / level["price"] * 100
                if abs(distance) <= threshold_pct:
                    flags.append({
                        "window": window,
                        "kind": kind,
                        "price": level["price"],
                        "distance_pct": round(distance, 2),
                        "touches": level["touches"],
                    })
    return flags


def summarise(bars, settings):
    """Everything the site needs for one instrument."""
    last_close = bars[-1]["close"]
    signal = trend_signal(bars, settings)
    levels = compute_levels(bars, settings)
    entries, exits, rsi_val, wk_rsi = entry_exit_signal(
        bars, settings, signal["sma20"], signal["sma50"], signal["above50"])

    ytd_open = next(
        (bar["close"] for bar in bars if bar["date"][:4] == bars[-1]["date"][:4]),
        None,
    )
    prior_close = bars[-2]["close"] if len(bars) > 1 else None

    # Realized volatility (annualised) + expected ~1-month move, from daily returns.
    # Fallback vol figure for names with no options IV (KR/SG); every name gets one.
    closes = [bar["close"] for bar in bars]
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]
    ret_window = rets[-21:] if len(rets) >= 21 else rets
    rvol = rmove = None
    if len(ret_window) >= 5:
        mean = sum(ret_window) / len(ret_window)
        dstd = (sum((r - mean) ** 2 for r in ret_window) / len(ret_window)) ** 0.5
        rvol = round(dstd * (252 ** 0.5) * 100, 1)
        rmove = round(dstd * (21 ** 0.5) * 100, 1)

    flags = proximity_flags(last_close, levels, settings["proximity_alert_pct"])

    idea = None
    if entries:
        idea = {"kind": "enter", "types": [ENTRY_LABELS[e] for e in entries]}
    elif exits:
        idea = {"kind": "take_profit", "types": [EXIT_LABELS[e] for e in exits]}

    return {
        "last_close": round(last_close, 4),
        "last_date": bars[-1]["date"],
        "change_pct": round((last_close / prior_close - 1) * 100, 2) if prior_close else None,
        "ytd_pct": round((last_close / ytd_open - 1) * 100, 2) if ytd_open else None,
        "sma20": signal["sma20"],
        "sma50": signal["sma50"],
        "rsi": rsi_val,
        "weekly_rsi": wk_rsi,
        "trend": signal["trend"],
        "rvol": rvol,
        "rmove": rmove,
        "cta": donchian_signal(bars, settings.get("donchian_period", 20)),
        "idea": idea,
        "exit_triggers": [EXIT_LABELS[e] for e in exits] if exits else [],
        "levels": levels,
        "flags": flags,
        "bars": [
            {"d": bar["date"], "o": bar["open"], "h": bar["high"],
             "l": bar["low"], "c": bar["close"], "v": bar.get("volume")}
            for bar in bars[-260:]
        ],
    }
