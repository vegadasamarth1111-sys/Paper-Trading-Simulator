"""
Paper Trading Simulator — Yahoo Finance provider (Phase 1, free tier)
=======================================================================
Keyless history + LTP polling for NSE equities and indices.

- No API key, no account, no cost. This is why it is the Phase 1 default
  and why the system works today with ZERO keys in .env.
- Source: Yahoo chart API (query1/query2.finance.yahoo.com/v8/finance/chart).
- NSE symbol RELIANCE -> ticker RELIANCE.NS (see universe.py).
- Honest limits, documented here instead of discovered in production:
  * 1m history is limited to ~7 days per request (we page nothing; callers
    pass period_days <= 7 for 1m, longer only for coarser intervals).
  * Quotes are ~15 minutes delayed. Fine for paper trading and replay;
    NOT for live scalping. The Angel One provider exists for real-time
    when the user configures keys.
  * Yahoo rate-limits aggressively without a User-Agent; every request
    carries a browser UA and callers must respect POLL_INTERVAL_SECONDS.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from ..universe import yahoo_ticker_for
from .base import MarketDataProviderBase, to_decimal

logger = logging.getLogger("core")

_CHART_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

# Our timeframe -> Yahoo interval/range. 1m range capped at 7d by Yahoo.
_INTERVAL_MAP = {
    "1m": ("1m", "7d"),
    "5m": ("5m", "1mo"),
    "15m": ("15m", "1mo"),
    "1h": ("60m", "3mo"),
    "1d": ("1d", "5y"),
}


class YahooFinanceProvider(MarketDataProviderBase):
    """Keyless Yahoo Finance market-data provider."""

    name = "yahoo"

    def __init__(
        self,
        client: httpx.Client | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._client = client
        self._timeout = timeout_seconds

    def _get_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(
            timeout=self._timeout,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        )

    def _chart(self, ticker: str, yahoo_interval: str, yahoo_range: str) -> dict:
        """Fetch one chart payload, trying query1 then query2 (Yahoo convention)."""
        params = {"interval": yahoo_interval, "range": yahoo_range}
        owns_client = self._client is None
        client = self._get_client()
        try:
            last_error: Exception | None = None
            for host in _CHART_HOSTS:
                url = f"https://{host}/v8/finance/chart/{ticker}"
                try:
                    response = client.get(url, params=params)
                    response.raise_for_status()
                    return response.json()  # type: ignore[no-any-return]
                except Exception as exc:  # noqa: BLE001 - fall through to next host
                    last_error = exc
                    logger.warning("Yahoo host %s failed for %s: %s", host, ticker, exc)
            raise RuntimeError(f"Yahoo chart request failed for {ticker}: {last_error}")
        finally:
            if owns_client:
                client.close()

    @staticmethod
    def _parse_chart(payload: dict) -> list[dict]:
        """Chart JSON -> ascending OHLC bars with Decimal prices (never float)."""
        try:
            result = payload["chart"]["result"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Unexpected Yahoo chart payload: {exc}") from exc
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        bars: list[dict] = []
        for i, epoch in enumerate(timestamps):
            if i >= len(closes) or closes[i] is None:
                continue  # Yahoo pads missing minutes with nulls; skip, don't invent
            ts = datetime.fromtimestamp(int(epoch), tz=UTC)
            # Missing OHLC legs fall back to close (a real print happened).
            close = to_decimal(str(closes[i]))
            bars.append(
                {
                    "ts": ts,
                    "open": to_decimal(str(opens[i]))
                    if i < len(opens) and opens[i] is not None
                    else close,
                    "high": to_decimal(str(highs[i]))
                    if i < len(highs) and highs[i] is not None
                    else close,
                    "low": to_decimal(str(lows[i]))
                    if i < len(lows) and lows[i] is not None
                    else close,
                    "close": close,
                    "volume": int(volumes[i]) if i < len(volumes) and volumes[i] is not None else 0,
                }
            )
        bars.sort(key=lambda b: b["ts"])
        return bars

    def fetch_history(
        self,
        symbol: str,
        *,
        interval: str = "1m",
        period_days: int = 5,
    ) -> list[dict]:
        if interval not in _INTERVAL_MAP:
            raise ValueError(f"Unsupported interval {interval!r}")
        yahoo_interval, default_range = _INTERVAL_MAP[interval]
        # Respect Yahoo's 1m window: clamp range instead of silently truncating.
        if interval == "1m" and period_days > 7:
            raise ValueError("Yahoo 1m history is limited to ~7 days; use period_days<=7")
        yahoo_range = default_range
        if interval == "1d" and period_days <= 30:
            yahoo_range = "1mo"
        ticker = yahoo_ticker_for(symbol)
        payload = self._chart(ticker, yahoo_interval, yahoo_range)
        bars = self._parse_chart(payload)
        if period_days < 365 and interval == "1d" and len(bars) > period_days:
            bars = bars[-period_days:]
        logger.info("Yahoo history: %s %s -> %d bars", symbol, interval, len(bars))
        return bars

    def fetch_ltp(self, symbols: list[str]) -> dict[str, dict]:
        """Latest close per symbol (1d, 5d window). Delayed ~15 min by Yahoo."""
        out: dict[str, dict] = {}
        for symbol in symbols:
            ticker = yahoo_ticker_for(symbol)
            payload = self._chart(ticker, "1d", "5d")
            bars = self._parse_chart(payload)
            if not bars:
                logger.warning("Yahoo returned no bars for %s", symbol)
                continue
            last = bars[-1]
            out[symbol] = {"ltp": last["close"], "ts": last["ts"]}
        return out

    def fetch_instruments(self) -> list[dict]:
        """
        Seed-universe rows in InstrumentLoader shape.

        Yahoo has no instrument-master endpoint, so this returns the Phase 1
        seed universe (NIFTY 50 + indices) with sensible equity defaults.
        The Angel One public ScripMaster remains the authority for tokens,
        lot sizes, and expiries — see AngelOneProvider.fetch_instruments().
        """
        from ..universe import get_seed_universe

        rows: list[dict] = []
        for entry in get_seed_universe():
            rows.append(
                {
                    "symbol": entry.symbol,
                    "exchange": "NSE",
                    "segment": "IDX" if entry.is_index else "EQ",
                    "name": entry.name,
                    # Empty token: Yahoo has no master; Angel ScripMaster fills
                    # real tokens on sync. Not a credential.  # nosec B105
                    "exchange_token": "",
                    "isin": "",
                    "lot_size": 1,
                    "tick_size": Decimal("0.05"),
                    "expiry_date": None,
                    "strike_price": None,
                    "option_type": "",
                    "underlying_symbol": "",
                    "upper_circuit": None,
                    "lower_circuit": None,
                }
            )
        return rows
