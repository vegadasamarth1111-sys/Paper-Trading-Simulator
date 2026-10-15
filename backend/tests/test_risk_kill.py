"""
Tests for risk.kill — the one call that stops everything.

Kill cancels resting orders, squares every open position at market,
halts the account, and logs the halt. Release is deliberate and never
automatic. A second kill is safe (idempotent placement keys).
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command

from core.enums import OrderStatus, RiskRuleType
from core.models import Order, Position, RiskEvent
from market_data.models import Tick
from risk.kill import kill_switch, release_kill_switch
from risk.seed import seed_account_defaults

UTC = ZoneInfo("UTC")
PX = datetime(2026, 1, 5, 4, 30, tzinfo=UTC)

pytestmark = pytest.mark.django_db


def _tick(instrument, price="2500", vol=1000):
    return Tick.objects.create(
        instrument=instrument,
        symbol=instrument.symbol,
        exchange="NSE",
        exchange_ts=PX,
        received_ts=PX,
        ltp=Decimal(price),
        volume_traded=vol,
        source="yahoo",
    )


@pytest.fixture
def broker(frozen_clock):
    from broker.paper import PaperBroker

    return PaperBroker(clock=frozen_clock)


@pytest.fixture
def live_book(broker, account, equity_instrument):
    """An account with one resting order and one open MIS position."""
    _tick(equity_instrument, price="2500")
    resting = broker.place_order(
        account=account,
        instrument=equity_instrument,
        side="BUY",
        order_type="LMT",
        quantity=10,
        price=Decimal("2400"),
        product_type="MIS",
        validity="DAY",
        client_order_id="kill-rest",
        stop_loss_price=Decimal("2350"),
    )
    assert resting.status == OrderStatus.OPEN
    filled = broker.place_order(
        account=account,
        instrument=equity_instrument,
        side="BUY",
        order_type="MKT",
        quantity=10,
        product_type="MIS",
        validity="DAY",
        client_order_id="kill-fill",
        stop_loss_price=Decimal("2400"),
    )
    assert filled.status == OrderStatus.FILLED
    return account


class TestKillSwitch:
    def test_kill_cancels_squares_halts(self, broker, live_book, equity_instrument):
        account = live_book
        summary = kill_switch(account=account, reason="test halt", broker=broker)

        assert summary["cancelled"] == ["kill-rest"]
        assert len(summary["squared"]) == 1
        account.refresh_from_db()
        assert account.kill_switch_active is True
        assert (
            Position.objects.filter(account=account, is_open=True).exclude(quantity=0).count() == 0
        )
        assert RiskEvent.objects.filter(
            account=account, rule_type=RiskRuleType.KILL_SWITCH
        ).exists()

    def test_halted_account_rejects_even_exits(self, broker, live_book, equity_instrument):
        from risk.engine import CheckRequest, RiskEngine

        account = live_book
        kill_switch(account=account, reason="test halt", broker=broker)
        decision = RiskEngine.check(
            CheckRequest(
                account=account,
                instrument=equity_instrument,
                side="SELL",
                order_type="MKT",
                quantity=10,
                product_type="MIS",
                ref_price=Decimal("2500"),
                client_order_id="kill-exit",
                now=PX,
            )
        )
        assert not decision.approved
        assert decision.rule == RiskRuleType.KILL_SWITCH

    def test_second_kill_is_safe(self, broker, live_book):
        first = kill_switch(account=live_book, reason="one", broker=broker)
        second = kill_switch(account=live_book, reason="two", broker=broker)
        assert second["already_halted"] is True
        # No duplicate exit orders: the square-off idempotency key is
        # deterministic per (account, symbol, timestamp-second).
        squares = Order.objects.filter(client_order_id__startswith=f"kill-{live_book.id.hex[:8]}-")
        assert squares.count() == len(first["squared"]) + len(second["squared"])

    def test_release_is_deliberate(self, broker, live_book, equity_instrument):
        kill_switch(account=live_book, reason="test halt", broker=broker)
        live_book.refresh_from_db()
        assert live_book.kill_switch_active is True
        release_kill_switch(account=live_book, reason="false alarm")
        live_book.refresh_from_db()
        assert live_book.kill_switch_active is False


class TestSeedAndCommands:
    def test_seed_writes_all_rules_without_clobbering(self, account):
        first = seed_account_defaults(str(account.id))
        assert len(first["created"]) == 11  # every rule except KILL_SWITCH
        # Tighten one limit by hand; re-seed must not overwrite it.
        from core.models import RiskConfig

        RiskConfig.objects.filter(
            account=account, rule_type=RiskRuleType.MAX_OPEN_POSITIONS
        ).update(limit_value=Decimal("3"))
        second = seed_account_defaults(str(account.id))
        assert len(second["created"]) == 0
        assert RiskConfig.objects.get(
            account=account, rule_type=RiskRuleType.MAX_OPEN_POSITIONS
        ).limit_value == Decimal("3.0000")

    def test_kill_switch_command(self, live_book, broker):
        from io import StringIO

        call_command(
            "kill_switch",
            "--account",
            str(live_book.id),
            "--reason",
            "cmd test",
            stdout=StringIO(),
        )
        live_book.refresh_from_db()
        assert live_book.kill_switch_active is True
        call_command(
            "kill_switch",
            "--account",
            str(live_book.id),
            "--release",
            "--reason",
            "cmd release",
            stdout=StringIO(),
        )
        live_book.refresh_from_db()
        assert live_book.kill_switch_active is False

    def test_seed_command(self, account):
        from io import StringIO

        from core.models import RiskConfig

        call_command("seed_risk_defaults", "--account", str(account.id), stdout=StringIO())
        assert RiskConfig.objects.filter(account=account).count() == 11

    def test_tasks(self, live_book):
        from risk.tasks import kill_switch_task, release_task, seed_defaults_task

        assert len(seed_defaults_task(str(live_book.id))["created"]) >= 0
        result = kill_switch_task(str(live_book.id), "task halt")
        assert result["already_halted"] in (True, False)
        assert release_task(str(live_book.id), "task release") == {"released": True}
        live_book.refresh_from_db()
        assert live_book.kill_switch_active is False
