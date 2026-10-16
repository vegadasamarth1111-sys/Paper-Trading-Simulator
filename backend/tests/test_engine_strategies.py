"""
Tests for engine strategies + sizing (Phase 4, plan section 7).

Baselines are deliberately naive; these tests pin their exact behavior
so pipeline work never silently changes what the strategies decide.
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from engine.base import CandleView, EmittedSignal
from engine.sizing import size_from_signal
from engine.strategies.registry import REGISTRY, get_strategy
from engine.strategies.sma import SMACrossover

UTC = ZoneInfo("UTC")


def _candles(closes: list[str], symbol: str = "RELIANCE") -> list[CandleView]:
    out = []
    for i, close in enumerate(closes):
        px = Decimal(close)
        out.append(
            CandleView(
                symbol=symbol,
                timeframe="1m",
                open_ts=datetime(2026, 1, 5, 4, i, tzinfo=UTC),
                open_price=px,
                high_price=px + Decimal("1"),
                low_price=px - Decimal("1"),
                close_price=px,
                volume=100,
            )
        )
    return out


class TestRegistry:
    def test_known_strategies(self):
        assert set(REGISTRY) == {"sma_cross", "donchian"}
        assert isinstance(get_strategy("sma_cross"), SMACrossover)

    def test_unknown_name_raises(self):
        with pytest.raises(ValueError, match="unknown strategy"):
            get_strategy("nope")

    def test_unknown_param_raises(self):
        with pytest.raises(ValueError, match="unknown params"):
            get_strategy("sma_cross", {"bogus": 1})

    def test_version_stamp(self):
        assert "@4.0.0" in get_strategy("sma_cross").version


class TestSignalValidation:
    def test_bad_direction_raises(self):
        with pytest.raises(ValueError, match="direction"):
            EmittedSignal(symbol="X", direction="HOLD", entry_price=Decimal("1")).validate()

    def test_inverted_stop_raises(self):
        with pytest.raises(ValueError, match="below entry"):
            EmittedSignal(
                symbol="X",
                direction="BUY",
                entry_price=Decimal("100"),
                stop_loss=Decimal("110"),
            ).validate()

    def test_confidence_bounds(self):
        with pytest.raises(ValueError, match="confidence"):
            EmittedSignal(
                symbol="X",
                direction="BUY",
                entry_price=Decimal("100"),
                confidence=Decimal("2"),
            ).validate()


class TestSMACross:
    def test_needs_enough_history(self):
        strat = get_strategy("sma_cross", {"fast": 2, "slow": 3})
        assert strat.on_candle(_candles(["10", "11"])[-1], _candles(["10", "11"])) == []

    def test_cross_up_emits_buy(self):
        strat = get_strategy("sma_cross", {"fast": 2, "slow": 3})
        history = _candles(["10", "10", "10", "12"])
        signals = strat.on_candle(history[-1], history)
        assert len(signals) == 1
        (signal,) = signals
        assert signal.direction == "BUY"
        assert signal.stop_loss < signal.entry_price < signal.target
        assert signal.entry_price == Decimal("12")

    def test_cross_down_emits_sell(self):
        strat = get_strategy("sma_cross", {"fast": 2, "slow": 3})
        history = _candles(["12", "12", "12", "10"])
        (signal,) = strat.on_candle(history[-1], history)
        assert signal.direction == "SELL"
        assert signal.target < signal.entry_price < signal.stop_loss

    def test_no_cross_no_signal(self):
        strat = get_strategy("sma_cross", {"fast": 2, "slow": 3})
        history = _candles(["10", "10", "10", "10"])
        assert strat.on_candle(history[-1], history) == []

    def test_bad_params_rejected(self):
        strat = get_strategy("sma_cross", {"fast": 10, "slow": 5})
        history = _candles(["10"] * 12)
        with pytest.raises(ValueError, match="fast"):
            strat.on_candle(history[-1], history)

    def test_tick_handler_quiet_by_default(self):
        from engine.base import TickView

        strat = get_strategy("sma_cross")
        tick = TickView(
            symbol="X", exchange_ts=datetime.now(UTC), ltp=Decimal("1"), volume_traded=1
        )
        assert strat.on_tick(tick) == []


class TestDonchian:
    def test_breakout_up(self):
        strat = get_strategy("donchian", {"lookback": 3})
        history = _candles(["10", "11", "10", "13"])
        (signal,) = strat.on_candle(history[-1], history)
        assert signal.direction == "BUY"

    def test_breakout_down(self):
        strat = get_strategy("donchian", {"lookback": 3})
        history = _candles(["10", "9", "10", "7"])
        (signal,) = strat.on_candle(history[-1], history)
        assert signal.direction == "SELL"

    def test_inside_channel_silent(self):
        strat = get_strategy("donchian", {"lookback": 3})
        history = _candles(["10", "11", "9", "10"])
        assert strat.on_candle(history[-1], history) == []

    def test_needs_lookback_plus_one(self):
        strat = get_strategy("donchian", {"lookback": 5})
        history = _candles(["10"] * 5)
        assert strat.on_candle(history[-1], history) == []


class TestSizing:
    def test_risk_based_qty(self):
        # 10L x 1% = 10,000 risk / Rs 100 distance = 100 units.
        result = size_from_signal(
            capital=Decimal("1000000"),
            risk_pct=Decimal("0.01"),
            entry_price=Decimal("2500"),
            stop_loss=Decimal("2400"),
            lot_size=1,
        )
        assert not result.skipped
        assert result.quantity == 100
        assert result.risk_amount == Decimal("10000.0000")

    def test_lot_rounding_down(self):
        result = size_from_signal(
            capital=Decimal("1000000"),
            risk_pct=Decimal("0.01"),
            entry_price=Decimal("120"),
            stop_loss=Decimal("110"),
            lot_size=75,
        )
        # 10,000 / 10 = 1000 units -> 13 lots = 975.
        assert result.quantity == 975

    def test_sub_lot_skips(self):
        result = size_from_signal(
            capital=Decimal("10000"),
            risk_pct=Decimal("0.01"),
            entry_price=Decimal("120"),
            stop_loss=Decimal("110"),
            lot_size=75,
        )
        assert result.skipped  # Rs 100 risk buys 10 units < 1 lot

    def test_no_stop_no_size(self):
        result = size_from_signal(
            capital=Decimal("1000000"),
            risk_pct=Decimal("0.01"),
            entry_price=Decimal("100"),
            stop_loss=None,
            lot_size=1,
        )
        assert result.skipped

    def test_notional_cap(self):
        result = size_from_signal(
            capital=Decimal("1000000"),
            risk_pct=Decimal("0.50"),
            entry_price=Decimal("2500"),
            stop_loss=Decimal("2400"),
            lot_size=1,
            max_notional=Decimal("100000"),
        )
        assert result.notional <= Decimal("100000")

    def test_bad_inputs_skip(self):
        assert size_from_signal(
            capital=Decimal("0"),
            risk_pct=Decimal("0.01"),
            entry_price=Decimal("100"),
            stop_loss=Decimal("90"),
            lot_size=1,
        ).skipped
