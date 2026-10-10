"""
Tests for broker.costs — Zerodha-style charge math (plan section 5.4).

Four trades are hand-computed to the 4th decimal in the test bodies (an
independent check of the formulas, not just internal consistency). The
20-trade golden table then pins every leg of the `verify_costs` checklist
so a rate/formula regression cannot slip past CI; matching those numbers
to the broker's online calculator remains the human half of the exit gate.
"""

from decimal import Decimal

import pytest

from broker.costs import CostRates, calculate
from broker.management.commands.verify_costs import SAMPLES

R = CostRates()


class TestHandComputed:
    """Independent arithmetic. If these fail, the formula is wrong, not the test."""

    def test_delivery_buy(self):
        b = calculate(
            side="BUY", quantity=10, price=Decimal("2500"), segment="EQ", product="CNC", rates=R
        )
        assert b.brokerage == Decimal("0.0000")  # delivery free
        assert b.stt == Decimal("25.0000")  # 0.1% both sides
        assert b.exchange_charges == Decimal("0.7425")
        assert b.sebi_charges == Decimal("0.0250")
        assert b.gst == Decimal("0.1382")  # 18% of (0 + 0.7425 + 0.025)
        assert b.stamp_duty == Decimal("3.7500")  # 0.015% buy
        assert b.dp_charges == Decimal("0.0000")  # buys never pay DP
        assert b.total == Decimal("29.6557")

    def test_intraday_sell(self):
        b = calculate(
            side="SELL", quantity=100, price=Decimal("151"), segment="EQ", product="MIS", rates=R
        )
        assert b.brokerage == Decimal("4.5300")  # min(0.03% x 15100, 20)
        assert b.stt == Decimal("3.7750")  # 0.025% sell only
        assert b.exchange_charges == Decimal("0.4485")
        assert b.sebi_charges == Decimal("0.0151")
        assert b.gst == Decimal("0.8988")
        assert b.stamp_duty == Decimal("0.0000")  # sells never pay stamp
        assert b.total == Decimal("9.6674")

    def test_option_buy(self):
        b = calculate(
            side="BUY", quantity=50, price=Decimal("120"), segment="OPT", product="NRML", rates=R
        )
        assert b.brokerage == Decimal("1.8000")
        assert b.stt == Decimal("0.0000")  # STT on sell only
        assert b.exchange_charges == Decimal("2.1018")  # on premium
        assert b.sebi_charges == Decimal("0.0060")
        assert b.gst == Decimal("0.7034")
        assert b.stamp_duty == Decimal("0.1200")
        assert b.total == Decimal("4.7312")

    def test_delivery_sell_pays_dp(self):
        b = calculate(
            side="SELL", quantity=10, price=Decimal("2510"), segment="EQ", product="CNC", rates=R
        )
        assert b.brokerage == Decimal("0.0000")
        assert b.stt == Decimal("25.1000")
        assert b.dp_charges == Decimal("15.3400")
        assert b.total == Decimal("41.3493")

    def test_brokerage_cap(self):
        b = calculate(
            side="BUY", quantity=1000, price=Decimal("5000"), segment="FUT", product="NRML", rates=R
        )
        assert b.brokerage == Decimal("20.0000")  # 0.03% would be 1500; capped

    def test_exercise_has_no_brokerage(self):
        b = calculate(
            side="SELL",
            quantity=75,
            price=Decimal("100"),
            segment="OPT",
            product="NRML",
            rates=R,
            is_exercise=True,
        )
        assert b.brokerage == Decimal("0.0000")
        assert b.stt == Decimal("9.3750")  # 0.125% of 7500 intrinsic


class TestInvariants:
    """Properties that must hold for every row of the 20-trade table."""

    @pytest.mark.parametrize("sample", SAMPLES, ids=[s[5] for s in SAMPLES])
    def test_total_is_sum_of_legs(self, sample):
        side, qty, price, seg, prod, _ = sample
        b = calculate(
            side=side, quantity=qty, price=Decimal(price), segment=seg, product=prod, rates=R
        )
        parts = (
            b.brokerage
            + b.stt
            + b.exchange_charges
            + b.sebi_charges
            + b.gst
            + b.stamp_duty
            + b.dp_charges
        )
        assert b.total == parts.quantize(Decimal("0.0001"))

    @pytest.mark.parametrize("sample", SAMPLES, ids=[s[5] for s in SAMPLES])
    def test_no_negative_legs(self, sample):
        side, qty, price, seg, prod, _ = sample
        b = calculate(
            side=side, quantity=qty, price=Decimal(price), segment=seg, product=prod, rates=R
        )
        for leg in (
            b.brokerage,
            b.stt,
            b.exchange_charges,
            b.sebi_charges,
            b.gst,
            b.stamp_duty,
            b.dp_charges,
        ):
            assert leg >= 0

    def test_stt_only_on_sell_for_intraday(self):
        buy = calculate(
            side="BUY", quantity=10, price=Decimal("100"), segment="EQ", product="MIS", rates=R
        )
        sell = calculate(
            side="SELL", quantity=10, price=Decimal("100"), segment="EQ", product="MIS", rates=R
        )
        assert buy.stt == 0
        assert sell.stt > 0

    def test_dp_only_on_delivery_sell(self):
        buy = calculate(
            side="BUY", quantity=10, price=Decimal("100"), segment="EQ", product="CNC", rates=R
        )
        intra = calculate(
            side="SELL", quantity=10, price=Decimal("100"), segment="EQ", product="MIS", rates=R
        )
        assert buy.dp_charges == 0
        assert intra.dp_charges == 0

    def test_invalid_inputs_raise(self):
        with pytest.raises(ValueError):
            calculate(side="HOLD", quantity=1, price=Decimal("1"), segment="EQ", product="MIS")
        with pytest.raises(ValueError):
            calculate(side="BUY", quantity=0, price=Decimal("1"), segment="EQ", product="MIS")
        with pytest.raises(ValueError):
            calculate(side="BUY", quantity=1, price=Decimal("0"), segment="EQ", product="MIS")

    def test_money_never_float(self):
        b = calculate(
            side="BUY", quantity=1, price=Decimal("100"), segment="EQ", product="CNC", rates=R
        )
        for leg in (
            b.brokerage,
            b.stt,
            b.exchange_charges,
            b.sebi_charges,
            b.gst,
            b.stamp_duty,
            b.dp_charges,
            b.total,
        ):
            assert isinstance(leg, Decimal)
