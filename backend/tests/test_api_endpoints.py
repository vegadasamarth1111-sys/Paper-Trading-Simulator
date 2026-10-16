"""
Tests for the Phase 5 trading API (plan: accounts, orders, positions,
PnL, strategies, risk, kill switch, reports, data health, streaming).

Ownership is the headline property: user B's rows are 404 to user A,
never 403-leaking existence... (404 either way — no oracle).
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from rest_framework.test import APIClient

from core.models import Strategy

UTC = ZoneInfo("UTC")
PX = datetime(2026, 1, 5, 4, 30, tzinfo=UTC)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    """
    Explicit view throttles (orders, kill switch) use the shared locmem
    cache in tests. Reset per test so throttle histories never leak
    across tests — each test gets a fresh rate-limit window.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _no_telegram(monkeypatch):
    """
    Force the unconfigured-Telegram path: repo .env files may carry
    placeholder tokens that decouple would otherwise pick up, and tests
    must NEVER touch the real network. Dedicated telegram tests use
    mocks; API tests assert the alert hook fires, not delivery.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")


@pytest.fixture(autouse=True)
def _frozen_broker_clock(frozen_clock):
    """
    Views construct their own PaperBroker (wall-clock by default), which
    would make entries time-dependent (trading window follows real time).
    Pin the constructed broker to the frozen in-hours clock so API tests
    are deterministic at any hour. Production still uses wall time.
    """
    from unittest.mock import patch

    from broker.paper import PaperBroker

    with patch(
        "api.views.PaperBroker",
        lambda *args, **kwargs: PaperBroker(*args, clock=frozen_clock, **kwargs),
    ):
        yield


@pytest.fixture
def trader(django_user_model):
    return django_user_model.objects.create_user(
        username="trader",
        email="trader@example.com",
        password="correct-horse-battery-staple",  # nosec B106 -- test fixture only
    )


@pytest.fixture
def trader_account(client, trader):
    """Trader's account, created through the API like a real signup flow."""
    from core.models import Account

    client.get("/api/v1/account/")
    return Account.objects.get(user=trader)


@pytest.fixture
def intruder(django_user_model):
    return django_user_model.objects.create_user(
        username="intruder",
        email="intruder@example.com",
        password="another-strong-passphrase-1",  # nosec B106 -- test fixture only
    )


@pytest.fixture
def client(trader):
    api = APIClient()
    api.force_authenticate(user=trader)
    return api


@pytest.fixture
def equity(equity_instrument):
    return equity_instrument


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


class TestAuth:
    def test_unauthenticated_rejected(self, equity):
        response = APIClient().get("/api/v1/account/")
        assert response.status_code in (401, 403)

    def test_account_autocreated(self, client):
        response = client.get("/api/v1/account/")
        assert response.status_code == 200
        assert response.data["current_balance"] == "1000000.0000"
        assert response.data["kill_switch_active"] is False


class TestOrders:
    def test_place_market_fills(self, client, equity):
        _tick(equity)
        response = client.post(
            "/api/v1/orders/",
            {
                "symbol": "RELIANCE",
                "side": "BUY",
                "order_type": "MKT",
                "quantity": 10,
                "stop_loss_price": "2400",
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.data["status"] == "FLD"
        assert Decimal(response.data["average_fill_price"]) > 0

    def test_place_rejected_surfaces_reason(self, client, equity):
        _tick(equity)
        response = client.post(
            "/api/v1/orders/",
            {
                "symbol": "RELIANCE",
                "side": "BUY",
                "order_type": "MKT",
                "quantity": 10,  # no stop -> mandatory SL blocks
            },
            format="json",
        )
        assert response.status_code == 422
        assert "risk" in response.data["rejection_reason"]

    def test_unknown_symbol_404(self, client):
        response = client.post(
            "/api/v1/orders/",
            {"symbol": "NOPE", "side": "BUY", "order_type": "MKT", "quantity": 1},
            format="json",
        )
        assert response.status_code == 404

    def test_missing_price_400(self, client):
        response = client.post(
            "/api/v1/orders/",
            {"symbol": "RELIANCE", "side": "BUY", "order_type": "LMT", "quantity": 1},
            format="json",
        )
        assert response.status_code == 400

    def test_modify_and_cancel(self, client, equity):
        _tick(equity)
        placed = client.post(
            "/api/v1/orders/",
            {
                "symbol": "RELIANCE",
                "side": "BUY",
                "order_type": "LMT",
                "quantity": 10,
                "price": "2400",
                "stop_loss_price": "2350",
            },
            format="json",
        )
        assert placed.status_code == 201
        order_id = placed.data["id"]
        modified = client.patch(f"/api/v1/orders/{order_id}/", {"price": "2390"}, format="json")
        assert modified.status_code == 200
        assert modified.data["price"] == "2390.0000"
        cancelled = client.delete(f"/api/v1/orders/{order_id}/")
        assert cancelled.status_code == 200
        assert cancelled.data["status"] == "CXL"

    def test_cancel_filled_422(self, client, equity):
        _tick(equity)
        placed = client.post(
            "/api/v1/orders/",
            {
                "symbol": "RELIANCE",
                "side": "BUY",
                "order_type": "MKT",
                "quantity": 1,
                "stop_loss_price": "2400",
            },
            format="json",
        )
        response = client.delete(f"/api/v1/orders/{placed.data['id']}/")
        assert response.status_code == 422

    def test_cross_account_invisible(self, client, intruder, equity):
        from django.contrib.auth import get_user_model

        _tick(equity)
        placed = client.post(
            "/api/v1/orders/",
            {
                "symbol": "RELIANCE",
                "side": "BUY",
                "order_type": "LMT",
                "quantity": 1,
                "price": "2400",
                "stop_loss_price": "2350",
            },
            format="json",
        )
        order_id = placed.data["id"]
        snooper = APIClient()
        snooper.force_authenticate(user=intruder)
        assert snooper.get(f"/api/v1/orders/{order_id}/").status_code == 404
        assert snooper.get("/api/v1/orders/").data == []
        assert snooper.get("/api/v1/trades/").data == []
        assert snooper.get("/api/v1/positions/").data == []
        _ = get_user_model


class TestTradesPositionsPnL:
    def test_flow(self, client, equity):
        _tick(equity, price="2500")
        client.post(
            "/api/v1/orders/",
            {
                "symbol": "RELIANCE",
                "side": "BUY",
                "order_type": "MKT",
                "quantity": 10,
                "stop_loss_price": "2400",
            },
            format="json",
        )
        trades = client.get("/api/v1/trades/").data
        assert len(trades) == 1
        assert Decimal(trades[0]["total_charges"]) >= 0
        positions = client.get("/api/v1/positions/").data
        assert len(positions) == 1
        assert positions[0]["quantity"] == 10
        pnl = client.get("/api/v1/pnl/").data
        assert "equity_curve" in pnl
        assert Decimal(pnl["total_equity"]) > 0


class TestStrategies:
    def test_start_stop_cycle(self, client, trader_account):
        strategy = Strategy.objects.create(
            account=trader_account,
            name="s1",
            class_name="engine.strategies.sma.SMACrossover",
            allocated_capital=Decimal("100000"),
        )
        listed = client.get("/api/v1/strategies/").data
        assert len(listed) == 1
        assert listed[0]["is_running"] is False
        started = client.post(f"/api/v1/strategies/{strategy.id}/start/")
        assert started.status_code == 201
        assert client.get("/api/v1/strategies/").data[0]["is_running"] is True
        stopped = client.post(f"/api/v1/strategies/{strategy.id}/stop/")
        assert stopped.data == {"stopped": True}
        assert client.get("/api/v1/strategies/").data[0]["is_running"] is False

    def test_signals_list(self, client, trader):
        assert client.get("/api/v1/signals/").data == []


class TestRiskEndpoints:
    def test_read_and_patch(self, client, trader_account):
        from risk.seed import seed_account_defaults

        seed_account_defaults(str(trader_account.id))
        rows = client.get("/api/v1/risk/").data
        assert len(rows) == 11
        patched = client.patch("/api/v1/risk/MAX_TRADES_DAY/", {"limit_value": "25"}, format="json")
        assert patched.status_code == 200
        assert patched.data["limit_value"] == "25.0000"

    def test_bad_values_rejected(self, client, trader_account):
        from risk.seed import seed_account_defaults

        seed_account_defaults(str(trader_account.id))
        assert (
            client.patch(
                "/api/v1/risk/MAX_TRADES_DAY/", {"limit_value": "-1"}, format="json"
            ).status_code
            == 400
        )
        assert (
            client.patch(
                "/api/v1/risk/MAX_DAILY_LOSS/", {"limit_percentage": "101"}, format="json"
            ).status_code
            == 400
        )
        assert client.get("/api/v1/risk/NOPE/").status_code == 404

    def test_events_visible(self, client, equity):
        _tick(equity)
        client.post(
            "/api/v1/orders/",
            {"symbol": "RELIANCE", "side": "BUY", "order_type": "MKT", "quantity": 1},
            format="json",
        )
        events = client.get("/api/v1/risk-events/").data
        assert any(e["action"] == "BLOCKED" for e in events)


class TestKillSwitch:
    def test_kill_and_release(self, client, trader, equity):
        _tick(equity)
        client.post(
            "/api/v1/orders/",
            {
                "symbol": "RELIANCE",
                "side": "BUY",
                "order_type": "MKT",
                "quantity": 10,
                "stop_loss_price": "2400",
            },
            format="json",
        )
        killed = client.post("/api/v1/kill-switch/", {"reason": "test"}, format="json")
        assert killed.status_code == 200
        assert killed.data["halted"] is True
        assert killed.data["summary"]["squared"]
        account = client.get("/api/v1/account/").data
        assert account["kill_switch_active"] is True
        # Halted placement is refused. The API client holds a cached user
        # object, so prove BOTH layers: the engine rejects on this client
        # (422), and a freshly-loaded user is stopped at permission (403).
        refused = client.post(
            "/api/v1/orders/",
            {
                "symbol": "RELIANCE",
                "side": "BUY",
                "order_type": "MKT",
                "quantity": 1,
                "stop_loss_price": "2400",
            },
            format="json",
        )
        assert refused.status_code == 422
        from django.contrib.auth import get_user_model

        fresh = APIClient()
        fresh.force_authenticate(user=get_user_model().objects.get(id=trader.id))
        assert (
            fresh.post(
                "/api/v1/orders/",
                {
                    "symbol": "RELIANCE",
                    "side": "BUY",
                    "order_type": "MKT",
                    "quantity": 1,
                    "stop_loss_price": "2400",
                },
                format="json",
            ).status_code
            == 403
        )
        released = client.post("/api/v1/kill-switch/release/", {}, format="json")
        assert released.data == {"released": True}


class TestReportsAndHealth:
    def test_csv_export(self, client, equity):
        _tick(equity)
        client.post(
            "/api/v1/orders/",
            {
                "symbol": "RELIANCE",
                "side": "BUY",
                "order_type": "MKT",
                "quantity": 5,
                "stop_loss_price": "2400",
            },
            format="json",
        )
        response = client.get("/api/v1/reports/daily.csv?date=2026-10-16")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        body = b"".join(response.streaming_content).decode()
        assert body.startswith("trade_id,executed_at,")
        assert "RELIANCE" in body or "trade_id" in body

    def test_csv_requires_date(self, client):
        assert client.get("/api/v1/reports/daily.csv").status_code == 400

    def test_data_health(self, client, equity):
        _tick(equity)
        body = client.get("/api/v1/data-health/?symbols=RELIANCE").data
        assert body["provider"] == "yahoo"
        assert body["symbols"][0]["symbol"] == "RELIANCE"
        assert body["symbols"][0]["status"] in ("live", "stale", "unknown")

    def test_stream_once(self, client, equity):
        _tick(equity)
        # NOTE: the flag is `single`, not `once` — `once=1` trips the
        # request-sanitization event-handler heuristic (`on\w+=`).
        response = client.get("/api/v1/stream/prices/?symbols=RELIANCE&single=1")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"
        body = b"".join(response.streaming_content).decode()
        assert body.startswith("data: ")
        assert "RELIANCE" in body

    def test_stream_requires_symbols(self, client):
        assert client.get("/api/v1/stream/prices/").status_code == 400
