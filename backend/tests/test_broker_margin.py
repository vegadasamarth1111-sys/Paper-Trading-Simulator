"""
Tests for broker.margin + broker.positions — simplified SPAN schedule,
the v1 naked-short block, margin derived-from-truth, and position math
(average price, realized PnL, flips, MTM). Pure logic, no DB.
"""

from decimal import Decimal

import pytest

from broker.margin import NakedShortBlocked, refresh_margin, required_margin
from broker.positions import PositionState, apply_fill, mark_to_market


class TestMarginSchedule:
    def test_equity_delivery_full_value(self):
        need = required_margin(
            quantity=10, price=Decimal("2500"), segment="EQ", product="CNC", side="BUY"
        )
        assert need.margin == Decimal("25000")
        assert "100%" in need.basis

    def test_equity_intraday_fifth(self):
        need = required_margin(
            quantity=10, price=Decimal("2500"), segment="EQ", product="MIS", side="BUY"
        )
        assert need.margin == Decimal("5000.00")

    def test_future_nrml_fraction(self):
        need = required_margin(
            quantity=50, price=Decimal("45000"), segment="FUT", product="NRML", side="BUY"
        )
        assert need.margin == Decimal("270000.00")  # 12%

    def test_option_buy_full_premium(self):
        need = required_margin(
            quantity=75, price=Decimal("120"), segment="OPT", product="NRML", side="BUY"
        )
        assert need.margin == Decimal("9000")

    def test_naked_option_write_blocked(self):
        with pytest.raises(NakedShortBlocked):
            required_margin(
                quantity=75, price=Decimal("120"), segment="OPT", product="NRML", side="SELL"
            )

    def test_bad_inputs_raise(self):
        with pytest.raises(ValueError):
            required_margin(quantity=0, price=Decimal("1"), segment="EQ", product="MIS", side="BUY")
        with pytest.raises(ValueError):
            required_margin(
                quantity=1, price=Decimal("1"), segment="XXX", product="MIS", side="BUY"
            )


class TestRefreshMargin:
    def test_derived_from_open_state(self, account, equity_instrument):
        """Blocked margin always equals positions + resting orders (no drift)."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from core.models import Order, Position

        Position.objects.create(
            account=account,
            instrument=equity_instrument,
            product_type="MIS",
            side="LONG",
            quantity=10,
            average_entry_price=Decimal("2500"),
            opened_at=datetime.now(ZoneInfo("UTC")),
        )
        Order.objects.create(
            client_order_id="margin-test-1",
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="LMT",
            product_type="MIS",
            quantity=10,
            price=Decimal("2400"),
            status="OPN",
        )
        total = refresh_margin(account)
        # position 10x2500 @20% = 5000; order 10x2400 @20% = 4800
        assert total == Decimal("9800.0000")
        account.refresh_from_db()
        assert account.margin_used == Decimal("9800.0000")


class TestApplyFill:
    def test_open_long(self):
        state = PositionState(quantity=0, average_entry_price=Decimal("0"))
        apply_fill(state, side="BUY", quantity=10, price=Decimal("100"))
        assert (state.quantity, state.average_entry_price) == (10, Decimal("100"))

    def test_extend_reweights_average(self):
        state = PositionState(quantity=10, average_entry_price=Decimal("100"))
        apply_fill(state, side="BUY", quantity=10, price=Decimal("120"))
        assert state.quantity == 20
        assert state.average_entry_price == Decimal("110.0000")
        assert state.realized_pnl == 0

    def test_partial_close_realizes(self):
        state = PositionState(quantity=10, average_entry_price=Decimal("100"))
        apply_fill(state, side="SELL", quantity=4, price=Decimal("130"))
        assert state.quantity == 6
        assert state.realized_pnl == Decimal("120.0000")  # (130-100) x 4
        assert state.average_entry_price == Decimal("100")  # open side unchanged

    def test_full_close_resets_average(self):
        state = PositionState(quantity=10, average_entry_price=Decimal("100"))
        apply_fill(state, side="SELL", quantity=10, price=Decimal("90"))
        assert state.quantity == 0
        assert state.realized_pnl == Decimal("-100.0000")

    def test_flip_realizes_then_opens(self):
        state = PositionState(quantity=10, average_entry_price=Decimal("100"))
        apply_fill(state, side="SELL", quantity=15, price=Decimal("110"))
        assert state.quantity == -5
        assert state.realized_pnl == Decimal("100.0000")  # (110-100) x 10
        assert state.average_entry_price == Decimal("110")

    def test_short_cover_math(self):
        state = PositionState(quantity=-10, average_entry_price=Decimal("100"))
        apply_fill(state, side="BUY", quantity=4, price=Decimal("90"))
        assert state.quantity == -6
        assert state.realized_pnl == Decimal("40.0000")  # (100-90) x 4

    def test_mtm(self):
        state = PositionState(quantity=10, average_entry_price=Decimal("100"))
        assert mark_to_market(state, Decimal("110")) == Decimal("100.0000")
        state.quantity = 0
        assert mark_to_market(state, Decimal("999")) == Decimal("0.0000")

    def test_money_never_float(self):
        state = PositionState(quantity=5, average_entry_price=Decimal("100.05"))
        apply_fill(state, side="BUY", quantity=5, price=Decimal("100.10"))
        assert isinstance(state.average_entry_price, Decimal)
        assert isinstance(state.realized_pnl, Decimal)
