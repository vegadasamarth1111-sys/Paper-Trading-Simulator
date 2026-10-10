"""
Tests for broker.fills — every rule in plan section 5.3.

Market fills at bid/ask (never LTP), conservative limits need
trade-through, stops trigger on LTP cross, depth caps partial fills,
circuits halt, illiquid names reject. Each test names its rule.
"""

from decimal import Decimal

from broker.fills import (
    FillConfig,
    MarketSnapshot,
    fill_limit,
    fill_market,
    fill_stop,
    stop_triggered,
)

STD = FillConfig()


def _snap(**overrides):
    base = {
        "bid": Decimal("2499"),
        "ask": Decimal("2501"),
        "ltp": Decimal("2500"),
        "bid_qty": 100,
        "ask_qty": 100,
        "volume": 50000,
    }
    base.update(overrides)
    return MarketSnapshot(**base)


class TestMarketFills:
    def test_buy_fills_at_ask_not_ltp(self):
        r = fill_market(side="BUY", quantity=10, snapshot=_snap(), config=STD)
        # ask 2501 + 2bps slippage (0.5002) = 2501.5002
        assert r.fill_price == Decimal("2501.5002")
        assert r.filled_qty == 10
        assert not r.rejected

    def test_sell_fills_at_bid(self):
        r = fill_market(side="SELL", quantity=10, snapshot=_snap(), config=STD)
        assert r.fill_price == Decimal("2498.5002")  # bid 2499 - 2bps (0.4998)
        assert r.filled_qty == 10

    def test_partial_fill_capped_by_depth(self):
        snap = _snap(ask_qty=10)  # 50% of 10 = 5 per tick
        r = fill_market(side="BUY", quantity=100, snapshot=snap, config=STD)
        assert r.filled_qty == 5
        assert not r.rejected

    def test_no_quote_rejects(self):
        r = fill_market(side="BUY", quantity=1, snapshot=_snap(ask=Decimal("0")), config=STD)
        assert r.rejected

    def test_circuit_lock_halts(self):
        snap = _snap(
            bid=Decimal("2750"),
            ask=Decimal("2750"),
            ltp=Decimal("2750"),
            upper_circuit=Decimal("2750"),
            lower_circuit=Decimal("2250"),
        )
        r = fill_market(side="BUY", quantity=1, snapshot=snap, config=STD)
        assert r.rejected
        assert "circuit" in r.reject_reason

    def test_illiquid_rejects(self):
        config = FillConfig(min_bid_qty=1000, min_volume=100000)
        r = fill_market(side="BUY", quantity=1, snapshot=_snap(), config=config)
        assert r.rejected
        assert "illiquid" in r.reject_reason


class TestLimitFills:
    def test_conservative_needs_trade_through(self):
        # ask == limit is a touch, not a trade-through: no fill.
        r = fill_limit(side="BUY", quantity=10, limit=Decimal("2501"), snapshot=_snap(), config=STD)
        assert r.filled_qty == 0
        assert not r.rejected

    def test_touch_mode_fills_on_touch(self):
        config = FillConfig(conservative_limit=False)
        r = fill_limit(
            side="BUY", quantity=10, limit=Decimal("2501"), snapshot=_snap(), config=config
        )
        assert r.filled_qty == 10
        assert r.fill_price == Decimal("2501.0000")  # filled AT the limit

    def test_trade_through_fills_at_limit(self):
        r = fill_limit(side="BUY", quantity=10, limit=Decimal("2505"), snapshot=_snap(), config=STD)
        assert r.filled_qty == 10
        assert r.fill_price == Decimal("2505.0000")

    def test_sell_limit_mirror(self):
        r = fill_limit(
            side="SELL", quantity=10, limit=Decimal("2490"), snapshot=_snap(), config=STD
        )
        assert r.filled_qty == 10  # bid 2499 > 2490: traded through
        r2 = fill_limit(
            side="SELL", quantity=10, limit=Decimal("2499"), snapshot=_snap(), config=STD
        )
        assert r2.filled_qty == 0  # touch only in conservative mode


class TestStops:
    def test_untriggered_stop_rests(self):
        r = fill_stop(
            side="BUY",
            quantity=10,
            trigger=Decimal("2600"),
            limit=None,
            snapshot=_snap(),
            config=STD,
        )
        assert r.filled_qty == 0
        assert not r.trigger_hit

    def test_slm_becomes_market_on_trigger(self):
        snap = _snap(ltp=Decimal("2601"))
        r = fill_stop(
            side="BUY", quantity=10, trigger=Decimal("2600"), limit=None, snapshot=snap, config=STD
        )
        assert r.trigger_hit
        assert r.filled_qty == 10
        assert r.fill_price == Decimal("2501.5002")  # market at ask + slippage

    def test_sl_uses_limit_after_trigger(self):
        snap = _snap(ltp=Decimal("2601"))
        r = fill_stop(
            side="BUY",
            quantity=10,
            trigger=Decimal("2600"),
            limit=Decimal("2605"),
            snapshot=snap,
            config=STD,
        )
        assert r.trigger_hit
        assert r.fill_price == Decimal("2605.0000")

    def test_sell_stop_triggers_downward(self):
        assert stop_triggered(
            side="SELL", trigger=Decimal("2490"), snapshot=_snap(ltp=Decimal("2489"))
        )
        assert not stop_triggered(side="SELL", trigger=Decimal("2490"), snapshot=_snap())
