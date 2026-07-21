"""Moving averages and swing-based support/resistance."""


def moving_averages(bars, periods):
    """Latest SMA for each period. The chart recomputes full series from bars."""
    closes = [bar["close"] for bar in bars]
    latest = {}

    for period in periods:
        if len(closes) < period:
            latest[str(period)] = None
            continue
        latest[str(period)] = round(sum(closes[-period:]) / period, 4)

    return latest


def trend_signal(bars, ma_latest, settings):
    """MA-discipline signals: long above a rising 200DMA, enter on 50DMA pullbacks.

    trend: 'up' = above a rising 200DMA · 'down' = below 200DMA · 'flat' = otherwise.
    entry_setup: in an uptrend and pulled back to within entry_pullback_pct of the 50DMA.
    """
    closes = [bar["close"] for bar in bars]
    last = closes[-1]
    ma50 = ma_latest.get("50")
    ma200 = ma_latest.get("200")
    lookback = settings.get("ma_slope_lookback", 20)
    pullback = settings.get("entry_pullback_pct", 2.0)

    slope_pct = None
    if ma200 and len(closes) >= 200 + lookback:
        prev200 = sum(closes[-200 - lookback:-lookback]) / 200
        if prev200:
            slope_pct = round((ma200 / prev200 - 1) * 100, 2)

    above200 = ma200 is not None and last > ma200
    rising = slope_pct is None or slope_pct > 0
    if above200 and rising:
        trend = "up"
    elif ma200 is not None and last < ma200:
        trend = "down"
    else:
        trend = "flat"

    entry = trend == "up" and ma50 is not None and abs((last / ma50 - 1) * 100) <= pullback

    return {"trend": trend, "ma200_slope_pct": slope_pct, "entry_setup": entry}


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
    """Levels the price is currently sitting on, within threshold."""
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
    ma_latest = moving_averages(bars, settings["ma_periods"])
    levels = compute_levels(bars, settings)
    signal = trend_signal(bars, ma_latest, settings)

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
        "ma": ma_latest,
        "vs_ma": {
            period: round((last_close / value - 1) * 100, 2)
            for period, value in ma_latest.items() if value
        },
        "trend": signal["trend"],
        "ma200_slope_pct": signal["ma200_slope_pct"],
        "entry_setup": signal["entry_setup"],
        "levels": levels,
        "flags": proximity_flags(last_close, levels, settings["proximity_alert_pct"]),
        "bars": [
            {
                "d": bar["date"],
                "o": bar["open"],
                "h": bar["high"],
                "l": bar["low"],
                "c": bar["close"],
            }
            for bar in bars[-260:]
        ],
    }
