"""
Tests for core.types — Decimal-safe money handling.

The production plan forbids floats for money anywhere. These tests prove that
Money and the pydantic market-data models actively reject float input rather
than silently corrupting values.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.types import CandleData, Money, SignalData, TickData


class TestMoneyFromValue:
    def test_accepts_decimal_and_quantizes(self):
        assert Money.from_value(Decimal("10.5")) == Decimal("10.5000")

    def test_accepts_string(self):
        assert Money.from_value("1234.56789") == Decimal("1234.5679")

    def test_accepts_int(self):
        assert Money.from_value(1000) == Decimal("1000.0000")

    def test_rejects_float(self):
        """A float must never be silently accepted for a money value."""
        with pytest.raises(TypeError, match="Float values are forbidden"):
            Money.from_value(10.5)

    def test_rejects_garbage_string(self):
        with pytest.raises(ValueError, match="Cannot convert"):
            Money.from_value("not-a-number")

    def test_rejects_unsupported_type(self):
        with pytest.raises(TypeError, match="Unsupported type"):
            Money.from_value([1, 2, 3])

    def test_rounds_half_up(self):
        """0.00005 rounds away from zero at the 4th decimal place."""
        assert Money.from_value(Decimal("1.00005")) == Decimal("1.0001")

    def test_zero_is_allowed(self):
        assert Money.from_value(0) == Decimal("0.0000")


class TestMoneyValidation:
    def test_validate_positive_accepts_positive(self):
        assert Money.validate_positive(Decimal("5")) == Decimal("5.0000")

    def test_validate_positive_rejects_zero(self):
        with pytest.raises(ValueError, match="must be positive"):
            Money.validate_positive(Decimal("0"), field_name="price")

    def test_validate_positive_rejects_negative(self):
        with pytest.raises(ValueError, match="price must be positive"):
            Money.validate_positive(Decimal("-1"), field_name="price")

    def test_validate_non_negative_accepts_zero(self):
        assert Money.validate_non_negative(Decimal("0")) == Decimal("0.0000")

    def test_validate_non_negative_rejects_negative(self):
        with pytest.raises(ValueError, match="must be non-negative"):
            Money.validate_non_negative(Decimal("-0.01"))


class TestTickData:
    def _tick(self, **overrides):
        payload = {
            "symbol_id": 1,
            "exchange_ts": 1767796200.0,
            "received_ts": 1767796200.2,
            "ltp": Decimal("2500.50"),
            "last_qty": 10,
            "volume_traded": 100000,
        }
        payload.update(overrides)
        return payload

    def test_valid_tick_is_accepted(self):
        tick = TickData(**self._tick())
        assert tick.ltp == Decimal("2500.50")

    def test_float_ltp_is_rejected(self):
        with pytest.raises(ValidationError, match="Float price detected"):
            TickData(**self._tick(ltp=2500.5))

    def test_float_bid_is_rejected(self):
        with pytest.raises(ValidationError, match="Float price detected"):
            TickData(**self._tick(best_bid=2500.0))

    def test_float_ask_is_rejected(self):
        with pytest.raises(ValidationError, match="Float price detected"):
            TickData(**self._tick(best_ask=2501.0))

    def test_numeric_string_price_is_coerced_to_decimal(self):
        tick = TickData(**self._tick(ltp="2500.50"))
        assert tick.ltp == Decimal("2500.50")

    def test_optional_prices_may_be_none(self):
        tick = TickData(**self._tick())
        assert tick.best_bid is None
        assert tick.best_ask is None


class TestCandleData:
    def _candle(self, **overrides):
        payload = {
            "symbol_id": 1,
            "timeframe": "1m",
            "open_ts": 1767796200.0,
            "close_ts": 1767796260.0,
            "open": Decimal("2500"),
            "high": Decimal("2510"),
            "low": Decimal("2495"),
            "close": Decimal("2505"),
            "volume": 5000,
        }
        payload.update(overrides)
        return payload

    def test_valid_candle_is_accepted(self):
        candle = CandleData(**self._candle())
        assert candle.is_closed is False
        assert candle.tick_count == 0

    @pytest.mark.parametrize("field", ["open", "high", "low", "close"])
    def test_float_prices_are_rejected(self, field):
        with pytest.raises(ValidationError, match="Float price in candle"):
            CandleData(**self._candle(**{field: 2500.0}))


class TestSignalData:
    def test_defaults(self):
        signal = SignalData(strategy_id="s1", symbol_id=1, direction="BUY")
        assert signal.confidence == Decimal("0.5")
        assert signal.model_version is None

    def test_signal_carries_model_version(self):
        """Plan: every signal must record which model version produced it."""
        signal = SignalData(
            strategy_id="ml-rank-v3",
            symbol_id=1,
            direction="SELL",
            model_version="xgb-2026-10-01",
        )
        assert signal.model_version == "xgb-2026-10-01"
