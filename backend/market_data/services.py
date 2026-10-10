"""
Paper Trading Simulator — Market-data services (Phase 1)
==========================================================
Read/write layer over Tick/Candle/BadTick. Views and tasks go through
here; they never touch models directly.

- get_provider(): MARKET_DATA_PROVIDER=yahoo (default, keyless) or angel
  (keyless master; quotes raise MissingCredentials until keys exist).
- Latest prices live in Redis (django cache) for instant broker/risk reads,
  with a DB fallback when the cache is cold.
- store_history(): provider OHLC bars -> 1m Candle rows (idempotent:
  existing (instrument, timeframe, open_ts) rows are updated, not duplicated).
- rebuild_and_verify(): EOD rebuild of 1m candles from ticks_raw; returns
  the mismatch count feeding the data-quality report.
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from decouple import config
from django.core.cache import cache
from django.db import transaction

from core.enums import CandleTimeframe
from core.models.instruments import Instrument

from .candles import (
    CandleData,
    TickPoint,
    aggregate_candles,
    build_1m_candles,
    floor_open_ts,
    is_interval_closed,
)
from .models import Candle, DataSource, Tick

logger = logging.getLogger("core")
UTC = ZoneInfo("UTC")
IST = ZoneInfo("Asia/Kolkata")

LATEST_KEY = "md:latest:{symbol}"
CACHE_TIMEOUT_SECONDS = 900


def get_provider() -> object:
    """Provider factory from env. Unknown names fail loudly, not silently."""
    from .providers.angel import AngelOneProvider
    from .providers.yahoo import YahooFinanceProvider

    name = config("MARKET_DATA_PROVIDER", default="yahoo").strip().lower()
    if name == "yahoo":
        return YahooFinanceProvider()
    if name == "angel":
        return AngelOneProvider()
    raise RuntimeError(
        f"Unknown MARKET_DATA_PROVIDER={name!r}. Use 'yahoo' (free, keyless) or 'angel'."
    )


def get_latest_price(symbol: str) -> dict | None:
    """
    Latest price for the broker/risk engine. Cache first, DB fallback.

    Returns {"ltp": Decimal, "ts": datetime} or None when nothing is known.
    """
    symbol = symbol.upper()
    try:
        cached = cache.get(LATEST_KEY.format(symbol=symbol))
    except Exception:  # noqa: BLE001 - cache outage must not break trading reads
        cached = None
    if cached:
        try:
            return {
                "ltp": Decimal(str(cached["ltp"])),
                "ts": datetime.fromisoformat(str(cached["ts"])),
            }
        except Exception:  # noqa: BLE001, S110 - corrupt cache entry -> fall through to DB
            logger.debug("Corrupt latest-price cache entry for %s; using DB", symbol)
    instrument = Instrument.objects.filter(
        symbol=symbol, exchange="NSE", is_active=True, effective_to__isnull=True
    ).first()
    if instrument is None:
        return None
    row = Tick.objects.filter(instrument=instrument).order_by("-exchange_ts").first()
    if row is None:
        return None
    return {"ltp": row.ltp, "ts": row.exchange_ts}


def store_history(
    symbol: str,
    bars: list[dict],
    *,
    source: str = DataSource.YAHOO,
) -> dict:
    """
    Persist provider OHLC bars as 1m Candle rows (plus 5m/15m/1h/1d rollups).

    Idempotent: re-running the same bars updates existing rows. Returns
    {"created": n, "updated": n, "skipped": n}.
    """
    instrument = Instrument.objects.filter(
        symbol=symbol.upper(), exchange="NSE", is_active=True, effective_to__isnull=True
    ).first()
    if instrument is None:
        raise ValueError(f"No active instrument for {symbol!r}; run sync_master first")
    if not bars:
        return {"created": 0, "updated": 0, "skipped": 0}

    # Provider bars -> 1m CandleData, then roll up.
    minute_candles = [
        CandleData(
            open_ts=b["ts"],
            open_price=b["open"],
            high_price=b["high"],
            low_price=b["low"],
            close_price=b["close"],
            volume=int(b.get("volume", 0)),
            trades=1,
        )
        for b in bars
    ]
    layers: dict[str, list[CandleData]] = {"1m": minute_candles}
    for timeframe in ("5m", "15m", "1h", "1d"):
        layers[timeframe] = aggregate_candles(minute_candles, timeframe)

    created = updated = skipped = 0
    now_utc = datetime.now(UTC)
    with transaction.atomic():
        for timeframe, rows in layers.items():
            for row in rows:
                complete = is_interval_closed(row.open_ts, timeframe, now_utc)
                obj, was_created = Candle.objects.update_or_create(
                    instrument=instrument,
                    timeframe=timeframe,
                    open_ts=row.open_ts,
                    defaults={
                        "symbol": instrument.symbol,
                        "exchange": instrument.exchange,
                        "open_price": row.open_price,
                        "high_price": row.high_price,
                        "low_price": row.low_price,
                        "close_price": row.close_price,
                        "volume": row.volume,
                        "trades": row.trades,
                        "is_complete": complete,
                        "is_empty": row.is_empty,
                        "source": source,
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1
    logger.info("History stored %s: +%d ~%d", symbol, created, updated)
    return {"created": created, "updated": updated, "skipped": skipped}


def rebuild_1m_from_ticks(
    instrument: Instrument,
    trading_day: date,
) -> tuple[list[CandleData], int]:
    """
    Rebuild a day's 1m candles purely from ticks_raw.

    Returns (rebuilt, mismatches_vs_stored). The EOD job logs mismatches;
    nonzero means stored candles diverged from raw truth (a bug signal).
    """
    day_start_ist = datetime(trading_day.year, trading_day.month, trading_day.day, 0, 0, tzinfo=IST)
    day_start_utc = day_start_ist.astimezone(UTC)
    day_end_utc = day_start_utc + __import__("datetime").timedelta(days=1)

    ticks = list(
        Tick.objects.filter(
            instrument=instrument,
            exchange_ts__gte=day_start_utc,
            exchange_ts__lt=day_end_utc,
        ).order_by("exchange_ts")
    )
    points = [
        TickPoint(exchange_ts=t.exchange_ts, ltp=t.ltp, volume_traded=t.volume_traded)
        for t in ticks
    ]
    rebuilt = build_1m_candles(points)

    stored = {
        c.open_ts: c
        for c in Candle.objects.filter(
            instrument=instrument,
            timeframe=CandleTimeframe.M1,
            open_ts__gte=day_start_utc,
            open_ts__lt=day_end_utc,
        )
    }
    mismatches = 0
    for candle in rebuilt:
        row = stored.get(candle.open_ts)
        if row is None:
            mismatches += 1
            continue
        if (
            row.open_price != candle.open_price
            or row.high_price != candle.high_price
            or row.low_price != candle.low_price
            or row.close_price != candle.close_price
        ):
            mismatches += 1
    return rebuilt, mismatches


def floor_open(symbol_time: datetime, timeframe: str) -> datetime:
    """Public helper for views/tasks: floor any timestamp to its candle open."""
    _ = symbol_time  # reserved for future session-aware flooring
    return floor_open_ts(symbol_time, timeframe)
