"""
Tests for broker.paper — order lifecycle through the real engine.

Market orders fill immediately against seeded ticks; limit/SL orders rest
until process_snapshot() matches them. Bracket OCO, idempotency, IOC,
and every rejection path (lots, naked shorts, CNC shorts, circuits,
margin) are covered. Ticks are seeded directly — the matching engine,
not the data feed, is under test.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from broker.fills import MarketSnapshot
from broker.paper import PaperBroker
from core.enums import OrderStatus
from core.exceptions import InvalidOrderTransitionError
from core.models import Order, OrderEvent, Position, Trade
from market_data.models import Tick

UTC = ZoneInfo("UTC")
PX = datetime(2026, 1, 5, 4, 30, tzinfo=UTC)  # Monday 09:30 IST

pytestmark = pytest.mark.django_db


def _seed_tick(instrument, price="2500", vol=1000, ts=PX):
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


def _snap(price="2500", qty=1000):
    px = Decimal(price)
    return MarketSnapshot(bid=px, ask=px, ltp=px, bid_qty=qty, ask_qty=qty, volume=100000)


@pytest.fixture
def broker(frozen_clock):
    """PaperBroker on a frozen in-hours clock (deterministic risk window)."""
    return PaperBroker(clock=frozen_clock)


class TestPlacement:
    def test_market_buy_fills_immediately(self, broker, account, equity_instrument):
        _seed_tick(equity_instrument)
        order = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=10,
            product_type="MIS",
            validity="DAY",
            client_order_id="mkt-1",
            stop_loss_price=Decimal("2400"),
        )
        assert order.status == OrderStatus.FILLED
        assert order.filled_quantity == 10
        assert Trade.objects.filter(order=order).count() == 1
        # Full audit trail: NEW -> VALIDATED -> OPEN -> FILLED.
        trail = list(OrderEvent.objects.filter(order=order).values_list("to_status", flat=True))
        assert trail == ["VAL", "OPN", "FLD"]

    def test_idempotent_replay_returns_original(self, broker, account, equity_instrument):
        _seed_tick(equity_instrument)
        first = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=10,
            product_type="MIS",
            validity="DAY",
            client_order_id="idem-1",
            stop_loss_price=Decimal("2400"),
        )
        second = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=99,
            product_type="MIS",
            validity="DAY",
            client_order_id="idem-1",
        )
        assert first.id == second.id
        assert Order.objects.filter(client_order_id="idem-1").count() == 1

    def test_limit_rests_until_matched(self, broker, account, equity_instrument):
        _seed_tick(equity_instrument, price="2500")
        order = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="LMT",
            quantity=10,
            price=Decimal("2400"),
            product_type="MIS",
            validity="DAY",
            client_order_id="lmt-1",
            stop_loss_price=Decimal("2350"),
        )
        assert order.status == OrderStatus.OPEN
        assert Trade.objects.count() == 0
        broker.process_snapshot(equity_instrument, _snap(price="2399"))
        order.refresh_from_db()
        assert order.status == OrderStatus.FILLED  # ask 2399 < limit 2400: trade-through

    def test_insufficient_margin_rejects(self, broker, account, equity_instrument):
        from core.enums import RiskRuleType
        from core.models import RiskConfig

        _seed_tick(equity_instrument, price="2500")
        # Lift the position-size cap so the order reaches the margin check.
        RiskConfig.objects.create(
            account=account,
            strategy=None,
            rule_type=RiskRuleType.MAX_POSITION_SIZE,
            limit_value=Decimal("1000000000"),
            limit_percentage=None,
        )
        order = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=100000,
            product_type="CNC",
            validity="DAY",
            client_order_id="big-1",
            stop_loss_price=Decimal("2499.99"),
        )
        assert order.status == OrderStatus.REJECTED
        assert "margin" in order.rejection_reason

    def test_no_price_rejects_stale(self, broker, account, equity_instrument):
        order = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=1,
            product_type="MIS",
            validity="DAY",
            client_order_id="stale-1",
        )
        assert order.status == OrderStatus.REJECTED
        assert "stale" in order.rejection_reason

    def test_circuit_rejects_limit(self, broker, account, equity_instrument):
        _seed_tick(equity_instrument)
        equity_instrument.upper_circuit = Decimal("2600")
        equity_instrument.lower_circuit = Decimal("2400")
        equity_instrument.save()
        order = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="LMT",
            quantity=1,
            price=Decimal("2700"),
            product_type="MIS",
            validity="DAY",
            client_order_id="ckt-1",
        )
        assert order.status == OrderStatus.REJECTED
        assert "circuit" in order.rejection_reason

    def test_cnc_sell_beyond_holding_rejects(self, broker, account, equity_instrument):
        _seed_tick(equity_instrument)
        order = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="SELL",
            order_type="MKT",
            quantity=5,
            product_type="CNC",
            validity="DAY",
            client_order_id="short-1",
        )
        assert order.status == OrderStatus.REJECTED
        assert "held quantity" in order.rejection_reason

    def test_option_lot_multiple_enforced(self, broker, account, nifty_option_instrument):
        _seed_tick(nifty_option_instrument, price="120")
        order = broker.place_order(
            account=account,
            instrument=nifty_option_instrument,
            side="BUY",
            order_type="MKT",
            quantity=10,
            product_type="NRML",
            validity="DAY",
            client_order_id="lot-1",
        )
        assert order.status == OrderStatus.REJECTED
        assert "lot size" in order.rejection_reason

    def test_naked_option_write_rejected(self, broker, account, nifty_option_instrument):
        _seed_tick(nifty_option_instrument, price="120")
        order = broker.place_order(
            account=account,
            instrument=nifty_option_instrument,
            side="SELL",
            order_type="MKT",
            quantity=75,
            product_type="NRML",
            validity="DAY",
            client_order_id="naked-1",
        )
        assert order.status == OrderStatus.REJECTED
        assert "Naked option" in order.rejection_reason


class TestModifyCancel:
    def test_modify_open_limit(self, broker, account, equity_instrument):
        _seed_tick(equity_instrument)
        order = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="LMT",
            quantity=10,
            price=Decimal("2400"),
            product_type="MIS",
            validity="DAY",
            client_order_id="mod-1",
            stop_loss_price=Decimal("2350"),
        )
        broker.modify_order(order, price=Decimal("2390"))
        order.refresh_from_db()
        assert order.price == Decimal("2390.0000")

    def test_modify_filled_raises(self, broker, account, equity_instrument):
        _seed_tick(equity_instrument)
        order = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=1,
            product_type="MIS",
            validity="DAY",
            client_order_id="mod-2",
            stop_loss_price=Decimal("2400"),
        )
        with pytest.raises(InvalidOrderTransitionError):
            broker.modify_order(order, price=Decimal("1"))

    def test_cancel_releases_margin(self, broker, account, equity_instrument):
        _seed_tick(equity_instrument)
        order = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="LMT",
            quantity=10,
            price=Decimal("2400"),
            product_type="MIS",
            validity="DAY",
            client_order_id="cxl-1",
            stop_loss_price=Decimal("2350"),
        )
        account.refresh_from_db()
        assert account.margin_used > 0
        broker.cancel_order(order, reason="test cancel")
        order.refresh_from_db()
        account.refresh_from_db()
        assert order.status == OrderStatus.CANCELLED
        assert account.margin_used == 0


class TestBracketOCO:
    def test_target_fill_cancels_stop(self, broker, account, equity_instrument):
        _seed_tick(equity_instrument, price="2500")
        entry = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="LMT",
            quantity=10,
            price=Decimal("2500"),
            product_type="MIS",
            validity="DAY",
            client_order_id="bo-1",
            target_price=Decimal("2550"),
            stoploss_price=Decimal("2450"),
        )
        broker.process_snapshot(equity_instrument, _snap(price="2499"))
        entry.refresh_from_db()
        assert entry.status == OrderStatus.FILLED
        legs = list(Order.objects.filter(parent_order=entry))
        assert len(legs) == 2
        broker.process_snapshot(equity_instrument, _snap(price="2551"))
        statuses = {Order.objects.get(id=leg.id).status for leg in legs}
        assert OrderStatus.FILLED in statuses  # target hit
        assert OrderStatus.CANCELLED in statuses  # stop cancelled (OCO)

    def test_legs_matchable_on_silent_feed(self, frozen_clock, account, equity_instrument):
        """Exits are never trapped: legs spawn AND match with no feed at all.

        No Tick rows exist and the quote source is silent — the entry
        carries its own quote time, and the exit legs pass the engine
        because exits only ever answer to the kill switch.
        """
        from broker.paper import PaperBroker

        broker = PaperBroker(
            clock=frozen_clock,
            price_source=lambda inst: Decimal("2500"),
            quote_ts_source=lambda inst: None,
        )
        quote_ts = datetime(2026, 1, 5, 4, 30, tzinfo=UTC)
        entry = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=10,
            product_type="MIS",
            validity="DAY",
            client_order_id="bo-silent-1",
            target_price=Decimal("2550"),
            stoploss_price=Decimal("2450"),
            stop_loss_price=Decimal("2450"),
            market_quote_ts=quote_ts,
        )
        assert entry.status == OrderStatus.FILLED
        legs = list(Order.objects.filter(parent_order=entry))
        assert len(legs) == 2
        assert {leg.status for leg in legs} == {OrderStatus.OPEN}
        px = Decimal("2551")
        broker.process_snapshot(
            equity_instrument,
            MarketSnapshot(bid=px, ask=px, ltp=px, bid_qty=1000, ask_qty=1000, ts=quote_ts),
        )
        statuses = {Order.objects.get(id=leg.id).status for leg in legs}
        assert OrderStatus.FILLED in statuses  # target hit
        assert OrderStatus.CANCELLED in statuses  # stop cancelled (OCO)

    def test_ioc_cancels_leftover(self, broker, account, equity_instrument):
        _seed_tick(equity_instrument, price="2500")
        order = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="LMT",
            quantity=10,
            price=Decimal("2400"),
            product_type="MIS",
            validity="IOC",
            client_order_id="ioc-1",
            stop_loss_price=Decimal("2350"),
        )
        broker.process_snapshot(equity_instrument, _snap(price="2500"))
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELLED


class TestMoneyFlow:
    def test_round_trip_pnl_and_charges(self, broker, account, equity_instrument):
        _seed_tick(equity_instrument, price="2500")
        buy = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=10,
            product_type="MIS",
            validity="DAY",
            client_order_id="rt-buy",
            stop_loss_price=Decimal("2400"),
        )
        assert buy.status == OrderStatus.FILLED
        # The market moves via a new tick (snapshots passed to the matcher are
        # never persisted — only ticks move the visible price).
        _seed_tick(equity_instrument, price="2510", vol=2000, ts=PX + timedelta(minutes=1))
        sell = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="SELL",
            order_type="MKT",
            quantity=10,
            product_type="MIS",
            validity="DAY",
            client_order_id="rt-sell",
        )
        assert sell.status == OrderStatus.FILLED
        position = Position.objects.get(
            account=account, instrument=equity_instrument, is_open=False
        )
        assert position.quantity == 0
        assert position.realized_pnl > 0  # 10 Rs/unit minus slippage+costs stays positive
        account.refresh_from_db()
        assert account.realized_pnl == position.realized_pnl
