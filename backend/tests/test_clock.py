"""
Tests for core.clock — the single source of time for the trading system.

These cover the two invariants the production plan depends on:
  1. TradingCalendar correctly decides open/closed using IST, weekends and holidays.
  2. Clock returns real time only in LIVE mode, and refuses to run backwards in REPLAY.
"""

from datetime import date, datetime, timedelta

import pytest

from core.clock import IST, UTC, Clock, ClockMode, TradingCalendar

pytestmark = pytest.mark.django_db

# A known Wednesday (2026-01-07). Jan 1 2026 is a Thursday.
WEDNESDAY = date(2026, 1, 7)
SATURDAY = date(2026, 1, 3)
SUNDAY = date(2026, 1, 4)


def utc(dt: datetime) -> datetime:
    """Mark a naive datetime as UTC so it round-trips through _to_ist."""
    return dt.replace(tzinfo=UTC)


def test_ist_offset_is_exactly_five_and_a_half_hours():
    """
    Regression guard: IST must be +05:30, not Local Mean Time (+05:53).

    This is the failure mode you get if anyone reintroduces pytz and uses it
    as `tzinfo=` directly. A 23-minute skew would silently mis-classify whether
    the market is open, so it is worth pinning down.
    """
    naive = datetime(2026, 1, 7, 0, 0)
    assert naive.replace(tzinfo=IST).utcoffset() == timedelta(hours=5, minutes=30)
    assert naive.replace(tzinfo=UTC).utcoffset() == timedelta(0)


class TestTradingCalendar:
    def test_weekday_is_a_trading_day(self):
        cal = TradingCalendar()
        assert cal.is_trading_day(utc(datetime(2026, 1, 7, 6, 0))) is True

    @pytest.mark.parametrize("d", [SATURDAY, SUNDAY])
    def test_weekend_is_not_a_trading_day(self, d):
        cal = TradingCalendar()
        assert cal.is_trading_day(utc(datetime(d.year, d.month, d.day, 6, 0))) is False

    def test_holiday_is_not_a_trading_day(self):
        # 2026-01-26 is a Monday (Republic Day)
        cal = TradingCalendar(holidays={date(2026, 1, 26)})
        assert cal.is_trading_day(utc(datetime(2026, 1, 26, 6, 0))) is False

    def test_holiday_on_a_weekend_is_still_not_a_trading_day(self):
        cal = TradingCalendar(holidays={SATURDAY})
        assert cal.is_trading_day(utc(datetime(2026, 1, 3, 6, 0))) is False

    def test_update_holidays_converts_datetimes_to_dates(self):
        cal = TradingCalendar()
        cal.update_holidays({datetime(2026, 1, 26, 15, 30)})
        assert cal.is_trading_day(utc(datetime(2026, 1, 26, 6, 0))) is False

    def test_update_holidays_can_clear_the_list(self):
        cal = TradingCalendar(holidays={date(2026, 1, 26)})
        cal.update_holidays(set())
        assert cal.is_trading_day(utc(datetime(2026, 1, 26, 6, 0))) is True

    @pytest.mark.parametrize(
        "hour,minute,is_open",
        [
            # IST is UTC+05:30. 09:15 IST == 03:45 UTC, 15:30 IST == 10:00 UTC.
            (3, 44, False),  # 09:14 IST — one minute before open
            (3, 45, True),  # 09:15 IST — open (inclusive)
            (10, 0, True),  # 15:30 IST — close (inclusive)
            (10, 1, False),  # 15:31 IST — after close
        ],
    )
    def test_market_open_boundaries_in_utc(self, hour, minute, is_open):
        cal = TradingCalendar()
        dt = utc(datetime(WEDNESDAY.year, WEDNESDAY.month, WEDNESDAY.day, hour, minute))
        assert cal.is_market_open(dt) is is_open

    def test_market_closed_on_weekend_even_during_hours(self):
        cal = TradingCalendar()
        # Saturday 12:00 IST == 06:30 UTC
        assert cal.is_market_open(utc(datetime(2026, 1, 3, 6, 30))) is False

    def test_pre_open_window(self):
        cal = TradingCalendar()
        # 09:00 IST == 03:30 UTC -> pre-open
        assert cal.is_pre_open(utc(datetime(2026, 1, 7, 3, 30))) is True
        # 09:15 IST == 03:45 UTC -> pre-open ends exactly at open
        assert cal.is_pre_open(utc(datetime(2026, 1, 7, 3, 45))) is False

    def test_squareoff_window(self):
        cal = TradingCalendar()
        # 15:17 IST == 09:47 UTC -> inside 15:15-15:20
        assert cal.is_squareoff_window(utc(datetime(2026, 1, 7, 9, 47))) is True
        # 15:21 IST == 09:51 UTC -> outside
        assert cal.is_squareoff_window(utc(datetime(2026, 1, 7, 9, 51))) is False

    def test_squareoff_window_closed_on_weekend(self):
        cal = TradingCalendar()
        assert cal.is_squareoff_window(utc(datetime(2026, 1, 3, 9, 47))) is False

    def test_next_market_open_before_open_same_day(self):
        cal = TradingCalendar()
        # 07:00 IST on Wednesday -> that same day at 09:15 IST
        result = cal.next_market_open(utc(datetime(2026, 1, 7, 1, 30)))
        assert result.astimezone(IST).date() == WEDNESDAY
        assert result.astimezone(IST).hour == 9
        assert result.astimezone(IST).minute == 15

    def test_next_market_open_after_open_rolls_to_next_day(self):
        cal = TradingCalendar()
        # 16:00 IST Wednesday -> Thursday 09:15 IST
        result = cal.next_market_open(utc(datetime(2026, 1, 7, 10, 30)))
        assert result.astimezone(IST).date() == date(2026, 1, 8)

    def test_next_market_open_skips_weekend(self):
        cal = TradingCalendar()
        # Saturday 10:00 IST -> Monday 09:15 IST
        result = cal.next_market_open(utc(datetime(2026, 1, 3, 4, 30)))
        assert result.astimezone(IST).date() == date(2026, 1, 5)

    def test_next_market_open_skips_holiday(self):
        cal = TradingCalendar(holidays={date(2026, 1, 8)})
        result = cal.next_market_open(utc(datetime(2026, 1, 7, 10, 30)))
        assert result.astimezone(IST).date() == date(2026, 1, 9)

    def test_naive_datetime_is_treated_as_utc(self):
        cal = TradingCalendar()
        # Naive 04:00 == 09:30 IST -> open
        assert cal.is_market_open(datetime(2026, 1, 7, 4, 0)) is True


class TestClock:
    def test_live_mode_returns_aware_utc_time(self):
        clock = Clock(mode=ClockMode.LIVE)
        now = clock.now()
        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(0)

    def test_live_clock_is_monotonic(self):
        clock = Clock(mode=ClockMode.LIVE)
        first = clock.now()
        second = clock.now()
        assert second >= first

    def test_set_time_is_rejected_in_live_mode(self):
        clock = Clock(mode=ClockMode.LIVE)
        with pytest.raises(RuntimeError, match="Cannot set time in LIVE mode"):
            clock.set_time(datetime(2026, 1, 7, 4, 0))

    def test_advance_is_rejected_in_live_mode(self):
        clock = Clock(mode=ClockMode.LIVE)
        with pytest.raises(RuntimeError, match="Cannot advance time in LIVE mode"):
            clock.advance(timedelta(minutes=1))

    def test_replay_mode_without_time_raises(self):
        clock = Clock(mode=ClockMode.REPLAY)
        with pytest.raises(RuntimeError, match="no simulated time has been set"):
            clock.now()

    def test_replay_mode_set_time_returns_that_time(self):
        clock = Clock(mode=ClockMode.REPLAY)
        target = datetime(2026, 1, 7, 4, 0, tzinfo=UTC)
        clock.set_time(target)
        assert clock.now() == target

    def test_replay_mode_naive_input_is_assumed_utc(self):
        clock = Clock(mode=ClockMode.REPLAY)
        clock.set_time(datetime(2026, 1, 7, 4, 0))
        assert clock.now() == datetime(2026, 1, 7, 4, 0, tzinfo=UTC)

    def test_replay_mode_converts_other_timezones_to_utc(self):
        clock = Clock(mode=ClockMode.REPLAY)
        ist_time = datetime(2026, 1, 7, 9, 30, tzinfo=IST)
        clock.set_time(ist_time)
        assert clock.now() == datetime(2026, 1, 7, 4, 0, tzinfo=UTC)

    def test_replay_mode_rejects_moving_backwards(self):
        clock = Clock(mode=ClockMode.REPLAY)
        clock.set_time(datetime(2026, 1, 7, 4, 0, tzinfo=UTC))
        with pytest.raises(ValueError, match="cannot move backwards"):
            clock.set_time(datetime(2026, 1, 7, 3, 59, tzinfo=UTC))

    def test_replay_mode_allows_moving_forward(self):
        clock = Clock(mode=ClockMode.REPLAY)
        clock.set_time(datetime(2026, 1, 7, 4, 0, tzinfo=UTC))
        clock.set_time(datetime(2026, 1, 7, 4, 1, tzinfo=UTC))
        assert clock.now() == datetime(2026, 1, 7, 4, 1, tzinfo=UTC)

    def test_backtest_mode_allows_arbitrary_movement(self):
        clock = Clock(mode=ClockMode.BACKTEST)
        clock.set_time(datetime(2026, 1, 7, 4, 0, tzinfo=UTC))
        clock.set_time(datetime(2026, 1, 7, 3, 0, tzinfo=UTC))
        assert clock.now() == datetime(2026, 1, 7, 3, 0, tzinfo=UTC)

    def test_advance_moves_time_forward_and_returns_new_time(self):
        clock = Clock(mode=ClockMode.BACKTEST)
        start = datetime(2026, 1, 7, 4, 0, tzinfo=UTC)
        clock.set_time(start)
        result = clock.advance(timedelta(minutes=1))
        assert result == start + timedelta(minutes=1)
        assert clock.now() == result

    def test_advance_without_start_time_raises(self):
        clock = Clock(mode=ClockMode.BACKTEST)
        with pytest.raises(RuntimeError, match="Set an initial time before advancing"):
            clock.advance(timedelta(minutes=1))

    def test_mode_and_calendar_are_exposed(self):
        cal = TradingCalendar()
        clock = Clock(mode=ClockMode.REPLAY, calendar=cal)
        assert clock.mode == ClockMode.REPLAY
        assert clock.calendar is cal

    def test_default_calendar_is_created_when_omitted(self):
        clock = Clock(mode=ClockMode.LIVE)
        assert isinstance(clock.calendar, TradingCalendar)

    def test_market_helpers_delegate_to_calendar(self):
        # 2026-01-07 04:00 UTC == 09:30 IST on a Wednesday
        clock = Clock(mode=ClockMode.REPLAY)
        clock.set_time(datetime(2026, 1, 7, 4, 0, tzinfo=UTC))
        assert clock.is_trading_day() is True
        assert clock.is_market_open() is True
        assert clock.is_pre_open() is False
        assert clock.is_squareoff_window() is False

    def test_to_ist_converts_for_display_only(self):
        clock = Clock(mode=ClockMode.REPLAY)
        clock.set_time(datetime(2026, 1, 7, 4, 0, tzinfo=UTC))
        ist_now = clock.to_ist()
        assert ist_now.hour == 9 and ist_now.minute == 30

    def test_repr_does_not_raise(self):
        clock = Clock(mode=ClockMode.LIVE)
        assert "Clock(mode=live" in repr(clock)
