"""Lean per-instrument analytics: one EMA entry, 50DMA, S/R, trend, CTA breakout."""


def ema(closes, period):
    """Exponential moving average, latest value."""
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    value = sum(closes[:period]) / period
    for close in closes[period:]:
        value = close * k + value * (1 - k)
    return round(value, 4)


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


def trend_signal(bars, settings):
    """One EMA-based entry: long above a rising entry-EMA, enter on pullbacks to it."""
    closes = [bar["close"] for bar in bars]
    last = closes[-1]
    period = settings.get("entry_ema", 21)
    lookback = settings.get("ma_slope_lookback", 10)
    pullback = settings.get("entry_pullback_pct", 2.0)

    ema_now = ema(closes, period)
    slope = None
    if ema_now is not None and len(closes) > period + lookback:
        ema_prev = ema(closes[:-lookback], period)
        if ema_prev:
            slope = round((ema_now / ema_prev - 1) * 100, 2)

    above = ema_now is not None and last > ema_now
    rising = slope is None or slope > 0
    if above and rising:
        trend = "up"
    elif ema_now is not None and last < ema_now:
        trend = "down"
    else:
        trend = "flat"

    entry = trend == "up" and ema_now is not None and abs((last / ema_now - 1) * 100) <= pullback
    return {"ema": ema_now, "trend": trend, "entry_setup": entry}


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
    """Support/resistance per lookback window, split around the last close."""
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
    """Everything the site needs for one instrument — lean."""
    last_close = bars[-1]["close"]
    closes = [bar["close"] for bar in bars]
    signal = trend_signal(bars, settings)
    sma50 = round(sum(closes[-50:]) / 50, 4) if len(closes) >= 50 else None
    levels = compute_levels(bars, settings)

    # 21EMA is never a signal in isolation — qualify it with the broader trend
    # (50SMA structure), the higher timeframe (100SMA regime), and volume.
    sma100 = sum(closes[-100:]) / 100 if len(closes) >= 100 else None
    sma50_prev = sum(closes[-60:-10]) / 50 if len(closes) >= 60 else None
    # 5-day vs 20-day average volume — robust to the current incomplete session
    # (a single partial last bar would otherwise read as fake-low volume).
    vols = [bar.get("volume") or 0 for bar in bars]
    avg_vol = sum(vols[-20:]) / 20 if len(vols) >= 20 and sum(vols[-20:]) else 0
    vol5 = sum(vols[-5:]) / 5 if len(vols) >= 5 else 0
    vol_ratio = round(vol5 / avg_vol, 2) if avg_vol else None
    above50 = sma50 is not None and last_close > sma50
    sma50_up = sma50 is not None and sma50_prev is not None and sma50 > sma50_prev
    htf_up = sma100 is not None and last_close > sma100
    entry_quality = None
    if signal["entry_setup"]:
        entry_quality = "strong" if (above50 and sma50_up and htf_up) else "weak"

    ytd_open = next(
        (bar["close"] for bar in bars if bar["date"][:4] == bars[-1]["date"][:4]),
        None,
    )
    prior_close = bars[-2]["close"] if len(bars) > 1 else None

    return {
        "last_close": round(last_close, 4),
        "last_date": bars[-1]["date"],
        "change_pct": round((last_close / prior_close - 1) * 100, 2) if prior_close else None,
        "ytd_pct": round((last_close / ytd_open - 1) * 100, 2) if ytd_open else None,
        "ema21": signal["ema"],
        "sma50": sma50,
        "trend": signal["trend"],
        "entry_setup": signal["entry_setup"],
        "entry_quality": entry_quality,
        "above50": above50,
        "sma50_up": sma50_up,
        "htf_up": htf_up,
        "vol_ratio": vol_ratio,
        "cta": donchian_signal(bars, settings.get("donchian_period", 20)),
        "levels": levels,
        "flags": proximity_flags(last_close, levels, settings["proximity_alert_pct"]),
        "bars": [
            {"d": bar["date"], "o": bar["open"], "h": bar["high"],
             "l": bar["low"], "c": bar["close"], "v": bar.get("volume")}
            for bar in bars[-260:]
        ],
    }
