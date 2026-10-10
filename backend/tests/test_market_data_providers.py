"""
Tests for market_data providers — Yahoo (keyless) and Angel One (split keys).

Yahoo parsing is tested against a frozen chart payload (no network), so CI
never depends on Yahoo being reachable. Angel master parsing is tested the
same way. Angel quote methods MUST raise MissingCredentials while the user
shares zero keys — returning [] would masquerade "no keys" as "no data".
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import httpx
import pytest

from market_data.providers.angel import AngelOneProvider, MissingCredentials
from market_data.providers.base import to_decimal
from market_data.providers.yahoo import YahooFinanceProvider

_YAHOO_PAYLOAD = {
    "chart": {
        "result": [
            {
                "timestamp": [1767585600, 1767585660, 1767585720],
                "indicators": {
                    "quote": [
                        {
                            "open": [2500.0, 2501.0, None],
                            "high": [2502.0, 2503.0, 2504.0],
                            "low": [2499.0, 2500.0, 2501.0],
                            "close": [2501.0, 2502.5, None],
                            "volume": [1000, 1200, 800],
                        }
                    ]
                },
            }
        ],
        "error": None,
    }
}


class TestToDecimal:
    def test_float_is_forbidden(self):
        with pytest.raises(TypeError):
            to_decimal(1.5)

    def test_str_and_int_ok(self):
        assert to_decimal("2500.05") == Decimal("2500.05")
        assert to_decimal(100) == Decimal("100")


class TestYahooParsing:
    def test_null_minutes_skipped_not_invented(self):
        bars = YahooFinanceProvider._parse_chart(_YAHOO_PAYLOAD)
        assert len(bars) == 2  # third minute has null close -> skipped
        assert bars[0]["close"] == Decimal("2501.0")
        assert bars[0]["volume"] == 1000
        assert bars[0]["ts"] == datetime.fromtimestamp(1767585600, tz=UTC)

    def test_missing_ohlc_legs_fall_back_to_close(self):
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1000],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [None],
                                    "high": [None],
                                    "low": [None],
                                    "close": [100.5],
                                    "volume": [10],
                                }
                            ]
                        },
                    }
                ],
                "error": None,
            }
        }
        (bar,) = YahooFinanceProvider._parse_chart(payload)
        assert bar["open"] == bar["high"] == bar["low"] == Decimal("100.5")

    def test_garbage_payload_raises(self):
        with pytest.raises(ValueError):
            YahooFinanceProvider._parse_chart({"chart": {"result": []}})

    def test_1m_window_guard(self):
        provider = YahooFinanceProvider()
        with pytest.raises(ValueError, match="limited to ~7 days"):
            provider.fetch_history("RELIANCE", interval="1m", period_days=30)

    def test_unknown_interval_raises(self):
        provider = YahooFinanceProvider()
        with pytest.raises(ValueError, match="Unsupported interval"):
            provider.fetch_history("RELIANCE", interval="2m")

    def test_fetch_history_uses_injected_client(self):
        """No real network: inject a stub httpx client."""
        response = MagicMock()
        response.json.return_value = _YAHOO_PAYLOAD
        client = MagicMock(spec=httpx.Client)
        client.get.return_value = response
        provider = YahooFinanceProvider(client=client)
        bars = provider.fetch_history("RELIANCE", interval="1d", period_days=5)
        assert len(bars) == 2
        client.get.assert_called_once()
        url = client.get.call_args[0][0]
        assert "RELIANCE.NS" in url

    def test_host_fallback_on_first_failure(self):
        good = MagicMock()
        good.json.return_value = _YAHOO_PAYLOAD
        client = MagicMock(spec=httpx.Client)
        client.get.side_effect = [httpx.ConnectError("down"), good]
        provider = YahooFinanceProvider(client=client)
        bars = provider.fetch_history("RELIANCE", interval="1d", period_days=5)
        assert len(bars) == 2
        assert client.get.call_count == 2

    def test_seed_instruments_shape_matches_loader(self):
        rows = YahooFinanceProvider().fetch_instruments()
        symbols = {r["symbol"] for r in rows}
        assert {"RELIANCE", "TCS", "INFY", "NIFTY", "BANKNIFTY"} <= symbols
        for row in rows:
            assert set(row) >= {
                "symbol",
                "exchange",
                "segment",
                "name",
                "exchange_token",
                "lot_size",
                "tick_size",
            }


class TestAngelOne:
    def test_quotes_require_credentials(self):
        with patch("market_data.providers.angel._credentials_present", return_value=False):
            provider = AngelOneProvider()
            with pytest.raises(MissingCredentials, match="ANGEL_API_KEY"):
                provider.fetch_ltp(["RELIANCE"])
            with pytest.raises(MissingCredentials, match="ANGEL_API_KEY"):
                provider.fetch_history("RELIANCE")

    def test_master_parsing_filters_nse(self):
        raw = [
            {
                "exch_seg": "NSE",
                "symbol": "RELIANCE",
                "name": "RELIANCE",
                "token": "2885",
                "lotsize": "1",
                "ticksize": "0.05",
                "instrumenttype": "",
                "expiry": "",
                "strike": "-1",
            },
            {
                "exch_seg": "NFO",
                "symbol": "NIFTY27FEB25FUT",
                "name": "NIFTY",
                "token": "12345",
                "lotsize": "65",
                "ticksize": "0.05",
                "instrumenttype": "FUTIDX",
                "expiry": "27FEB2025",
                "strike": "-1",
            },
            {"exch_seg": "BSE", "symbol": "RELIANCE", "name": "x", "token": "1"},
            {"exch_seg": "NSE", "symbol": "", "name": "", "token": "0"},
        ]
        response = MagicMock()
        response.json.return_value = raw
        with patch("httpx.get", return_value=response):
            rows = AngelOneProvider().fetch_instruments()
        assert len(rows) == 2
        eq, fut = rows
        assert (eq["symbol"], eq["segment"], eq["exchange_token"]) == ("RELIANCE", "EQ", "2885")
        assert fut["segment"] == "FUT"
        assert fut["lot_size"] == 65
        assert str(fut["expiry_date"]) == "2025-02-27"
