"""
Tests for market_data.views — authenticated read-only market API.

Write paths stay in commands/Celery by design; these endpoints serve the
dashboard and strategies. Closed candles sort first so a strategy can
never mistake an open candle for truth.
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from rest_framework.test import APIClient

from core.models import Instrument
from market_data.models import Candle, DataQualityReport, Tick

UTC = ZoneInfo("UTC")
TS = datetime(2026, 1, 5, 4, 30, tzinfo=UTC)

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(
        username="trader",
        email="trader@example.com",
        password="correct-horse-battery-staple",  # nosec B106 -- test fixture only
    )


@pytest.fixture
def client(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def reliance():
    return Instrument.objects.create(
        symbol="RELIANCE",
        exchange="NSE",
        segment="EQ",
        name="Reliance Industries Limited",
        exchange_token="256265",
        lot_size=1,
        tick_size=Decimal("0.05"),
        effective_from=datetime(2026, 1, 1, tzinfo=UTC).date(),
        is_active=True,
    )


class TestLatestPrice:
    def test_requires_symbol(self, client):
        response = client.get("/api/v1/market/latest/")
        assert response.status_code == 400

    def test_404_without_data(self, client):
        response = client.get("/api/v1/market/latest/", {"symbol": "RELIANCE"})
        assert response.status_code == 404

    def test_returns_db_tick(self, client, reliance):
        Tick.objects.create(
            instrument=reliance,
            symbol="RELIANCE",
            exchange="NSE",
            exchange_ts=TS,
            received_ts=TS,
            ltp=Decimal("2510.50"),
            volume_traded=999,
            source="yahoo",
        )
        response = client.get("/api/v1/market/latest/", {"symbol": "reliance"})
        assert response.status_code == 200
        assert response.data["ltp"] == "2510.5000"

    def test_unauthenticated_rejected(self, reliance):
        response = APIClient().get("/api/v1/market/latest/", {"symbol": "RELIANCE"})
        assert response.status_code in (401, 403)


class TestCandles:
    def _candle(self, reliance, minute, complete, close="2501"):
        return Candle.objects.create(
            instrument=reliance,
            symbol="RELIANCE",
            exchange="NSE",
            timeframe="1m",
            open_ts=datetime(2026, 1, 5, 4, minute, tzinfo=UTC),
            open_price=Decimal("2500"),
            high_price=Decimal("2502"),
            low_price=Decimal("2499"),
            close_price=Decimal(close),
            volume=100,
            trades=5,
            is_complete=complete,
            source="yahoo",
        )

    def test_closed_first(self, client, reliance):
        self._candle(reliance, 30, complete=False)
        self._candle(reliance, 29, complete=True)
        response = client.get("/api/v1/market/candles/", {"symbol": "RELIANCE", "timeframe": "1m"})
        assert response.status_code == 200
        candles = response.data["candles"]
        assert candles[0]["is_complete"] is True

    def test_bad_timeframe_rejected(self, client):
        response = client.get("/api/v1/market/candles/", {"symbol": "RELIANCE", "timeframe": "2m"})
        assert response.status_code == 400


class TestQuality:
    def test_404_without_report(self, client):
        response = client.get("/api/v1/market/quality/")
        assert response.status_code == 404

    def test_returns_latest_report(self, client):
        DataQualityReport.objects.create(
            report_date=datetime(2026, 1, 5, tzinfo=UTC).date(),
            exchange="NSE",
            symbols_checked=50,
            total_ticks=1000,
            bad_ticks=10,
            is_suspect=False,
            notes="ok",
        )
        response = client.get("/api/v1/market/quality/")
        assert response.status_code == 200
        assert response.data["is_suspect"] is False
        assert response.data["bad_tick_ratio"] == pytest.approx(0.01)

    def test_bad_date_rejected(self, client):
        response = client.get("/api/v1/market/quality/", {"date": "not-a-date"})
        assert response.status_code == 400
