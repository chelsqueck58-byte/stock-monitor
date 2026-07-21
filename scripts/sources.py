"""Price bar sources. Swap the active source without touching downstream code.

Every source exposes fetch_bars(member, lookback_days) -> list of bar dicts
sorted oldest-first: {date, open, high, low, close, volume}.
"""
import time

import requests

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
REQUEST_PAUSE = 0.3


class SourceError(Exception):
    """Raised when a source cannot return usable bars."""


class YahooSource:
    """Free daily bars. Validated against IBKR for daily-close accuracy.

    Bootstrap source: works headless with no auth, which IB Gateway does not
    until it is installed and logged in on the host.
    """

    name = "yahoo"

    def fetch_bars(self, member, lookback_days=730):
        symbol = member.get("yahoo")
        if not symbol:
            raise SourceError(f"{member['id']}: no yahoo symbol configured")

        params = {"range": f"{lookback_days}d", "interval": "1d"}
        try:
            response = requests.get(
                YAHOO_CHART.format(symbol=symbol),
                params=params,
                headers=HEADERS,
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise SourceError(f"{member['id']}: network error - {exc}") from exc
        except ValueError as exc:
            raise SourceError(f"{member['id']}: bad JSON - {exc}") from exc

        chart = payload.get("chart", {})
        if chart.get("error"):
            raise SourceError(f"{member['id']}: {chart['error'].get('description')}")

        results = chart.get("result") or []
        if not results:
            raise SourceError(f"{member['id']}: empty result")

        result = results[0]
        stamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        meta = result.get("meta", {})

        bars = []
        for index, stamp in enumerate(stamps):
            close = _at(quote.get("close"), index)
            if close is None:
                continue
            bars.append({
                "date": time.strftime("%Y-%m-%d", time.gmtime(stamp)),
                "open": _at(quote.get("open"), index),
                "high": _at(quote.get("high"), index),
                "low": _at(quote.get("low"), index),
                "close": close,
                "volume": _at(quote.get("volume"), index),
            })

        if not bars:
            raise SourceError(f"{member['id']}: no usable bars")

        time.sleep(REQUEST_PAUSE)
        return bars, {"currency": meta.get("currency"), "exchange": meta.get("fullExchangeName")}


class IbkrSource:
    """IBKR bars via IB Gateway. Requires Gateway running and logged in.

    Not usable from a headless `claude -p` run through MCP - the connector is
    session-bound to claude.ai. This talks to a local Gateway instead.
    """

    name = "ibkr"

    def __init__(self, host="127.0.0.1", port=4001, client_id=17):
        self.host = host
        self.port = port
        self.client_id = client_id
        self._ib = None

    def _connect(self):
        if self._ib is not None:
            return self._ib
        try:
            from ib_insync import IB
        except ImportError as exc:
            raise SourceError(
                "ib_insync not installed. Run: uv pip install ib_insync"
            ) from exc

        ib = IB()
        try:
            ib.connect(self.host, self.port, clientId=self.client_id, timeout=15)
        except Exception as exc:
            raise SourceError(
                f"cannot reach IB Gateway at {self.host}:{self.port} - "
                f"is it running and logged in? ({exc})"
            ) from exc
        self._ib = ib
        return ib

    def fetch_bars(self, member, lookback_days=730):
        from ib_insync import Contract

        ib = self._connect()
        spec = member.get("ibkr") or {}
        contract = Contract(
            symbol=spec.get("symbol"),
            secType=spec.get("sec_type", "STK"),
            exchange=spec.get("exchange", "SMART"),
            currency=spec.get("currency"),
        )

        try:
            qualified = ib.qualifyContracts(contract)
            if not qualified:
                raise SourceError(f"{member['id']}: contract did not qualify")
            raw = ib.reqHistoricalData(
                qualified[0],
                endDateTime="",
                durationStr=f"{lookback_days} D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(f"{member['id']}: IBKR request failed - {exc}") from exc

        if not raw:
            raise SourceError(f"{member['id']}: IBKR returned no bars")

        bars = [{
            "date": str(bar.date),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        } for bar in raw]

        return bars, {"currency": spec.get("currency"), "exchange": spec.get("exchange")}


def get_source(name):
    """Resolve a source by name."""
    sources = {"yahoo": YahooSource, "ibkr": IbkrSource}
    if name not in sources:
        raise SourceError(f"unknown source '{name}'. Options: {', '.join(sources)}")
    return sources[name]()


def _at(series, index):
    """Safe indexed read from a possibly-None series."""
    if not series or index >= len(series):
        return None
    return series[index]
