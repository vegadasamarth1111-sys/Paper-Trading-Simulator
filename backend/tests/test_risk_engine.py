"""
Tests for risk.engine — one per rule, each proving it blocks the bad order.

Setup per test: a Monday 10:00 IST timestamp (inside the trading window)
and a fresh tick (stale guard passes), so the ONLY firing rule is the one
under test. The exit-gate requirement is literal here: every rule in
plan section 6 has a blocking test below.
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from core.enums import RiskRuleType
from core.models import RiskConfig
from market_data.models import Tick
from risk.engine import CheckRequest, RiskEngine

UTC = ZoneInfo("UTC")
MON_10AM = datetime(2026, 1, 5, 4, 30, tzinfo=UTC)  # Monday, 10:00 IST

pytestmark = pytest.mark.django_db


def _tick(instrument, price="2500", ts=MON_10AM, vol=1000):
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


def _req(account, instrument, **overrides):
    base = {
        "account": account,
        "instrument": instrument,
        "side": "BUY",
        "order_type": "MKT",
        "quantity": 10,
        "product_type": "MIS",
        "ref_price": Decimal("2500"),
        "stop_loss_price": Decimal("2400"),  # baseline request is fully valid
        "client_order_id": "risk-test-1",
        "now": MON_10AM,
    }
    base.update(overrides)
    return CheckRequest(**base)


def _set(account, rule, value=None, pct=None, enabled=True):
    RiskConfig.objects.create(
        account=account,
        strategy=None,
        rule_type=rule,
        limit_value=Decimal(value) if value is not None else Decimal("0"),
        limit_percentage=Decimal(pct) if pct is not None else None,
        is_enabled=enabled,
    )


@pytest.fixture
def broker(frozen_clock):
    """PaperBroker on a frozen in-hours clock (deterministic risk window)."""
    from broker.paper import PaperBroker

    return PaperBroker(clock=frozen_clock)


class TestApprovals:
    def test_clean_order_approved(self, account, equity_instrument):
        _tick(equity_instrument)
        decision = RiskEngine.check(_req(account, equity_instrument))
        assert decision.approved

    def test_disabled_rule_skips(self, account, equity_instrument):
        _tick(equity_instrument)
        _set(account, RiskRuleType.MANDATORY_STOP_LOSS, value="0", enabled=False)
        # Rule off: approved even without a stop.
        assert RiskEngine.check(_req(account, equity_instrument, stop_loss_price=None)).approved


class TestKillSwitch:
    def test_halted_account_blocks_everything(self, account, equity_instrument):
        _tick(equity_instrument)
        account.kill_switch_active = True
        account.save()
        decision = RiskEngine.check(_req(account, equity_instrument))
        assert not decision.approved
        assert decision.rule == RiskRuleType.KILL_SWITCH


class TestDrawdown:
    def test_breached_drawdown_halts_entries(self, account, equity_instrument):
        _tick(equity_instrument)
        account.peak_balance = Decimal("1000000")
        account.current_balance = Decimal("850000")  # -15% drawdown
        account.save()
        decision = RiskEngine.check(_req(account, equity_instrument))
        assert not decision.approved
        assert decision.rule == RiskRuleType.MAX_DRAWDOWN


class TestTradingWindow:
    def test_first_minutes_block_entries(self, account, equity_instrument):
        _tick(equity_instrument)
        early = datetime(2026, 1, 5, 3, 47, tzinfo=UTC)  # 09:17 IST
        decision = RiskEngine.check(_req(account, equity_instrument, now=early))
        assert not decision.approved
        assert decision.rule == RiskRuleType.TRADING_WINDOW

    def test_late_entries_blocked(self, account, equity_instrument):
        _tick(equity_instrument)
        late = datetime(2026, 1, 5, 9, 50, tzinfo=UTC)  # 15:20 IST
        decision = RiskEngine.check(_req(account, equity_instrument, now=late))
        assert not decision.approved
        assert decision.rule == RiskRuleType.TRADING_WINDOW


class TestStaleData:
    def test_unknown_symbol_blocked(self, account, equity_instrument):
        decision = RiskEngine.check(_req(account, equity_instrument))
        assert not decision.approved
        assert decision.rule == RiskRuleType.STALE_DATA

    def test_old_tick_blocked(self, account, equity_instrument):
        _tick(equity_instrument, ts=MON_10AM.replace(hour=1))  # ~3.5h old
        decision = RiskEngine.check(_req(account, equity_instrument))
        assert not decision.approved
        assert decision.rule == RiskRuleType.STALE_DATA


class TestDuplicate:
    def test_same_signal_twice_blocked(self, broker, account, equity_instrument):
        _tick(equity_instrument)
        first = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=10,
            product_type="MIS",
            validity="DAY",
            client_order_id="dup-1",
            signal_id="sig-abc",
            stop_loss_price=Decimal("2400"),
        )
        assert first.status == "FLD"
        decision = RiskEngine.check(
            _req(account, equity_instrument, signal_id="sig-abc", client_order_id="dup-2")
        )
        assert not decision.approved
        assert decision.rule == RiskRuleType.DUPLICATE_ORDER

    def test_rejected_signal_may_retry(self, broker, account, equity_instrument):
        _tick(equity_instrument)
        # Rejected (absurd size) — retrying the signal stays legal.
        broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=100000,
            product_type="CNC",
            validity="DAY",
            client_order_id="dup-r1",
            signal_id="sig-retry",
            stop_loss_price=Decimal("2400"),
        )
        decision = RiskEngine.check(
            _req(
                account,
                equity_instrument,
                signal_id="sig-retry",
                client_order_id="dup-r2",
                stop_loss_price=Decimal("2400"),
            )
        )
        assert decision.approved


class TestRateLimits:
    def test_minute_rate_blocks(self, account, equity_instrument):
        _tick(equity_instrument)
        _set(account, RiskRuleType.ORDER_RATE_LIMIT, value="1")
        assert RiskEngine.check(_req(account, equity_instrument, client_order_id="r1")).approved
        from core.models import Order

        Order.objects.create(
            client_order_id="rate-prior",
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            product_type="MIS",
            quantity=1,
        )
        decision = RiskEngine.check(_req(account, equity_instrument, client_order_id="r2"))
        assert not decision.approved
        assert decision.rule == RiskRuleType.ORDER_RATE_LIMIT

    def test_daily_count_blocks(self, account, equity_instrument):
        _tick(equity_instrument)
        _set(account, RiskRuleType.MAX_TRADES_PER_DAY, value="1")
        assert RiskEngine.check(_req(account, equity_instrument, client_order_id="d1")).approved
        from core.models import Order

        prior = Order.objects.create(
            client_order_id="day-prior",
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            product_type="MIS",
            quantity=1,
        )
        Order.objects.filter(id=prior.id).update(placed_at=MON_10AM)
        decision = RiskEngine.check(_req(account, equity_instrument, client_order_id="d2"))
        assert not decision.approved
        assert decision.rule == RiskRuleType.MAX_TRADES_PER_DAY


class TestDailyLoss:
    def test_breached_loss_blocks_entries(self, account, equity_instrument):
        _tick(equity_instrument)
        account.realized_pnl = Decimal("-25000")  # -2.5% of 10L day start
        account.save()
        decision = RiskEngine.check(_req(account, equity_instrument))
        assert not decision.approved
        assert decision.rule == RiskRuleType.MAX_DAILY_LOSS


class TestMandatoryStop:
    def test_entry_without_stop_blocked(self, account, equity_instrument):
        _tick(equity_instrument)
        decision = RiskEngine.check(_req(account, equity_instrument, stop_loss_price=None))
        assert not decision.approved
        assert decision.rule == RiskRuleType.MANDATORY_STOP_LOSS

    def test_entry_with_stop_passes_this_rule(self, account, equity_instrument):
        _tick(equity_instrument)
        decision = RiskEngine.check(
            _req(account, equity_instrument, stop_loss_price=Decimal("2400"))
        )
        assert decision.approved


class TestRiskPerTrade:
    def test_oversized_risk_blocked(self, account, equity_instrument):
        _tick(equity_instrument)
        # 200 x Rs 100 stop distance = Rs 20,000 risk > 1% of 10L.
        decision = RiskEngine.check(
            _req(
                account,
                equity_instrument,
                quantity=200,
                stop_loss_price=Decimal("2400"),
                client_order_id="rt-1",
            )
        )
        assert not decision.approved
        assert decision.rule == RiskRuleType.MAX_RISK_PER_TRADE


class TestPositionSize:
    def test_value_cap_blocks(self, account, equity_instrument):
        _tick(equity_instrument)
        # 100 x 2500 = 2,50,000 > Rs 2,00,000 cap (risk-per-trade passes).
        decision = RiskEngine.check(
            _req(
                account,
                equity_instrument,
                quantity=100,
                stop_loss_price=Decimal("2490"),
                client_order_id="ps-1",
            )
        )
        assert not decision.approved
        assert decision.rule == RiskRuleType.MAX_POSITION_SIZE

    def test_percentage_cap_blocks(self, account, equity_instrument):
        _tick(equity_instrument)
        _set(account, RiskRuleType.MAX_POSITION_SIZE, value="10000000", pct="20")
        # 120 x 2500 = 3,00,000 > 20% of 10L, under the raised value cap.
        decision = RiskEngine.check(
            _req(
                account,
                equity_instrument,
                quantity=120,
                stop_loss_price=Decimal("2499"),
                client_order_id="ps-2",
            )
        )
        assert not decision.approved
        assert decision.rule == RiskRuleType.MAX_POSITION_SIZE


class TestOpenPositions:
    def test_eleventh_position_blocked(self, account, equity_instrument):
        from datetime import date

        from core.models import Instrument, Position

        _tick(equity_instrument)
        _set(account, RiskRuleType.ORDER_RATE_LIMIT, value="1000")
        for i in range(10):
            inst = Instrument.objects.create(
                symbol=f"POS{i}",
                exchange="NSE",
                segment="EQ",
                name=f"Pos {i}",
                exchange_token=str(9000 + i),
                lot_size=1,
                tick_size=Decimal("0.05"),
                effective_from=date(2024, 1, 1),
            )
            Position.objects.create(
                account=account,
                instrument=inst,
                product_type="MIS",
                side="LONG",
                quantity=1,
                average_entry_price=Decimal("100"),
                opened_at=datetime.now(UTC),
            )
        decision = RiskEngine.check(
            _req(
                account,
                equity_instrument,
                quantity=1,
                stop_loss_price=Decimal("2499"),
                client_order_id="op-1",
            )
        )
        assert not decision.approved
        assert decision.rule == RiskRuleType.MAX_OPEN_POSITIONS


class TestStrategyScope:
    def test_strategy_row_beats_account_row(self, account, equity_instrument):
        from core.models import Strategy

        _tick(equity_instrument)
        strategy = Strategy.objects.create(
            account=account, name="strat-1", class_name="x.Y", allocated_capital=Decimal("100000")
        )
        # Strategy disables the stop rule: its orders pass without stops.
        RiskConfig.objects.create(
            account=account,
            strategy=strategy,
            rule_type=RiskRuleType.MANDATORY_STOP_LOSS,
            limit_value=Decimal("0"),
            is_enabled=False,
        )
        from core.models import StrategyRun

        run = StrategyRun.objects.create(
            strategy=strategy,
            mode="paper",
            strategy_version="test",
            parameters_snapshot={},
        )
        assert RiskEngine.check(
            _req(account, equity_instrument, strategy_run=run, stop_loss_price=None)
        ).approved
        # Same order without the strategy scope is still blocked.
        assert not RiskEngine.check(_req(account, equity_instrument, stop_loss_price=None)).approved
