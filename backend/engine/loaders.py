"""
Paper Trading Simulator — Historical data loaders for runs (Phase 4)
=======================================================================
Closed candles and raw ticks from the store, mapped to strategy views.
Backtests prefer stored CLOSED candles; when the store has gaps, the
caller sees fewer bars (never invented ones — fabrication would poison
the determinism gate).
"""

from datetime import datetime

from core.models.instruments import Instrument
from engine.base import CandleView, TickView


def load_closed_candles(
    symbol: str, timeframe: str, start: datetime, end: datetime
) -> list[CandleView]:
    """Ascending closed candles for one symbol in [start, end)."""
    from market_data.models import Candle

    rows = (
        Candle.objects.filter(
            symbol=symbol.upper(),
            timeframe=timeframe,
            open_ts__gte=start,
            open_ts__lt=end,
            is_complete=True,
        )
        .order_by("open_ts")
        .values(
            "symbol",
            "timeframe",
            "open_ts",
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "volume",
        )
    )
    return [CandleView(**row) for row in rows]


def load_ticks(symbol: str, start: datetime, end: datetime) -> list[TickView]:
    """Ascending raw ticks for one symbol in [start, end)."""
    from market_data.models import Tick

    rows = (
        Tick.objects.filter(symbol=symbol.upper(), exchange_ts__gte=start, exchange_ts__lt=end)
        .order_by("exchange_ts", "id")
        .values("symbol", "exchange_ts", "ltp", "volume_traded")
    )
    return [TickView(**row) for row in rows]


def resolve_instruments(symbols: list[str]) -> dict[str, Instrument]:
    """Active NSE rows for the universe. Missing symbols raise (fail loudly)."""
    found = {
        inst.symbol: inst
        for inst in Instrument.objects.filter(
            symbol__in=[s.upper() for s in symbols],
            exchange="NSE",
            is_active=True,
            effective_to__isnull=True,
        )
    }
    missing = [s for s in symbols if s.upper() not in found]
    if missing:
        raise ValueError(f"symbols missing from instrument master: {missing}")
    return found
