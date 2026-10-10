"""
Tests for market_data.candles — 1m building, 09:15-anchored grids,
empty-interval markers, higher-timeframe rollups, close logic.
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from market_data.candles import (
    TickPoint,
    aggregate_candles,
    build_1m_candles,
    fill_empty_intervals,
    floor_open_ts,
    interval_end,
    is_interval_closed,
)

UTC = ZoneInfo("UTC")
IST = ZoneInfo("Asia/Kolkata")


def _ist(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=IST).astimezone(UTC)


def _tick(h, mi, price, vol, day=(2026, 1, 5)):
    return TickPoint(exchange_ts=_ist(*day, h, mi), ltp=Decimal(str(price)), volume_traded=vol)


class TestFloorAnchoredTo0915:
    def test_5m_grid_anchors_at_open(self):
        # 09:17 belongs to the 09:15 bucket, not 09:15-clock-grid 09:15.
        assert floor_open_ts(_ist(2026, 1, 5, 9, 17), "5m") == _ist(2026, 1, 5, 9, 15)
        assert floor_open_ts(_ist(2026, 1, 5, 9, 20), "5m") == _ist(2026, 1, 5, 9, 20)

    def test_1m_floors_seconds(self):
        dt = datetime(2026, 1, 5, 3, 47, 33, tzinfo=UTC)  # 09:17:33 IST
        assert floor_open_ts(dt, "1m") == _ist(2026, 1, 5, 9, 17)

    def test_1h_grid_anchors_at_open(self):
        assert floor_open_ts(_ist(2026, 1, 5, 10, 10), "1h") == _ist(2026, 1, 5, 9, 15)
        assert floor_open_ts(_ist(2026, 1, 5, 10, 15), "1h") == _ist(2026, 1, 5, 10, 15)

    def test_unknown_timeframe_raises(self):
        with pytest.raises(ValueError):
            floor_open_ts(_ist(2026, 1, 5, 10, 0), "2m")


class TestBuild1m:
    def test_ohlc_and_volume_delta(self):
        ticks = [
            _tick(9, 15, "100.00", 1000),
            _tick(9, 15, "101.00", 1200),
            _tick(9, 15, "99.50", 1500),
            _tick(9, 16, "100.50", 1800),
        ]
        candles = build_1m_candles(ticks)
        assert len(candles) == 2
        first, second = candles
        assert (first.open_price, first.high_price, first.low_price, first.close_price) == (
            Decimal("100.00"),
            Decimal("101.00"),
            Decimal("99.50"),
            Decimal("99.50"),
        )
        assert first.volume == 500  # 1500 - 1000 intra-minute movement
        assert first.trades == 3
        assert second.volume == 300  # 1800 - 1500 across minutes
        assert second.close_price == Decimal("100.50")

    def test_single_tick_minute_has_zero_volume_without_baseline(self):
        (candle,) = build_1m_candles([_tick(9, 15, "100", 5000)])
        assert candle.volume == 0
        assert candle.trades == 1

    def test_unsorted_input_still_buckets_deterministically(self):
        ticks = [_tick(9, 16, "101", 200), _tick(9, 15, "100", 100)]
        candles = build_1m_candles(ticks)
        assert [c.open_ts for c in candles] == sorted(c.open_ts for c in candles)

    def test_money_never_float(self):
        (candle,) = build_1m_candles([_tick(9, 15, "100.05", 10), _tick(9, 15, "100.10", 20)])
        for field in (candle.open_price, candle.high_price, candle.low_price, candle.close_price):
            assert isinstance(field, Decimal)


class TestEmptyIntervals:
    def test_gap_becomes_explicit_empty_candle(self):
        ticks = [_tick(9, 15, "100", 100), _tick(9, 18, "102", 200)]
        built = build_1m_candles(ticks)
        filled = fill_empty_intervals(
            built,
            timeframe="1m",
            session_start_utc=_ist(2026, 1, 5, 9, 15),
            session_end_utc=_ist(2026, 1, 5, 9, 19),
            prev_close=Decimal("99"),
        )
        assert [c.open_ts for c in filled] == [
            _ist(2026, 1, 5, 9, 15),
            _ist(2026, 1, 5, 9, 16),
            _ist(2026, 1, 5, 9, 17),
            _ist(2026, 1, 5, 9, 18),
        ]
        empties = [c for c in filled if c.is_empty]
        assert len(empties) == 2
        assert all(c.volume == 0 and c.trades == 0 for c in empties)
        # Empty candles carry the previous close forward — no invented prices.
        assert empties[0].close_price == Decimal("100")
        assert filled[-1].close_price == Decimal("102")


class TestAggregation:
    def _five_minutes(self):
        ticks = []
        vol = 0
        for minute in range(15, 20):
            vol += 100
            ticks.append(_tick(9, minute, f"{100 + minute - 15}", vol))
        return build_1m_candles(ticks)

    def test_5m_rollup(self):
        ones = self._five_minutes()
        (agg,) = aggregate_candles(ones, "5m")
        assert agg.open_price == Decimal("100")
        assert agg.close_price == Decimal("104")
        assert agg.high_price == Decimal("104")
        assert agg.low_price == Decimal("100")
        assert agg.volume == sum(c.volume for c in ones)
        assert agg.trades == 5

    def test_all_empty_aggregate_stays_empty(self):
        ones = fill_empty_intervals(
            [],
            timeframe="1m",
            session_start_utc=_ist(2026, 1, 5, 9, 15),
            session_end_utc=_ist(2026, 1, 5, 9, 20),
            prev_close=Decimal("100"),
        )
        (agg,) = aggregate_candles(ones, "5m")
        assert agg.is_empty
        assert agg.volume == 0


class TestCloseLogic:
    def test_candle_closes_when_interval_passes(self):
        open_ts = _ist(2026, 1, 5, 9, 15)
        assert not is_interval_closed(open_ts, "1m", open_ts)
        assert is_interval_closed(open_ts, "1m", interval_end(open_ts, "1m"))
        # Strategies only see closed candles: the boundary is exact, not fuzzy.
        assert not is_interval_closed(open_ts, "5m", _ist(2026, 1, 5, 9, 19))

    def test_golden_replay_is_deterministic(self):
        """Same event sequence twice -> identical candles. Replay-fidelity contract."""
        ticks = [
            _tick(9, 15, "2500.00", 10000),
            _tick(9, 15, "2501.50", 10100),
            _tick(9, 16, "2499.00", 10300),
            _tick(9, 16, "2502.00", 10500),
        ]
        first = build_1m_candles(list(ticks))
        second = build_1m_candles(list(ticks))
        assert [(c.open_price, c.close_price, c.volume) for c in first] == [
            (c.open_price, c.close_price, c.volume) for c in second
        ]
