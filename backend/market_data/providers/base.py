"""
Paper Trading Simulator — Provider interface (Phase 1)
=========================================================
One interface, two free-tier implementations:

- YahooFinanceProvider: keyless. History + 1-min polling. The Phase 1
  default; works with ZERO keys today.
- AngelOneProvider: public instrument master is keyless; QUOTES require
  API credentials the user has not shared yet. Market methods raise
  MissingCredentials with the exact .env names instead of failing cryptically.

Both providers speak the loader's instrument-dict shape so
InstrumentLoader can consume either without code changes.
"""

from abc import ABC, abstractmethod
from decimal import Decimal


class MissingCredentials(RuntimeError):
    """Raised when a provider needs keys the user has not configured yet."""


class OhlcBar(dict):
    """Typed convenience: {'ts', 'open', 'high', 'low', 'close', 'volume'}."""


class MarketDataProviderBase(ABC):
    """Market-data side of a provider: history + live quotes."""

    name: str = "base"

    @abstractmethod
    def fetch_history(
        self,
        symbol: str,
        *,
        interval: str = "1m",
        period_days: int = 5,
    ) -> list[dict]:
        """
        Return OHLC bars ascending by ts (UTC datetime).

        Each dict: {"ts": datetime, "open": Decimal, "high": Decimal,
                    "low": Decimal, "close": Decimal, "volume": int}.
        """
        ...

    @abstractmethod
    def fetch_ltp(self, symbols: list[str]) -> dict[str, dict]:
        """
        Return {symbol: {"ltp": Decimal, "ts": datetime}} for the watchlist.
        Used by the 1-min polling ingestor.
        """
        ...

    @abstractmethod
    def fetch_instruments(self) -> list[dict]:
        """Instrument-master rows in InstrumentLoader's dict shape."""
        ...

    def fetch_holidays(self, year: int) -> list[dict]:
        """Trading holidays in loader shape. Default: none (calendar falls back)."""
        _ = year
        return []


def to_decimal(value: object) -> Decimal:
    """Decimal() without ever touching float. Strings and ints only."""
    if isinstance(value, float):
        raise TypeError("float prices are forbidden; use str/int upstream")
    return Decimal(str(value))


def normalise_interval(interval: str) -> str:
    """Yahoo/Angel spellings -> our CandleTimeframe values."""
    mapping = {
        "1m": "1m",
        "1minute": "1m",
        "5m": "5m",
        "5minute": "5m",
        "15m": "15m",
        "15minute": "15m",
        "1h": "1h",
        "60m": "1h",
        "1d": "1d",
        "1day": "1d",
        "day": "1d",
    }
    try:
        return mapping[interval]
    except KeyError:
        raise ValueError(f"Unsupported interval {interval!r}") from None
