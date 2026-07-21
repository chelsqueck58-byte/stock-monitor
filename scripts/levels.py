"""Per-instrument analytics: 50>200 swing trend, 20/50DMA pullback entry,
1m/3m resistance targets, S/R levels, RSI, CTA breakout."""


def rsi(closes, period=14):
    """Wilder's RSI, latest value."""
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0)
        losses += max(-change, 0)
    avg_gain, avg_loss = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0)) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 1)


def entry_target(bars, levels, sma20, sma50, zone, last_close):
    """Entry = the pullback support zone (the 20/50DMA being bought into, bracketed
    with the nearest horizontal support). Exit = nearest 1-month or 3-month
    resistance above (per the user's rule). Stops are the trader's own — none here."""
    sup3 = [s["price"] for s in levels.get("3m", {}).get("support", [])]
    nearest_sup = max((p for p in sup3 if p < last_close), default=None)
    ma_zone = (sma20 if zone == "20DMA" else sma50) or last_close

    zone_low = min(ma_zone, nearest_sup) if nearest_sup else round(ma_zone * 0.985, 2)
    zone_high = max(ma_zone, last_close)
    if zone_low >= zone_high:
        zone_low = round(zone_high * 0.98, 2)

    res = sorted({p["price"] for p in levels.get("3m", {}).get("resistance", [])} |
                 {p["price"] for p in levels.get("1m", {}).get("resistance", [])})
    res_above = [r for r in res if r > last_close]
    hi63 = max((b["high"] for b in bars[-63:] if b["high"]), default=None)
    t1 = res_above[0] if res_above else (hi63 if hi63 and hi63 > last_close else None)
    entry_mid = (zone_low + zone_high) / 2
    reward = round((t1 / entry_mid - 1) * 100, 1) if t1 else None
    return {
        "entry": [round(zone_low, 2), round(zone_high, 2)],
        "t1": round(t1, 2) if t1 else None,
        "reward_pct": reward,
    }


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


def sma_at(closes, period, offset=0):
    """SMA of `period` closes ending `offset` bars from the latest."""
    end = len(closes) - offset
    return sum(closes[end - period:end]) / period if end >= period else None


def trend_signal(bars, settings):
    """Swing-long framework: medium-term uptrend (price > 50 & 200DMA, 50 > 200,
    both rising) + a pullback into the 20DMA (shallow) or 50DMA (deeper) zone."""
    closes = [bar["close"] for bar in bars]
    last = closes[-1]
    s20, s50, s200 = sma_at(closes, 20), sma_at(closes, 50), sma_at(closes, 200)
    s50_prev, s200_prev = sma_at(closes, 50, 10), sma_at(closes, 200, 20)
    s50_up = s50 is not None and s50_prev is not None and s50 > s50_prev
    s200_up = s200 is not None and s200_prev is not None and s200 > s200_prev

    if s50 and s200 and last > s50 and last > s200 and s50 > s200 and s50_up and s200_up:
        trend = "up"
    elif s50 and (last < s50 or (s200 and s50 < s200)):
        trend = "down"
    else:
        trend = "flat"

    near20 = s20 is not None and abs(last / s20 - 1) * 100 <= 2.0
    near50 = s50 is not None and abs(last / s50 - 1) * 100 <= 3.0
    entry = trend == "up" and (near20 or near50)
    return {
        "sma20": round(s20, 4) if s20 else None,
        "sma50": round(s50, 4) if s50 else None,
        "sma200": round(s200, 4) if s200 else None,
        "trend": trend,
        "entry_setup": entry,
        "zone": "20DMA" if near20 else ("50DMA" if near50 else None),
    }


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
    levels = compute_levels(bars, settings)

    ytd_open = next(
        (bar["close"] for bar in bars if bar["date"][:4] == bars[-1]["date"][:4]),
        None,
    )
    prior_close = bars[-2]["close"] if len(bars) > 1 else None

    flags = proximity_flags(last_close, levels, settings["proximity_alert_pct"])
    rsi_val = rsi(closes)
    rsi_prev = rsi(closes[:-1])
    rsi_rising = rsi_val is not None and rsi_prev is not None and rsi_val > rsi_prev
    overbought = rsi_val is not None and rsi_val >= 70
    near_res = any(f["kind"] == "resistance" for f in flags)
    near_sup = any(f["kind"] == "support" for f in flags)

    # Idea = swing-long: medium-term uptrend + pullback into the 20/50DMA zone,
    # confirmed by RSI turning up (the bullish reaction) and not yet overbought.
    # Exit/take-profit = RSI overbought or price at a 1m/3m resistance.
    idea = None
    if signal["entry_setup"] and rsi_rising and rsi_val is not None and rsi_val < 68:
        lv = entry_target(bars, levels, signal["sma20"], signal["sma50"], signal["zone"], last_close)
        reason = f"pullback to {signal['zone']}, RSI↑" + (" + support" if near_sup else "")
        idea = {"kind": "enter", "reason": reason, **lv}
    elif signal["trend"] == "up" and (overbought or near_res):
        idea = {"kind": "take_profit", "reason": f"RSI {rsi_val:.0f} overbought" if overbought else "at resistance"}

    return {
        "last_close": round(last_close, 4),
        "last_date": bars[-1]["date"],
        "change_pct": round((last_close / prior_close - 1) * 100, 2) if prior_close else None,
        "ytd_pct": round((last_close / ytd_open - 1) * 100, 2) if ytd_open else None,
        "sma20": signal["sma20"],
        "sma50": signal["sma50"],
        "sma200": signal["sma200"],
        "rsi": rsi_val,
        "trend": signal["trend"],
        "entry_setup": signal["entry_setup"],
        "zone": signal["zone"],
        "cta": donchian_signal(bars, settings.get("donchian_period", 20)),
        "idea": idea,
        "levels": levels,
        "flags": flags,
        "bars": [
            {"d": bar["date"], "o": bar["open"], "h": bar["high"],
             "l": bar["low"], "c": bar["close"], "v": bar.get("volume")}
            for bar in bars[-260:]
        ],
    }
