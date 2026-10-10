"""
Tests for the core domain models.

Focus is on the invariants the production plan calls out explicitly:
  - money is Decimal and never float,
  - the order state machine rejects illegal transitions,
  - trades compute their own value and charges,
  - ledger-style balances can be reasoned about.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from core.enums import (
    AuditAction,
    OrderStatus,
    PositionSide,
    ProductType,
    RiskAction,
    RiskRuleType,
)
from core.exceptions import InvalidOrderTransitionError
from core.models import (
    Account,
    AccountLedgerEntry,
    AuditLog,
    Instrument,
    Order,
    OrderEvent,
    PortfolioSnapshot,
    Position,
    RiskConfig,
    RiskEvent,
    Signal,
    Strategy,
    StrategyRun,
    SystemConfig,
    Trade,
    TradingHoliday,
)

pytestmark = pytest.mark.django_db

UTC = UTC


@pytest.fixture
def strategy(account):
    return Strategy.objects.create(
        account=account, name="Baseline Momentum", class_name="strategies.momentum.Momentum"
    )


@pytest.fixture
def strategy_run(strategy):
    return StrategyRun.objects.create(
        strategy=strategy,
        mode="backtest",
        strategy_version="1.0.0",
        parameters_snapshot={"lookback": 20},
    )


class TestAccount:
    def test_default_capital_is_ten_lakh(self, user):
        account = Account.objects.create(user=user)
        assert account.initial_capital == Decimal("1000000.0000")
        assert account.current_balance == Decimal("1000000.0000")
        assert account.peak_balance == Decimal("1000000.0000")

    def test_available_margin(self, user):
        account = Account.objects.create(
            user=user, current_balance=Decimal("800000"), margin_used=Decimal("50000")
        )
        assert account.available_margin == Decimal("750000")

    def test_total_equity_includes_unrealized_pnl(self, user):
        account = Account.objects.create(
            user=user,
            current_balance=Decimal("1000000"),
            unrealized_pnl=Decimal("25000"),
        )
        assert account.total_equity == Decimal("1025000")

    def test_drawdown_is_percentage_of_peak(self, user):
        account = Account.objects.create(
            user=user,
            current_balance=Decimal("900000"),
            unrealized_pnl=Decimal("0"),
            peak_balance=Decimal("1000000"),
        )
        assert account.drawdown == Decimal("10.00")

    def test_drawdown_is_zero_at_peak(self, user):
        account = Account.objects.create(
            user=user, current_balance=Decimal("1000000"), peak_balance=Decimal("1000000")
        )
        assert account.drawdown == Decimal("0")

    def test_drawdown_handles_zero_peak_without_dividing_by_zero(self, user):
        account = Account.objects.create(
            user=user, current_balance=Decimal("100"), peak_balance=Decimal("0")
        )
        assert account.drawdown == Decimal("0")

    def test_kill_switch_defaults_to_off(self, user):
        assert Account.objects.create(user=user).kill_switch_active is False

    def test_str_includes_username_and_balance(self, user):
        account = Account.objects.create(user=user)
        assert "samarth" in str(account)

    def test_one_account_per_user_is_enforced(self, user):
        Account.objects.create(user=user)
        with pytest.raises(IntegrityError), transaction.atomic():
            Account.objects.create(user=user)


class TestAccountLedgerEntry:
    def test_entry_str_shows_sign(self, account):
        entry = AccountLedgerEntry.objects.create(
            account=account,
            entry_type=AccountLedgerEntry.EntryType.INITIAL_DEPOSIT,
            amount=Decimal("1000000"),
            balance_after=Decimal("1000000"),
        )
        assert "+" in str(entry)

    def test_debit_entry_str_has_no_plus(self, account):
        entry = AccountLedgerEntry.objects.create(
            account=account,
            entry_type=AccountLedgerEntry.EntryType.TRADE_DEBIT,
            amount=Decimal("-5000"),
            balance_after=Decimal("995000"),
        )
        assert "+" not in str(entry)


class TestOrderStateMachine:
    def _make(self, account, instrument, **kwargs):
        payload = dict(
            client_order_id="ord-1",
            account=account,
            instrument=instrument,
            side="BUY",
            order_type="MKT",
            product_type="MIS",
            quantity=10,
        )
        payload.update(kwargs)
        return Order.objects.create(**payload)

    def test_pending_quantity_defaults_to_order_quantity(self, order):
        order.refresh_from_db()
        assert order.pending_quantity == 10

    def test_status_defaults_to_new(self, order):
        assert order.status == OrderStatus.NEW

    def test_new_to_validated(self, order):
        order.transition_to(OrderStatus.VALIDATED)
        assert order.status == OrderStatus.VALIDATED

    def test_new_to_open_is_illegal(self, order):
        """You cannot skip validation."""
        with pytest.raises(InvalidOrderTransitionError, match="Invalid transition"):
            order.transition_to(OrderStatus.OPEN)

    def test_new_to_filled_is_illegal(self, order):
        with pytest.raises(InvalidOrderTransitionError):
            order.transition_to(OrderStatus.FILLED)

    def test_rejection_records_the_reason(self, order):
        order.transition_to(OrderStatus.REJECTED, reason="Insufficient margin")
        assert order.status == OrderStatus.REJECTED
        assert order.rejection_reason == "Insufficient margin"

    def test_open_to_cancelled(self, order):
        order.status = OrderStatus.OPEN
        order.transition_to(OrderStatus.CANCELLED)
        assert order.status == OrderStatus.CANCELLED

    def test_open_to_partially_filled_to_filled(self, order):
        order.status = OrderStatus.OPEN
        order.transition_to(OrderStatus.PARTIALLY_FILLED)
        order.transition_to(OrderStatus.FILLED)
        assert order.status == OrderStatus.FILLED

    def test_partial_fill_can_repeat(self, order):
        order.status = OrderStatus.PARTIALLY_FILLED
        order.transition_to(OrderStatus.PARTIALLY_FILLED)
        assert order.status == OrderStatus.PARTIALLY_FILLED

    def test_terminal_state_blocks_all_transitions(self, order):
        order.status = OrderStatus.FILLED
        for target in [
            OrderStatus.OPEN,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.PARTIALLY_FILLED,
        ]:
            with pytest.raises(InvalidOrderTransitionError, match="terminal state"):
                order.transition_to(target)

    def test_transition_error_carries_order_id_in_details(self, order):
        with pytest.raises(InvalidOrderTransitionError) as exc:
            order.transition_to(OrderStatus.FILLED)
        assert exc.value.details["order_id"] == str(order.id)

    @pytest.mark.parametrize(
        "status,terminal",
        [
            (OrderStatus.FILLED, True),
            (OrderStatus.REJECTED, True),
            (OrderStatus.CANCELLED, True),
            (OrderStatus.EXPIRED, True),
            (OrderStatus.OPEN, False),
            (OrderStatus.NEW, False),
        ],
    )
    def test_is_terminal(self, order, status, terminal):
        order.status = status
        assert order.is_terminal is terminal

    @pytest.mark.parametrize(
        "status,active",
        [
            (OrderStatus.OPEN, True),
            (OrderStatus.PARTIALLY_FILLED, True),
            (OrderStatus.FILLED, False),
            (OrderStatus.NEW, False),
        ],
    )
    def test_is_active(self, order, status, active):
        order.status = status
        assert order.is_active is active

    def test_client_order_id_must_be_unique(self, account, equity_instrument):
        self._make(account, equity_instrument)
        with pytest.raises(IntegrityError), transaction.atomic():
            self._make(account, equity_instrument)

    def test_order_event_str_shows_transition(self, order):
        event = OrderEvent.objects.create(
            order=order, from_status=OrderStatus.NEW, to_status=OrderStatus.VALIDATED
        )
        assert "NEW" in str(event) and "VAL" in str(event)

    def test_str_includes_symbol_and_side(self, order):
        text = str(order)
        assert "RELIANCE" in text and "BUY" in text


class TestTrade:
    def _make(self, account, order, instrument, **kwargs):
        payload = dict(
            trade_id="trd-1",
            order=order,
            account=account,
            instrument=instrument,
            side="BUY",
            quantity=10,
            price=Decimal("2500.0000"),
            executed_at=datetime(2026, 1, 7, 4, 0, tzinfo=UTC),
        )
        payload.update(kwargs)
        return Trade.objects.create(**payload)

    def test_value_is_computed_from_price_and_quantity(self, account, order, equity_instrument):
        trade = self._make(account, order, equity_instrument)
        assert trade.value == Decimal("25000.0000")

    def test_total_charges_is_the_sum_of_components(self, account, order, equity_instrument):
        trade = self._make(
            account,
            order,
            equity_instrument,
            brokerage=Decimal("20"),
            stt=Decimal("2.5"),
            exchange_charges=Decimal("1.1"),
            sebi_charges=Decimal("0.1"),
            gst=Decimal("3.7"),
            stamp_duty=Decimal("1.25"),
            dp_charges=Decimal("15.3"),
        )
        assert trade.total_charges == Decimal("43.9500")

    def test_net_value_subtracts_charges(self, account, order, equity_instrument):
        trade = self._make(account, order, equity_instrument, brokerage=Decimal("100"))
        assert trade.net_value == Decimal("24900.0000")

    def test_slippage_is_measured_against_expected_price(self, account, order, equity_instrument):
        trade = self._make(
            account,
            order,
            equity_instrument,
            price=Decimal("2501.0000"),
            expected_price=Decimal("2500.0000"),
        )
        assert trade.slippage == Decimal("1.0000")

    def test_no_slippage_without_expected_price(self, account, order, equity_instrument):
        trade = self._make(account, order, equity_instrument)
        assert trade.slippage == Decimal("0.0000")

    def test_trade_id_must_be_unique(self, account, order, equity_instrument):
        self._make(account, order, equity_instrument)
        with pytest.raises(IntegrityError), transaction.atomic():
            self._make(account, order, equity_instrument)


class TestPosition:
    def _make(self, account, instrument, **kwargs):
        payload = dict(
            account=account,
            instrument=instrument,
            side=PositionSide.LONG,
            product_type=ProductType.CNC,
            quantity=100,
            average_entry_price=Decimal("250.0000"),
            opened_at=datetime(2026, 1, 7, 4, 0, tzinfo=UTC),
        )
        payload.update(kwargs)
        return Position.objects.create(**payload)

    def test_long_position_gains_when_price_rises(self, account, equity_instrument):
        position = self._make(account, equity_instrument)
        position.update_unrealized_pnl(Decimal("260.0000"))
        assert position.unrealized_pnl == Decimal("1000.0000")
        assert position.last_price == Decimal("260.0000")

    def test_long_position_loses_when_price_falls(self, account, equity_instrument):
        position = self._make(account, equity_instrument)
        position.update_unrealized_pnl(Decimal("240.0000"))
        assert position.unrealized_pnl == Decimal("-1000.0000")

    def test_flat_position_has_zero_unrealized_pnl(self, account, equity_instrument):
        position = self._make(account, equity_instrument, quantity=0)
        position.update_unrealized_pnl(Decimal("999.0000"))
        assert position.unrealized_pnl == Decimal("0.0000")

    def test_str_shows_direction(self, account, equity_instrument):
        assert "LONG" in str(self._make(account, equity_instrument))
        assert "SHORT" in str(
            self._make(account, equity_instrument, quantity=-50, side=PositionSide.SHORT)
        )


class TestInstrument:
    def test_equity_is_not_a_derivative(self, equity_instrument):
        assert equity_instrument.is_derivative is False
        assert equity_instrument.is_option is False

    def test_option_is_derivative_and_option(self, nifty_option_instrument):
        assert nifty_option_instrument.is_derivative is True
        assert nifty_option_instrument.is_option is True

    def test_unexpired_option(self, nifty_option_instrument):
        assert nifty_option_instrument.is_expired is False

    def test_expired_option(self, equity_instrument):
        equity_instrument.expiry_date = date(2020, 1, 1)
        assert equity_instrument.is_expired is True

    def test_equity_without_expiry_is_never_expired(self, equity_instrument):
        assert equity_instrument.is_expired is False

    def test_str_includes_exchange_and_segment(self, equity_instrument):
        assert "RELIANCE" in str(equity_instrument)
        assert "NSE" in str(equity_instrument)

    def test_option_str_includes_strike_and_expiry(self, nifty_option_instrument):
        text = str(nifty_option_instrument)
        assert "Strike" in text and "Exp" in text

    def test_duplicate_symbol_exchange_effective_from_is_rejected(self, equity_instrument):
        with pytest.raises(IntegrityError), transaction.atomic():
            Instrument.objects.create(
                symbol=equity_instrument.symbol,
                exchange=equity_instrument.exchange,
                segment=equity_instrument.segment,
                name="Duplicate",
                effective_from=equity_instrument.effective_from,
            )

    def test_same_symbol_on_a_later_date_is_allowed(self, equity_instrument):
        Instrument.objects.create(
            symbol=equity_instrument.symbol,
            exchange=equity_instrument.exchange,
            segment=equity_instrument.segment,
            name="New version",
            effective_from=date(2025, 1, 1),
        )
        assert Instrument.objects.filter(symbol="RELIANCE").count() == 2


class TestTradingHoliday:
    def test_duplicate_date_and_exchange_is_rejected(self):
        TradingHoliday.objects.create(
            date=date(2026, 1, 26), exchange="NSE", description="Republic Day"
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            TradingHoliday.objects.create(
                date=date(2026, 1, 26), exchange="NSE", description="Republic Day"
            )

    def test_str_marks_half_days(self):
        holiday = TradingHoliday.objects.create(
            date=date(2026, 1, 26), exchange="NSE", description="Muhurat", is_half_day=True
        )
        assert "half day" in str(holiday)

    def test_str_has_no_suffix_on_full_days(self):
        holiday = TradingHoliday.objects.create(
            date=date(2026, 1, 26), exchange="NSE", description="Republic Day"
        )
        assert "half day" not in str(holiday)


class TestPortfolioSnapshot:
    def _make(self, account, **kwargs):
        payload = dict(
            account=account,
            snapshot_date=date(2026, 1, 7),
            cash_balance=Decimal("1000000"),
            margin_used=Decimal("0"),
            total_equity=Decimal("1000000"),
            day_pnl=Decimal("0"),
            cumulative_pnl=Decimal("0"),
            peak_equity=Decimal("1000000"),
        )
        payload.update(kwargs)
        return PortfolioSnapshot.objects.create(**payload)

    def test_one_snapshot_per_account_per_day(self, account):
        self._make(account)
        with pytest.raises(IntegrityError), transaction.atomic():
            self._make(account)

    def test_str_includes_date_and_equity(self, account):
        assert "2026-01-07" in str(self._make(account))


class TestRiskModels:
    def test_risk_config_defaults_to_enabled(self, account):
        config = RiskConfig.objects.create(
            account=account,
            rule_type=RiskRuleType.MAX_DAILY_LOSS,
            limit_value=Decimal("5000"),
        )
        assert str(config)

    def test_risk_event_str_mentions_action(self, account):
        event = RiskEvent.objects.create(
            account=account,
            rule_type=RiskRuleType.MAX_OPEN_POSITIONS,
            action=RiskAction.BLOCKED,
            message="Too many open positions",
        )
        assert "BLOCKED" in str(event)


class TestStrategyModels:
    def test_new_run_starts_running(self, strategy_run):
        """A StrategyRun is created in the running state and stamped with a start time."""
        assert strategy_run.is_running is True
        assert strategy_run.started_at is not None
        assert str(strategy_run)

    def test_signal_defaults_to_not_executed(self, strategy_run, equity_instrument):
        signal = Signal.objects.create(
            strategy_run=strategy_run,
            instrument=equity_instrument,
            direction="BUY",
            confidence=Decimal("0.8"),
        )
        assert signal.was_executed is False
        assert str(signal)

    def test_account_name_is_unique_per_account(self, strategy):
        with pytest.raises(IntegrityError), transaction.atomic():
            Strategy.objects.create(
                account=strategy.account,
                name=strategy.name,
                class_name="other.Class",
            )


class TestAuditAndConfig:
    def test_audit_log_str(self):
        log = AuditLog.objects.create(
            action=AuditAction.ORDER_PLACED,
            entity_type="order",
            entity_id="abc-123",
            description="Order placed",
        )
        assert "ORD_PLACED" in str(log)

    def test_system_config_str(self):
        config = SystemConfig.objects.create(
            key="starting_capital", value="1000000", category="trading"
        )
        assert str(config)
