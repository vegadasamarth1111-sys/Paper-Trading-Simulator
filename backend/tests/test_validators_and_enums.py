"""
Tests for core.validators and core.enums (the order state machine).
"""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from core.enums import (
    Exchange,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    Segment,
)
from core.validators import (
    validate_lot_size_multiple,
    validate_no_float,
    validate_non_negative_decimal,
    validate_percentage,
    validate_positive_decimal,
    validate_price_tick_size,
)


class TestDecimalValidators:
    def test_positive_accepts_positive(self):
        validate_positive_decimal(Decimal("0.01"))

    @pytest.mark.parametrize("value", [Decimal("0"), Decimal("-1")])
    def test_positive_rejects_zero_and_negative(self, value):
        with pytest.raises(ValidationError, match="must be positive"):
            validate_positive_decimal(value)

    def test_positive_rejects_garbage(self):
        with pytest.raises(ValidationError, match="Invalid decimal value"):
            validate_positive_decimal("abc")

    def test_non_negative_accepts_zero(self):
        validate_non_negative_decimal(Decimal("0"))

    def test_non_negative_rejects_negative(self):
        with pytest.raises(ValidationError, match="must be non-negative"):
            validate_non_negative_decimal(Decimal("-0.5"))


class TestPercentageValidator:
    @pytest.mark.parametrize("value", ["0", "50", "100", Decimal(25)])
    def test_in_range_is_accepted(self, value):
        validate_percentage(value)

    @pytest.mark.parametrize("value", ["-0.01", "100.01", "1000"])
    def test_out_of_range_is_rejected(self, value):
        with pytest.raises(ValidationError, match="between 0 and 100"):
            validate_percentage(value)

    def test_garbage_is_rejected(self):
        with pytest.raises(ValidationError, match="Invalid percentage"):
            validate_percentage("nope")


class TestTradingValidators:
    def test_lot_size_multiple_accepted(self):
        validate_lot_size_multiple(150, 75)

    def test_lot_size_multiple_rejected(self):
        """Plan: option order quantity must be a multiple of the lot size."""
        with pytest.raises(ValidationError, match="must be a multiple of lot size"):
            validate_lot_size_multiple(100, 75)

    def test_lot_size_zero_or_none_skips_check(self):
        validate_lot_size_multiple(3, 0)
        validate_lot_size_multiple(3, None)

    def test_tick_size_aligned(self):
        validate_price_tick_size(Decimal("2500.05"), Decimal("0.05"))

    def test_tick_size_misaligned(self):
        with pytest.raises(ValidationError, match="must be a multiple of tick size"):
            validate_price_tick_size(Decimal("2500.03"), Decimal("0.05"))

    def test_tick_size_zero_skips_check(self):
        validate_price_tick_size(Decimal("2500.03"), Decimal("0"))
        validate_price_tick_size(Decimal("2500.03"), None)


class TestNoFloatValidator:
    def test_rejects_float(self):
        """Money fields must never accept a Python float."""
        with pytest.raises(ValidationError, match="Float values are forbidden"):
            validate_no_float(10.5)

    @pytest.mark.parametrize("value", [Decimal("10.5"), 10, "10.5"])
    def test_accepts_non_float(self, value):
        validate_no_float(value)


class TestOrderStatusStateMachine:
    def test_terminal_states(self):
        assert OrderStatus.terminal_states() == {
            OrderStatus.FILLED,
            OrderStatus.REJECTED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
        }

    def test_happy_path_transitions(self):
        """NEW -> VALIDATED -> OPEN -> PARTIALLY_FILLED -> FILLED"""
        allowed = OrderStatus.valid_transitions()
        assert OrderStatus.VALIDATED in allowed[OrderStatus.NEW]
        assert OrderStatus.OPEN in allowed[OrderStatus.VALIDATED]
        assert OrderStatus.PARTIALLY_FILLED in allowed[OrderStatus.OPEN]
        assert OrderStatus.FILLED in allowed[OrderStatus.PARTIALLY_FILLED]

    def test_rejection_allowed_from_new_and_validated(self):
        allowed = OrderStatus.valid_transitions()
        assert OrderStatus.REJECTED in allowed[OrderStatus.NEW]
        assert OrderStatus.REJECTED in allowed[OrderStatus.VALIDATED]

    def test_terminal_states_have_no_outgoing_transitions(self):
        allowed = OrderStatus.valid_transitions()
        for state in OrderStatus.terminal_states():
            assert state not in allowed, f"{state} must have no allowed transitions"

    def test_open_cannot_jump_straight_to_filled_from_new(self):
        allowed = OrderStatus.valid_transitions()
        assert OrderStatus.FILLED not in allowed[OrderStatus.NEW]


class TestEnumValues:
    def test_exchange_values(self):
        assert {Exchange.NSE.value, Exchange.BSE.value} == {"NSE", "BSE"}

    def test_segment_values(self):
        assert set(Segment.values) == {"EQ", "FUT", "OPT", "IDX"}

    def test_order_type_values(self):
        assert set(OrderType.values) == {"MKT", "LMT", "SL", "SLM"}

    def test_order_side_values(self):
        assert set(OrderSide.values) == {"BUY", "SELL"}

    def test_product_type_values(self):
        assert set(ProductType.values) == {"CNC", "MIS", "NRML"}
