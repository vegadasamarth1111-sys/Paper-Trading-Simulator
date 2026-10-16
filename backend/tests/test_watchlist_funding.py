"""
Tests for Phase 5+ endpoints: account funding, watchlists, market
overview/indices/chain (the Groww-style dashboard contract).
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from rest_framework.test import APIClient

from core.models import Watchlist

UTC = ZoneInfo("UTC")
PX = datetime(2026, 1, 5, 4, 30, tzinfo=UTC)

pytestmark = pytest.mark.django_db


@pytest.fixture
def trader(django_user_model):
    return django_user_model.objects.create_user(
        username="wtrader",
        email="wtrader@example.com",
        password="correct-horse-battery-staple",  # nosec B106 -- test fixture only
    )


@pytest.fixture
def client(trader):
    api = APIClient()
    api.force_authenticate(user=trader)
    return api


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


def _daily(symbol, closes, instrument=None):
    from market_data.models import Candle

    if instrument is None:
        from core.models import Instrument

        instrument = Instrument.objects.get(symbol=symbol)
    for i, close in enumerate(closes):
        ts = datetime(2026, 1, 5 + i, 4, 15, tzinfo=UTC)
        Candle.objects.create(
            instrument=instrument,
            symbol=symbol,
            exchange="NSE",
            timeframe="1d",
            open_ts=ts,
            open_price=Decimal(close),
            high_price=Decimal(close),
            low_price=Decimal(close),
            close_price=Decimal(close),
            volume=100,
            trades=1,
            is_complete=True,
            source="yahoo",
        )


class TestFunding:
    def test_deposit_and_withdraw(self, client, trader):
        from core.models import Account

        assert (
            client.post("/api/v1/account/deposit/", {"amount": "50000"}, format="json").status_code
            == 200
        )
        account = Account.objects.get(user=trader)
        assert account.current_balance == Decimal("1050000.0000")
        response = client.post("/api/v1/account/withdraw/", {"amount": "20000"}, format="json")
        assert response.status_code == 200
        account.refresh_from_db()
        assert account.current_balance == Decimal("1030000.0000")

    def test_bad_amounts_rejected(self, client):
        assert (
            client.post("/api/v1/account/deposit/", {"amount": "-5"}, format="json").status_code
            == 400
        )
        assert (
            client.post("/api/v1/account/deposit/", {"amount": "abc"}, format="json").status_code
            == 400
        )
        assert (
            client.post("/api/v1/account/frobnicate/", {"amount": "5"}, format="json").status_code
            == 400
        )

    def test_over_withdraw_blocked(self, client):
        response = client.post("/api/v1/account/withdraw/", {"amount": "99999999"}, format="json")
        assert response.status_code == 422

    def test_ledger_records_funding(self, client, trader):
        from core.models import Account, AccountLedgerEntry

        client.post("/api/v1/account/deposit/", {"amount": "1000"}, format="json")
        account = Account.objects.get(user=trader)
        assert (
            AccountLedgerEntry.objects.filter(account=account, reference_type="funding").count()
            == 1
        )


class TestWatchlists:
    def test_crud(self, client, equity_instrument):
        created = client.post(
            "/api/v1/watchlists/",
            {"name": "Mine", "symbols": ["reliance", "RELIANCE", " "]},
            format="json",
        )
        assert created.status_code == 201
        assert created.data["symbols"] == ["RELIANCE"]  # normalized + de-duped
        watch_id = created.data["id"]
        assert len(client.get("/api/v1/watchlists/").data) == 1
        patched = client.patch(
            f"/api/v1/watchlists/{watch_id}/",
            {"symbols": ["RELIANCE", "TCS"]},
            format="json",
        )
        # TCS is not in the test master: rejected, never stored half-clean.
        assert patched.status_code == 400
        assert client.delete(f"/api/v1/watchlists/{watch_id}/").data == {"deleted": True}
        assert Watchlist.objects.count() == 0

    def test_unknown_symbols_rejected(self, client):
        response = client.post(
            "/api/v1/watchlists/", {"name": "Bad", "symbols": ["NOPE"]}, format="json"
        )
        assert response.status_code == 400

    def test_empty_symbols_rejected(self, client):
        assert (
            client.post(
                "/api/v1/watchlists/", {"name": "E", "symbols": []}, format="json"
            ).status_code
            == 400
        )

    def test_cross_user_invisible(self, client, trader, django_user_model, equity_instrument):
        other = django_user_model.objects.create_user(username="o", email="o@e.com", password="x")
        mine = client.post(
            "/api/v1/watchlists/", {"name": "M", "symbols": ["RELIANCE"]}, format="json"
        ).data["id"]
        snooper = APIClient()
        snooper.force_authenticate(user=other)
        assert snooper.get(f"/api/v1/watchlists/{mine}/").status_code == 404
        assert snooper.get("/api/v1/watchlists/").data == []
        _ = trader


class TestOverview:
    def test_overview_with_change(self, client, equity_instrument):
        _tick(equity_instrument, price="2550")
        _daily("RELIANCE", ["2400", "2500"], instrument=equity_instrument)
        body = client.get("/api/v1/market/overview/?symbols=RELIANCE,NOPE").data
        (row,) = [r for r in body["symbols"] if r["symbol"] == "RELIANCE"]
        assert row["ltp"] == "2550.0000"
        # The tick predates both daily bars, so change is the last stored
        # session's own move (2500 - 2400), not a blend with stale data.
        assert row["change"] == "100.00"
        assert row["change_pct"] == "4.17"
        assert body["missing"] == ["NOPE"]

    def test_indices(self, client):
        from core.models import Instrument

        Instrument.objects.create(
            symbol="NIFTY",
            exchange="NSE",
            segment="IDX",
            name="Nifty 50",
            exchange_token="",
            lot_size=1,
            tick_size=Decimal("0.05"),
            effective_from=datetime(2024, 1, 1, tzinfo=UTC).date(),
        )
        body = client.get("/api/v1/market/indices/").data
        assert [r["symbol"] for r in body["indices"]] == ["NIFTY"]

    def test_expiries_and_chain(self, client, nifty_option_instrument):
        body = client.get("/api/v1/market/expiries/?underlying=NIFTY").data
        assert body["expiries"] == ["2026-10-29"]
        assert client.get("/api/v1/market/expiries/").status_code == 400
        chain = client.get("/api/v1/market/option-chain/?underlying=NIFTY&expiry=2026-10-29").data
        assert chain["strikes"][0]["strike"] == "25000.00"
        assert chain["strikes"][0]["call"]["symbol"] == "NIFTY26OCT25000CE"
        assert chain["strikes"][0]["call"]["ltp"] is None  # never quoted: honest null
        bad = client.get("/api/v1/market/option-chain/?underlying=NIFTY").data
        assert "errors" in bad
