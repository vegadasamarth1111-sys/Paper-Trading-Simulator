"""
Tests for market_data.quality — daily data-quality verdicts (plan 4.8).

A day is SUSPECT when: expected symbols have zero ticks, the bad-tick
ratio exceeds 5%, or the EOD candle rebuild disagrees with stored rows.
Clean days must be OK — otherwise every backtest would be discarded.
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from core.models import Instrument
from market_data.models import BadTick, Candle, Tick
from market_data.quality import _count_gaps, build_daily_report

UTC = ZoneInfo("UTC")
DAY = datetime(2026, 1, 5).date()  # a Monday
DAY_START = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)

pytestmark = pytest.mark.django_db


@pytest.fixture
def reliance():
    return Instrument.objects.create(
        symbol="RELIANCE",
        exchange="NSE",
        segment="EQ",
        name="Reliance Industries Limited",
        exchange_token="256265",
        lot_size=1,
        tick_size=Decimal("0.05"),
        effective_from=datetime(2026, 1, 1, tzinfo=UTC).date(),
        is_active=True,
    )


def _tick_at(instrument, hour_utc, minute, price="2500", vol=1000):
    ts = datetime(2026, 1, 5, hour_utc, minute, tzinfo=UTC)
    return Tick.objects.create(
        instrument=instrument,
        symbol=instrument.symbol,
        exchange="NSE",
        exchange_ts=ts,
        received_ts=ts,
        ltp=Decimal(price),
        volume_traded=vol,
        source="yahoo",
    )


def _store_matching_candles(instrument):
    """Store the 1m candles a healthy ingestor would have maintained."""
    from market_data.candles import TickPoint, build_1m_candles

    ticks = list(Tick.objects.filter(instrument=instrument).order_by("exchange_ts"))
    points = [
        TickPoint(exchange_ts=t.exchange_ts, ltp=t.ltp, volume_traded=t.volume_traded)
        for t in ticks
    ]
    for candle in build_1m_candles(points):
        Candle.objects.create(
            instrument=instrument,
            symbol=instrument.symbol,
            exchange="NSE",
            timeframe="1m",
            open_ts=candle.open_ts,
            open_price=candle.open_price,
            high_price=candle.high_price,
            low_price=candle.low_price,
            close_price=candle.close_price,
            volume=candle.volume,
            trades=candle.trades,
            is_complete=True,
            source="yahoo",
        )


class TestGapCounter:
    def test_counts_only_long_gaps(self):
        base = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)
        stamps = [base, base.replace(minute=1), base.replace(minute=10)]
        count, worst = _count_gaps(stamps, 300)
        assert (count, worst) == (1, 540)

    def test_no_gaps(self):
        base = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)
        count, worst = _count_gaps([base, base.replace(minute=1)], 300)
        assert (count, worst) == (0, 0)


class TestDailyReport:
    def test_clean_day_is_ok(self, reliance):
        _tick_at(reliance, 4, 0, vol=100)
        _tick_at(reliance, 4, 1, vol=200)
        _store_matching_candles(reliance)
        report = build_daily_report(DAY)
        assert report.total_ticks == 2
        assert not report.is_suspect
        assert report.bad_tick_ratio == 0.0

    def test_missing_symbol_marks_suspect(self, reliance):
        # RELIANCE exists in the master but has zero ticks -> suspect.
        report = build_daily_report(DAY)
        assert report.is_suspect
        assert "RELIANCE" in report.missing_symbols

    def test_high_bad_ratio_marks_suspect(self, reliance):
        _tick_at(reliance, 4, 0, vol=100)
        _store_matching_candles(reliance)
        day_start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
        for _ in range(3):
            BadTick.objects.create(
                instrument=reliance,
                symbol="RELIANCE",
                exchange="NSE",
                exchange_ts=day_start,
                received_ts=day_start,
                reason="STALE_TS",
                details="old",
                raw_payload={},
                source="yahoo",
            )
        # BadTicks are stamped at creation (auto_now_add); backdate them into
        # the reporting day so the ratio reflects the day under test.
        BadTick.objects.all().update(created_at=day_start)
        report = build_daily_report(DAY)
        assert report.bad_tick_ratio == 3.0  # 3 bad / 1 good
        assert report.is_suspect

    def test_report_is_idempotent(self, reliance):
        _tick_at(reliance, 4, 0, vol=100)
        first = build_daily_report(DAY)
        second = build_daily_report(DAY)
        assert first.id == second.id

    def test_candle_mismatch_marks_suspect(self, reliance):
        _tick_at(reliance, 4, 0, price="2500", vol=100)
        _tick_at(reliance, 4, 0, price="2600", vol=200)
        # Store a WRONG 1m candle for that minute: rebuild will disagree.
        Candle.objects.create(
            instrument=reliance,
            symbol="RELIANCE",
            exchange="NSE",
            timeframe="1m",
            open_ts=datetime(2026, 1, 5, 4, 0, tzinfo=UTC),
            open_price=Decimal("1"),
            high_price=Decimal("1"),
            low_price=Decimal("1"),
            close_price=Decimal("1"),
            volume=0,
            trades=0,
            is_complete=True,
            source="yahoo",
        )
        report = build_daily_report(DAY)
        assert report.candle_mismatches >= 1
        assert report.is_suspect
