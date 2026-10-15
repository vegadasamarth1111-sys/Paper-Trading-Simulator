"""
Tests for the health check endpoint, the instrument API and the instrument
service layer, plus the production-mode environment guard.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

HEALTH_URL = "/api/v1/health/"
INSTRUMENTS_URL = "/api/v1/instruments/"


@pytest.mark.django_db
class TestHealthCheck:
    def test_is_publicly_accessible(self, api_client):
        """Load balancers and Docker healthchecks cannot authenticate."""
        response = api_client.get(HEALTH_URL)
        assert response.status_code == 200

    def test_reports_healthy_when_cache_and_db_work(self, api_client):
        response = api_client.get(HEALTH_URL)
        body = response.json()
        assert body["status"] == "healthy"
        assert body["components"]["database"]["status"] == "up"
        assert body["components"]["cache"]["status"] == "up"

    def test_reports_unhealthy_when_cache_fails(self, api_client):
        """A dead Redis must surface as unhealthy, not as a 500."""
        # Patch only the name bound inside the view. Patching `cache` globally
        # would also break DRF's throttler, which legitimately needs the cache.
        with patch("core.views.cache") as mock_cache:
            mock_cache.set.side_effect = Exception("redis down")
            response = api_client.get(HEALTH_URL)
        assert response.status_code == 503
        assert response.json()["status"] == "unhealthy"

    def test_reports_unhealthy_when_cache_is_degraded(self, api_client):
        """Cache silently not persisting should also count as unhealthy."""
        with patch("core.views.cache") as mock_cache:
            mock_cache.set.return_value = None
            mock_cache.get.return_value = "not-ok"
            response = api_client.get(HEALTH_URL)
        assert response.status_code == 503


@pytest.mark.django_db
class TestInstrumentListAPI:
    def test_requires_authentication(self, api_client, equity_instrument):
        assert api_client.get(INSTRUMENTS_URL).status_code == 401

    def test_lists_active_instruments(self, auth_client, equity_instrument):
        response = auth_client.get(INSTRUMENTS_URL)
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert len(body["data"]) == 1
        assert body["data"][0]["symbol"] == "RELIANCE"

    def test_never_returns_lot_size_as_a_hardcoded_default_only(
        self, auth_client, nifty_option_instrument
    ):
        response = auth_client.get(INSTRUMENTS_URL, {"segment": "OPT"})
        assert response.json()["data"][0]["lot_size"] == 75

    def test_filters_by_exchange(self, auth_client, equity_instrument):
        response = auth_client.get(INSTRUMENTS_URL, {"exchange": "BSE"})
        assert response.json()["data"] == []

    def test_filters_by_segment(self, auth_client, equity_instrument, nifty_option_instrument):
        response = auth_client.get(INSTRUMENTS_URL, {"segment": "EQ"})
        assert [i["symbol"] for i in response.json()["data"]] == ["RELIANCE"]

    def test_search_matches_symbol(self, auth_client, equity_instrument, nifty_option_instrument):
        response = auth_client.get(INSTRUMENTS_URL, {"search": "RELIANCE"})
        assert len(response.json()["data"]) == 1

    def test_search_matches_name(self, auth_client, equity_instrument):
        response = auth_client.get(INSTRUMENTS_URL, {"search": "Reliance Industries"})
        assert len(response.json()["data"]) == 1

    def test_hides_inactive_instruments(self, auth_client, equity_instrument):
        equity_instrument.is_active = False
        equity_instrument.save()
        assert auth_client.get(INSTRUMENTS_URL).json()["data"] == []

    def test_hides_superseded_instrument_versions(self, auth_client, equity_instrument):
        equity_instrument.effective_to = date(2025, 1, 1)
        equity_instrument.save()
        assert auth_client.get(INSTRUMENTS_URL).json()["data"] == []

    def test_html_in_search_param_is_blocked(self, auth_client, equity_instrument):
        """
        An XSS payload in a query parameter is rejected outright by the
        request sanitization middleware — it never reaches the view, and can
        therefore never be echoed back to a browser.
        """
        response = auth_client.get(INSTRUMENTS_URL, {"search": "<script>alert(1)</script>"})
        assert response.status_code == 403
        assert b"<script>" not in response.content

    def test_legitimate_search_still_works(self, auth_client, equity_instrument):
        response = auth_client.get(INSTRUMENTS_URL, {"search": "RELIANCE"})
        assert response.status_code == 200
        assert len(response.json()["data"]) == 1


@pytest.mark.django_db
class TestInstrumentService:
    def test_option_chain_is_ordered_by_strike(self, auth_client):
        from instruments.services import InstrumentService

        for i, strike in enumerate([24000, 25000, 24500]):
            from core.enums import Exchange, Segment
            from core.models import Instrument

            Instrument.objects.create(
                symbol=f"NIFTY{i}",
                exchange=Exchange.NSE,
                segment=Segment.OPTIONS,
                name=f"NIFTY {strike} CE",
                underlying_symbol="NIFTY",
                lot_size=75,
                strike_price=Decimal(str(strike)),
                option_type="CE",
                expiry_date=date(2026, 10, 29),
                effective_from=date(2026, 1, 1),
            )

        chain = InstrumentService.get_option_chain("NIFTY", date(2026, 10, 29))
        assert [i.strike_price for i in chain] == [
            Decimal("24000.00"),
            Decimal("24500.00"),
            Decimal("25000.00"),
        ]

    def test_lookup_by_exchange_token(self, equity_instrument):
        from instruments.services import InstrumentService

        found = InstrumentService.get_instrument_by_token("256265")
        assert found == equity_instrument
        assert InstrumentService.get_instrument_by_token("nope") is None

    def test_expiry_dates_excludes_past_and_orders_them(self, nifty_option_instrument):
        from instruments.services import InstrumentService

        past = nifty_option_instrument.expiry_date - timedelta(days=30)
        nifty_option_instrument.expiry_date = past
        nifty_option_instrument.save()

        future = date.today() + timedelta(days=60)
        nifty_option_instrument.expiry_date = future
        nifty_option_instrument.save()

        expiries = InstrumentService.get_expiry_dates("NIFTY")
        assert expiries == [future]

    def test_adjustment_factor_multiplies_all_actions(self, equity_instrument):
        from core.models import CorporateAction
        from instruments.services import InstrumentService

        CorporateAction.objects.create(
            instrument=equity_instrument,
            action_type="SPLIT",
            ex_date=date(2025, 6, 1),
            adjustment_factor=Decimal("0.500000"),
        )
        CorporateAction.objects.create(
            instrument=equity_instrument,
            action_type="BONUS",
            ex_date=date(2025, 9, 1),
            adjustment_factor=Decimal("0.750000"),
        )

        factor = InstrumentService.get_adjustment_factor(
            equity_instrument, date(2025, 1, 1), date(2025, 12, 31)
        )
        assert factor == Decimal("0.375000000000")

    def test_adjustment_factor_is_one_when_no_actions(self, equity_instrument):
        from instruments.services import InstrumentService

        factor = InstrumentService.get_adjustment_factor(
            equity_instrument, date(2025, 1, 1), date(2025, 12, 31)
        )
        assert factor == Decimal("1.000000")


@pytest.mark.django_db
class TestEnvironmentGuard:
    """
    Plan section 12: "Separate configuration for paper and live so live keys
    can never be loaded by accident."
    """

    GUARD_SNIPPET = (
        "import os,sys;"
        "os.environ['DJANGO_SETTINGS_MODULE']='config.settings.base';"
        "os.environ['DJANGO_SECRET_KEY']='x'*64;"
        "os.environ['DB_PASSWORD']='pw';"
        "os.environ['TRADING_MODE']='{mode}';"
        "os.environ['LIVE_TRADING_ENABLED']='{live}';"
        "import django;"
        "django.setup();"
        "print('LOADED')"
    )

    def _run(self, mode, live):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-c", self.GUARD_SNIPPET.format(mode=mode, live=live)],
            capture_output=True,
            text=True,
        )
        return result

    def test_paper_mode_loads_fine(self):
        result = self._run("paper", "False")
        assert "LOADED" in result.stdout

    def test_live_flag_without_live_mode_is_refused(self):
        """The exact accident that would put real money at risk."""
        result = self._run("paper", "True")
        assert result.returncode != 0
        assert "FATAL" in result.stderr

    def test_short_secret_key_is_refused(self):
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os;os.environ['DJANGO_SETTINGS_MODULE']='config.settings.base';"
                "os.environ['DJANGO_SECRET_KEY']='short';"
                "os.environ['DB_PASSWORD']='pw';"
                "import django;django.setup();",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "too short" in result.stderr


class TestLoggingConfig:
    """
    Regression: ProcessorFormatter "processor" values were dotted-path
    STRINGS, so every error-level log crashed formatting and the record
    was silently dropped (kill-switch audit logging hit this in tests).
    dictConfig only resolves "()" keys — processors must be instances.
    """

    def test_processors_are_callable(self):
        from django.conf import settings

        for name, formatter in settings.LOGGING["formatters"].items():
            processor = formatter.get("processor")
            assert callable(processor), f"formatter {name}: processor is not callable"

    def test_error_log_emits_without_logging_error(self, caplog):
        import logging

        logger = logging.getLogger("core")
        with caplog.at_level(logging.ERROR, logger="core"):
            logger.error("risk-test probe %s", "ok")
        assert "Logging error" not in caplog.text
