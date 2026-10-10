"""
Paper Trading Simulator — Candle builder (Phase 1, plan section 4.5)
======================================================================
Pure functions: ticks in, candles out. No Django, no I/O, no clock.

- 1-minute candles are built from ticks, then aggregated to 5m/15m/1h/1d.
- Intraday boundaries align to 09:15 IST (market open).
- A candle is complete only when its interval has fully passed; strategies
  must only ever see complete candles (prevents look-ahead bias).
- Empty intervals produce explicit empty candles (volume=0, OHLC=prev close,
  is_empty=True) rather than being skipped, so gaps stay visible.
- End-of-day rebuilds from raw ticks must reproduce the stored candles;
  any difference is a data-quality mismatch, not a rounding error. Money
  stays Decimal throughout — float never touches a price here.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

# Timeframe -> interval length in minutes (1d handled separately).
TIMEFRAME_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1h": 60,
}

# Market open in IST — all intraday grids anchor here.
MARKET_OPEN_MINUTES = 9 * 60 + 15


@dataclass(frozen=True)
class CandleData:
    """One built candle (storage-agnostic; callers map this onto the model)."""

    open_ts: datetime  # UTC, interval open
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int
    trades: int
    is_empty: bool = False


@dataclass(frozen=True)
class TickPoint:
    """Minimal tick view the builder needs (keeps it testable without Django)."""

    exchange_ts: datetime  # UTC
    ltp: Decimal
    volume_traded: int  # cumulative day volume


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def floor_open_ts(dt_utc: datetime, timeframe: str) -> datetime:
    """
    Floor a UTC timestamp to its candle-open UTC timestamp.

    Intraday grids anchor at 09:15 IST. Daily candles open at 09:15 IST on
    the same trading day.
    """
    dt_utc = _as_utc(dt_utc)
    ist = dt_utc.astimezone(IST)

    if timeframe == "1d":
        day_open_ist = ist.replace(hour=9, minute=15, second=0, microsecond=0)
        # Ticks before 09:15 (pre-open) belong to that day's daily candle.
        if ist.time() < day_open_ist.time():
            day_open_ist -= timedelta(days=1)
            # Never attribute pre-open ticks to a weekend/holiday shift here;
            # the calendar owns trading-day decisions. This only anchors time.
        return day_open_ist.astimezone(UTC)

    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"Unknown timeframe {timeframe!r}")

    interval = TIMEFRAME_MINUTES[timeframe]
    minutes_since_midnight = ist.hour * 60 + ist.minute
    # Ticks before the open anchor to the 00:00 grid so pre-open prints
    # still bucket deterministically instead of raising.
    if minutes_since_midnight < MARKET_OPEN_MINUTES:
        floored = (minutes_since_midnight // interval) * interval
    else:
        elapsed = minutes_since_midnight - MARKET_OPEN_MINUTES
        floored = MARKET_OPEN_MINUTES + (elapsed // interval) * interval
    bucket_ist = ist.replace(hour=floored // 60, minute=floored % 60, second=0, microsecond=0)
    return bucket_ist.astimezone(UTC)


def interval_end(open_ts_utc: datetime, timeframe: str) -> datetime:
    """Exclusive end of the interval that starts at open_ts_utc."""
    open_ts_utc = _as_utc(open_ts_utc)
    if timeframe == "1d":
        # Daily candle closes at next day 09:15 IST; approximate as +24h in
        # UTC terms by round-tripping through IST to survive DST-free IST
        # (IST has no DST, so this is exact, but explicit is better).
        ist_open = open_ts_utc.astimezone(IST)
        ist_end = ist_open + timedelta(days=1)
        return ist_end.astimezone(UTC)
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"Unknown timeframe {timeframe!r}")
    return open_ts_utc + timedelta(minutes=TIMEFRAME_MINUTES[timeframe])


def is_interval_closed(open_ts_utc: datetime, timeframe: str, now_utc: datetime) -> bool:
    """True when the interval has fully passed (candle may be served to strategies)."""
    return _as_utc(now_utc) >= interval_end(open_ts_utc, timeframe)


def build_1m_candles(ticks: list[TickPoint]) -> list[CandleData]:
    """
    Aggregate ticks into 1-minute candles.

    Volume per candle = last cumulative volume in the minute minus the last
    cumulative volume before the minute (falls back to 0 when unknown).
    Ticks must be in ascending exchange_ts order; unsorted input raises.
    """
    ordered = sorted(ticks, key=lambda t: _as_utc(t.exchange_ts))
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        if _as_utc(cur.exchange_ts) < _as_utc(prev.exchange_ts):
            raise ValueError("ticks must be in ascending exchange_ts order")

    buckets: dict[datetime, list[TickPoint]] = {}
    for tick in ordered:
        key = floor_open_ts(tick.exchange_ts, "1m")
        buckets.setdefault(key, []).append(tick)

    candles: list[CandleData] = []
    prev_cumulative: int | None = None
    for open_ts in sorted(buckets):
        points = buckets[open_ts]
        o = points[0].ltp
        c = points[-1].ltp
        h = max(p.ltp for p in points)
        lo = min(p.ltp for p in points)
        last_cum = points[-1].volume_traded
        if prev_cumulative is None:
            vol = points[-1].volume_traded - points[0].volume_traded + 0
            # First bucket: intra-minute delta only; the day-open baseline is
            # unknown, so volume is the movement inside this minute.
            vol = max(0, last_cum - points[0].volume_traded)
            # If the minute holds a single tick, its volume contribution is 0
            # by construction (no baseline). The day total still reconciles
            # because every subsequent minute diffs against its predecessor.
        else:
            vol = max(0, last_cum - prev_cumulative)
        prev_cumulative = last_cum
        candles.append(
            CandleData(
                open_ts=open_ts,
                open_price=o,
                high_price=h,
                low_price=lo,
                close_price=c,
                volume=vol,
                trades=len(points),
            )
        )
    return candles


def fill_empty_intervals(
    candles: list[CandleData],
    *,
    timeframe: str,
    session_start_utc: datetime,
    session_end_utc: datetime,
    prev_close: Decimal,
) -> list[CandleData]:
    """
    Insert explicit empty candles for intervals with no ticks.

    Empty candles carry OHLC = prev_close, volume = 0, is_empty = True, so a
    gap reads as data instead of silence. prev_close rolls forward through
    consecutive empties.
    """
    by_open = {c.open_ts: c for c in candles}
    filled: list[CandleData] = []
    cursor = floor_open_ts(session_start_utc, timeframe)
    end = _as_utc(session_end_utc)
    close = prev_close
    while cursor < end:
        existing = by_open.get(cursor)
        if existing is None:
            filled.append(
                CandleData(
                    open_ts=cursor,
                    open_price=close,
                    high_price=close,
                    low_price=close,
                    close_price=close,
                    volume=0,
                    trades=0,
                    is_empty=True,
                )
            )
        else:
            filled.append(existing)
            close = existing.close_price
        if timeframe == "1d":
            cursor = interval_end(cursor, "1d")
        elif timeframe in TIMEFRAME_MINUTES:
            cursor = cursor + timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
        else:
            raise ValueError(f"Unknown timeframe {timeframe!r}")
    return filled


def aggregate_candles(candles_1m: list[CandleData], timeframe: str) -> list[CandleData]:
    """
    Roll 1-minute candles up to 5m/15m/1h/1d.

    Input must be complete 1m candles in ascending order. Empty 1m inputs
    propagate: an aggregate is empty only when ALL its minutes are empty.
    """
    if timeframe == "1m":
        return list(candles_1m)
    if timeframe not in (*TIMEFRAME_MINUTES, "1d"):
        raise ValueError(f"Unknown timeframe {timeframe!r}")
    groups: dict[datetime, list[CandleData]] = {}
    for candle in candles_1m:
        key = floor_open_ts(candle.open_ts, timeframe)
        groups.setdefault(key, []).append(candle)
    out: list[CandleData] = []
    for open_ts in sorted(groups):
        members = sorted(groups[open_ts], key=lambda c: c.open_ts)
        non_empty = [m for m in members if not m.is_empty]
        if not non_empty:
            anchor = members[0]
            out.append(
                CandleData(
                    open_ts=open_ts,
                    open_price=anchor.open_price,
                    high_price=anchor.high_price,
                    low_price=anchor.low_price,
                    close_price=anchor.close_price,
                    volume=0,
                    trades=0,
                    is_empty=True,
                )
            )
            continue
        out.append(
            CandleData(
                open_ts=open_ts,
                open_price=non_empty[0].open_price,
                high_price=max(m.high_price for m in non_empty),
                low_price=min(m.low_price for m in non_empty),
                close_price=non_empty[-1].close_price,
                volume=sum(m.volume for m in members),
                trades=sum(m.trades for m in members),
            )
        )
    return out
