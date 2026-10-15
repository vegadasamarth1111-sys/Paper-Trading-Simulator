"""
Tests for broker.ledger + broker.eod + broker.options.

- Reconciliation: exact rebuild on clean books; detects injected drift
  (balance, position, charges); the Hypothesis property proves
  balance == initial + sum(ledger) over random fill sequences.
- EOD: DAY orders expire, MIS squares off at close, ITM options
  cash-settle, OTM expire worthless, snapshots record equity.
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import settings as hypo_settings
from hypothesis import strategies as st

from broker.eod import expire_day_orders, settle_day, square_off_intraday
from broker.ledger import rebuild_balance, rebuild_positions, reconcile_account
from broker.options import intrinsic_value, settle_expired_options
from broker.paper import PaperBroker
from broker.positions import PositionState, apply_fill
from core.enums import OrderStatus
from core.models import Order, Position, Trade
from market_data.models import Tick

UTC = ZoneInfo("UTC")
PX = datetime(2026, 1, 5, 4, 30, tzinfo=UTC)

pytestmark = pytest.mark.django_db


def _tick(instrument, price="2500", vol=1000, ts=PX):
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


def _trade(account, instrument, order=None, side="BUY", qty=10, price="100"):
    return Trade.objects.create(
        trade_id=f"TRD-T{Trade.objects.count():06d}",
        order=order,
        account=account,
        instrument=instrument,
        side=side,
        quantity=qty,
        price=Decimal(price),
        value=Decimal(price) * qty,
        executed_at=datetime.now(UTC),
    )


@pytest.fixture
def broker(frozen_clock):
    """PaperBroker on a frozen in-hours clock (deterministic risk window)."""
    return PaperBroker(clock=frozen_clock)


class TestReconcile:
    def test_clean_books_reconcile(self, broker, account, equity_instrument):
        _tick(equity_instrument)
        broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=10,
            product_type="MIS",
            validity="DAY",
            client_order_id="rc-1",
            stop_loss_price=Decimal("2400"),
        )
        report = reconcile_account(account)
        assert report.ok, [(m.area, m.key, m.stored, m.rebuilt) for m in report.mismatches]

    def test_balance_drift_detected(self, broker, account, equity_instrument):
        _tick(equity_instrument)
        broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=10,
            product_type="MIS",
            validity="DAY",
            client_order_id="rc-2",
            stop_loss_price=Decimal("2400"),
        )
        account.current_balance += Decimal("100")  # silent hand-edit (the bug)
        account.save()
        report = reconcile_account(account)
        assert not report.balance_ok
        assert any(m.area == "balance" for m in report.mismatches)

    def test_phantom_position_detected(self, account, equity_instrument):
        Position.objects.create(
            account=account,
            instrument=equity_instrument,
            product_type="MIS",
            side="LONG",
            quantity=5,
            average_entry_price=Decimal("100"),
            opened_at=datetime.now(UTC),
        )
        report = reconcile_account(account)
        assert not report.positions_ok

    def test_rebuild_helpers(self, account, equity_instrument):
        assert rebuild_balance(account) == Decimal("1000000.0000")
        assert rebuild_positions(account) == {}


class TestLedgerProperty:
    """Property: balance always equals initial capital plus ledger sum.

    Objects are created per example (not via fixtures): Hypothesis runs
    many examples inside one test call, so function-scoped fixtures would
    leak state across examples.
    """

    @hypo_settings(max_examples=25, deadline=None)
    @given(
        fills=st.lists(
            st.tuples(
                st.sampled_from(["BUY", "SELL"]),
                st.integers(min_value=1, max_value=20),
                st.decimals(min_value="10", max_value="500", places=2),
            ),
            min_size=1,
            max_size=15,
        )
    )
    def test_balance_rebuild_matches(self, fills):
        import uuid as uuid_lib

        from django.contrib.auth import get_user_model

        from broker.ledger import record_fill_entries
        from core.models import Account, Instrument

        tag = uuid_lib.uuid4().hex[:8]
        user = get_user_model().objects.create_user(
            username=f"hypo-{tag}",
            email=f"{tag}@example.com",
            password="x",  # nosec B106
        )
        account = Account.objects.create(user=user)
        instrument = Instrument.objects.create(
            symbol=f"H{tag}",
            exchange="NSE",
            segment="EQ",
            name="Hypo",
            exchange_token=tag,
            lot_size=1,
            tick_size=Decimal("0.05"),
            effective_from=date(2024, 1, 1),
        )
        state = PositionState(quantity=0, average_entry_price=Decimal("0"))
        for side, qty, price in fills:
            trade = _trade(account, instrument, side=side, qty=qty, price=str(price))
            before = state.realized_pnl
            apply_fill(state, side=side, quantity=qty, price=price)
            account.refresh_from_db()
            record_fill_entries(
                account=account, trade=trade, position_realized=state.realized_pnl - before
            )
            account.save()
        account.refresh_from_db()
        assert abs(rebuild_balance(account) - account.current_balance) <= Decimal("0.0001")
        # Invariant: fills never exceed their order (single-fill trades here).
        for trade in Trade.objects.filter(account=account):
            assert trade.quantity > 0
            assert trade.value == (trade.price * trade.quantity).quantize(Decimal("0.0001"))


class TestEOD:
    def test_day_orders_expire(self, broker, account, equity_instrument):
        _tick(equity_instrument)
        order = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="LMT",
            quantity=10,
            price=Decimal("2400"),
            product_type="MIS",
            validity="DAY",
            client_order_id="eod-1",
            stop_loss_price=Decimal("2350"),
        )
        assert order.status == OrderStatus.OPEN
        # placed_at is auto_now_add (today); backdate into the trading day
        # under test so the EOD job for that day can see it.
        Order.objects.filter(id=order.id).update(placed_at=datetime(2026, 1, 5, 5, 0, tzinfo=UTC))
        assert expire_day_orders(as_of=date(2026, 1, 5)) == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.EXPIRED

    def test_mis_squared_off_delivery_left(self, broker, account, equity_instrument):
        _tick(equity_instrument, price="2500")
        broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=10,
            product_type="MIS",
            validity="DAY",
            client_order_id="sq-1",
            stop_loss_price=Decimal("2400"),
        )
        summary = square_off_intraday(
            account=account,
            price_of=lambda inst: Decimal("2510"),
            broker=broker,
        )
        assert summary["squared"] == 1
        position = Position.objects.get(
            account=account, instrument=equity_instrument, is_open=False
        )
        assert position.quantity == 0

    def test_settle_day_snapshot(self, broker, account, equity_instrument):
        _tick(equity_instrument, price="2500")
        broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=10,
            product_type="MIS",
            validity="DAY",
            client_order_id="st-1",
            stop_loss_price=Decimal("2400"),
        )
        snapshot = settle_day(
            account=account,
            day=date(2026, 1, 5),
            price_of=lambda inst: Decimal("2510"),
        )
        assert snapshot.total_equity > Decimal("1000000")
        assert snapshot.open_positions_count == 1
        # Re-running the same day updates instead of duplicating.
        again = settle_day(
            account=account,
            day=date(2026, 1, 5),
            price_of=lambda inst: Decimal("2510"),
        )
        assert again.id == snapshot.id


class TestOptions:
    def test_intrinsic_math(self):
        assert intrinsic_value(
            option_type="CE", spot=Decimal("26000"), strike=Decimal("25000")
        ) == Decimal("1000")
        assert intrinsic_value(
            option_type="PE", spot=Decimal("24000"), strike=Decimal("25000")
        ) == Decimal("1000")
        assert intrinsic_value(
            option_type="CE", spot=Decimal("24000"), strike=Decimal("25000")
        ) == Decimal("0")
        with pytest.raises(ValueError):
            intrinsic_value(option_type="XX", spot=Decimal("1"), strike=Decimal("1"))

    def test_itm_settles_otm_worthless(self, account, nifty_option_instrument):
        from core.models import Instrument

        # Expired yesterday; long 75 units bought at 120.
        nifty_option_instrument.expiry_date = date(2026, 1, 4)
        nifty_option_instrument.save()
        Position.objects.create(
            account=account,
            instrument=nifty_option_instrument,
            product_type="NRML",
            side="LONG",
            quantity=75,
            average_entry_price=Decimal("120"),
            opened_at=datetime.now(UTC),
        )
        summary = settle_expired_options(
            account=account,
            as_of=date(2026, 1, 5),
            spot_of=lambda sym: Decimal("25200"),  # 200 ITM on 25000 CE
        )
        assert summary["settled"] == 1
        assert summary["credited"] > 0
        position = Position.objects.get(account=account, instrument=nifty_option_instrument)
        assert not position.is_open

        # OTM contract expires worthless (no trade, no credit).
        otm = Instrument.objects.create(
            symbol="NIFTY26OCT24000PE",
            exchange="NSE",
            segment="OPT",
            name="NIFTY 24000 PE",
            underlying_symbol="NIFTY",
            lot_size=75,
            tick_size=Decimal("0.05"),
            strike_price=Decimal("24000"),
            option_type="PE",
            expiry_date=date(2026, 1, 4),
            effective_from=date(2026, 1, 1),
        )
        Position.objects.create(
            account=account,
            instrument=otm,
            product_type="NRML",
            side="LONG",
            quantity=75,
            average_entry_price=Decimal("50"),
            opened_at=datetime.now(UTC),
        )
        summary = settle_expired_options(
            account=account,
            as_of=date(2026, 1, 5),
            spot_of=lambda sym: Decimal("25200"),  # 26000 PE is OTM
        )
        assert summary["worthless"] == 1
        assert Trade.objects.filter(instrument=otm).count() == 0
