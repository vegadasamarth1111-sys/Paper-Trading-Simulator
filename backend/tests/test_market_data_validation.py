"""
Tests for market_data.validation — every rule in plan section 4.4.

Each test names the rule it proves. Rejections must carry the exact
TickRejectReason code so bad_ticks rows and the quality report stay
explainable. The extreme-move rule MUST flag, never reject.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from core.clock import TradingCalendar
from core.enums import TickRejectReason
from market_data.schema import NormalizedTick
from market_data.validation import ValidationThresholds, validate_tick

UTC = ZoneInfo("UTC")
IST = ZoneInfo("Asia/Kolkata")

# Monday 2026-01-05 10:00 IST (a trading day with no holidays configured).
MARKET_TS = datetime(2026, 1, 5, 4, 30, tzinfo=UTC)
CAL = TradingCalendar(holidays=set())


def _tick(**overrides):
    base = {
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "exchange_ts": MARKET_TS,
        "received_ts": MARKET_TS,
        "ltp": Decimal("2500.00"),
        "last_qty": 10,
        "volume_traded": 100000,
        "source": "yahoo",
    }
    base.update(overrides)
    return NormalizedTick(**base)


def _ctx(**overrides):
    ctx = {
        "upper_circuit": Decimal("2700.00"),
        "lower_circuit": Decimal("2300.00"),
        "prev_ltp": Decimal("2499.00"),
        "prev_volume": 99000,
        "prev_sequence_no": 41,
        "prev_exchange_ts": MARKET_TS - timedelta(minutes=1),
        "calendar": CAL,
        "now_utc": MARKET_TS,
    }
    ctx.update(overrides)
    return ctx


class TestAcceptsGoodTicks:
    def test_clean_tick_accepted(self):
        result = validate_tick(_tick(), **_ctx())
        assert result.accepted and result.reason is None

    def test_first_tick_without_history_accepted(self):
        ctx = _ctx(prev_ltp=None, prev_volume=None, prev_sequence_no=None, prev_exchange_ts=None)
        assert validate_tick(_tick(), **ctx).accepted

    def test_pre_open_tick_accepted_as_own_session(self):
        pre_open = datetime(2026, 1, 5, 3, 35, tzinfo=UTC)  # 09:05 IST
        tick = _tick(exchange_ts=pre_open, received_ts=pre_open)
        result = validate_tick(tick, **_ctx(now_utc=pre_open, prev_exchange_ts=None))
        assert result.accepted


class TestRejections:
    def test_negative_price_rejected(self):
        # Schema blocks <=0 first; validator keeps its own guard for raw paths.
        with pytest.raises(ValueError, match="greater than 0"):
            _tick(ltp=Decimal("-1"))

    def test_circuit_breach_rejected(self):
        result = validate_tick(_tick(ltp=Decimal("2800")), **_ctx())
        assert not result.accepted
        assert result.reason == TickRejectReason.CIRCUIT_BREACH

    def test_lower_circuit_rejected(self):
        result = validate_tick(_tick(ltp=Decimal("2200")), **_ctx())
        assert not result.accepted
        assert result.reason == TickRejectReason.CIRCUIT_BREACH

    def test_future_timestamp_rejected(self):
        future = MARKET_TS + timedelta(minutes=5)
        tick = _tick(exchange_ts=future, received_ts=future)
        result = validate_tick(tick, **_ctx(now_utc=MARKET_TS))
        assert not result.accepted
        assert result.reason == TickRejectReason.FUTURE_TIMESTAMP

    def test_stale_timestamp_rejected(self):
        old = MARKET_TS - timedelta(hours=2)
        tick = _tick(exchange_ts=old, received_ts=MARKET_TS)
        result = validate_tick(tick, **_ctx())
        assert not result.accepted
        assert result.reason == TickRejectReason.STALE_TIMESTAMP

    def test_outside_market_hours_rejected(self):
        night = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)  # 19:30 IST
        tick = _tick(exchange_ts=night, received_ts=night)
        result = validate_tick(tick, **_ctx(now_utc=night))
        assert not result.accepted
        assert result.reason == TickRejectReason.OUTSIDE_MARKET_HOURS

    def test_weekend_rejected(self):
        saturday = datetime(2026, 1, 10, 4, 30, tzinfo=UTC)
        tick = _tick(exchange_ts=saturday, received_ts=saturday)
        result = validate_tick(tick, **_ctx(now_utc=saturday))
        assert not result.accepted
        assert result.reason == TickRejectReason.OUTSIDE_MARKET_HOURS

    def test_volume_decrease_rejected(self):
        result = validate_tick(_tick(volume_traded=500), **_ctx(prev_volume=1000))
        assert not result.accepted
        assert result.reason == TickRejectReason.VOLUME_DECREASE

    def test_duplicate_sequence_rejected(self):
        tick = _tick(sequence_no=41)
        result = validate_tick(tick, **_ctx(prev_sequence_no=41))
        assert not result.accepted
        assert result.reason == TickRejectReason.DUPLICATE_TICK

    def test_out_of_order_rejected(self):
        tick = _tick(exchange_ts=MARKET_TS - timedelta(minutes=5))
        result = validate_tick(tick, **_ctx())
        assert not result.accepted
        assert result.reason == TickRejectReason.OUT_OF_ORDER

    def test_same_ts_without_sequence_is_duplicate(self):
        result = validate_tick(_tick(), **_ctx(prev_exchange_ts=MARKET_TS))
        assert not result.accepted
        assert result.reason == TickRejectReason.DUPLICATE_TICK

    def test_bid_above_ask_rejected(self):
        tick = _tick(best_bid=Decimal("2501"), best_ask=Decimal("2500"))
        result = validate_tick(tick, **_ctx())
        assert not result.accepted
        assert result.reason == TickRejectReason.INVALID_SPREAD


class TestFlagNotReject:
    def test_extreme_move_flagged_but_accepted(self):
        """A real circuit-day move must survive validation (flagged, kept)."""
        tick = _tick(ltp=Decimal("2750.00"))  # +10.04% in one tick
        thresholds = ValidationThresholds(extreme_move_pct=Decimal("0.10"))
        result = validate_tick(
            tick,
            **_ctx(
                upper_circuit=Decimal("3000"),
                thresholds=thresholds,
            ),
        )
        assert result.accepted
        assert result.flag_only
        assert result.reason == TickRejectReason.EXTREME_MOVE

    def test_small_move_not_flagged(self):
        result = validate_tick(_tick(), **_ctx())
        assert result.accepted and not result.flag_only
