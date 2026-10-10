"""
Tests for broker management commands + Celery tasks (thin wrappers).

Each command/task is exercised once: idempotency where claimed, failure
modes where they exit non-zero. Network-touching tasks (backfill/poll)
are covered with stub providers, never real HTTP.
"""

from datetime import datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command

from broker.models import CostTable
from core.models import Position

UTC = ZoneInfo("UTC")
PX = datetime(2026, 1, 5, 4, 30, tzinfo=UTC)

pytestmark = pytest.mark.django_db


def _tick(instrument, price="2500", vol=1000):
    from market_data.models import Tick

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


class TestLoadCostTable:
    def test_idempotent_seed(self):
        call_command("load_cost_table", stdout=StringIO())
        call_command("load_cost_table", stdout=StringIO())
        assert CostTable.objects.filter(version="zerodha-style-2026-01").count() == 1
        assert CostTable.active() is not None


class TestVerifyCosts:
    def test_prints_twenty_trades(self):
        out = StringIO()
        call_command("verify_costs", stdout=out)
        text = out.getvalue()
        assert "20 trades" in text
        assert "delivery buy 10x2500" in text


class TestReconcileCommand:
    def test_clean_books_succeed(self, account, equity_instrument):
        _tick(equity_instrument)
        from broker.paper import PaperBroker

        PaperBroker().place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=10,
            product_type="MIS",
            validity="DAY",
            client_order_id="cmd-1",
        )
        call_command("reconcile", stdout=StringIO())

    def test_drift_exits_nonzero(self, account, equity_instrument):
        Position.objects.create(
            account=account,
            instrument=equity_instrument,
            product_type="MIS",
            side="LONG",
            quantity=5,
            average_entry_price=Decimal("100"),
            opened_at=datetime.now(UTC),
        )
        with pytest.raises(SystemExit, match="mismatch"):
            call_command("reconcile", stdout=StringIO())


class TestSettleDayCommand:
    def test_full_pipeline(self, account, equity_instrument):
        _tick(equity_instrument, price="2500")
        from broker.paper import PaperBroker

        PaperBroker().place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=10,
            product_type="MIS",
            validity="DAY",
            client_order_id="sdc-1",
        )
        out = StringIO()
        call_command("settle_day", "--date", "2026-01-05", stdout=out)
        assert "equity=" in out.getvalue()
        account.refresh_from_db()
        assert Position.objects.filter(account=account, is_open=True).count() == 0


class TestTasks:
    def test_load_cost_table_task(self):
        from broker.tasks import load_cost_table_task

        result = load_cost_table_task()
        assert result["version"] == "zerodha-style-2026-01"

    def test_reconcile_task_ok(self, account, equity_instrument):
        from broker.tasks import reconcile_task

        assert reconcile_task(str(account.id))["ok"] is True

    def test_settle_day_task(self, account, equity_instrument):
        from broker.tasks import settle_day_task

        _tick(equity_instrument, price="2500")
        from broker.paper import PaperBroker

        PaperBroker().place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=10,
            product_type="MIS",
            validity="DAY",
            client_order_id="sdt-1",
        )
        result = settle_day_task(str(account.id), "2026-01-05")
        assert result["squared_off"] == 1
        assert Decimal(result["equity"]) > 0

    def test_backfill_task_uses_provider(self, account, equity_instrument):
        from market_data.tasks import backfill_history_task

        bars = [
            {
                "ts": datetime(2026, 1, 5, 3, 45, tzinfo=UTC),
                "open": Decimal("2500"),
                "high": Decimal("2510"),
                "low": Decimal("2495"),
                "close": Decimal("2505"),
                "volume": 100,
            }
        ]
        provider = MagicMock()
        provider.name = "yahoo"
        provider.fetch_history.return_value = bars
        with patch("market_data.services.get_provider", return_value=provider):
            result = backfill_history_task("RELIANCE", "1d", 5)
        assert result["created"] >= 1
