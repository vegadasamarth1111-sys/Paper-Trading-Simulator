"""
Tests for market_data ingestion + services + universe (Phase 1 wiring).

Proves: polling stores ticks then validates (raw-first), rejects land in
bad_ticks with codes, Redis latest-price works with DB fallback,
store_history is idempotent, and the universe seed is sane.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from core.clock import Clock, ClockMode, TradingCalendar
from core.models import Instrument
from market_data.ingestion import MAX_SUBSCRIPTIONS, PollingIngestor
from market_data.models import BadTick, Candle, Tick
from market_data.services import get_latest_price, get_provider, store_history
from market_data.universe import get_seed_symbols, get_seed_universe, yahoo_ticker_for

UTC = ZoneInfo("UTC")
IST = ZoneInfo("Asia/Kolkata")
MARKET_TS = datetime(2026, 1, 5, 4, 30, tzinfo=UTC)  # Monday 09:30 IST

pytestmark = pytest.mark.django_db


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
        upper_circuit=Decimal("3000"),
        lower_circuit=Decimal("2000"),
        effective_from=datetime(2026, 1, 1, tzinfo=UTC).date(),
        is_active=True,
    )


def _clock_at(ts=MARKET_TS):
    clock = Clock(mode=ClockMode.LIVE)
    clock.now = lambda: ts  # type: ignore[method-assign]
    clock._calendar = TradingCalendar(holidays=set())
    return clock


def _provider_with(quotes):
    provider = MagicMock()
    provider.name = "yahoo"
    provider.fetch_ltp.return_value = quotes
    return provider


class TestUniverse:
    def test_seed_has_nifty_50_plus_indices(self):
        universe = get_seed_universe()
        assert len(universe) == 53  # 50 equities + 3 indices
        symbols = {e.symbol for e in universe}
        assert {"RELIANCE", "TCS", "INFY", "NIFTY", "BANKNIFTY", "INDIAVIX"} <= symbols

    def test_yahoo_tickers(self):
        assert yahoo_ticker_for("RELIANCE") == "RELIANCE.NS"
        assert yahoo_ticker_for("NIFTY") == "^NSEI"
        with pytest.raises(KeyError):
            yahoo_ticker_for("NOPE")

    def test_no_duplicate_symbols(self):
        symbols = get_seed_symbols()
        assert len(symbols) == len(set(symbols))


class TestProviderFactory:
    def test_unknown_provider_fails_loudly(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_PROVIDER", "nope")
        with pytest.raises(RuntimeError, match="Unknown MARKET_DATA_PROVIDER"):
            get_provider()

    def test_default_is_yahoo(self):
        assert get_provider().name == "yahoo"


class TestPollingIngestor:
    def test_accepted_tick_stored_and_cached(self, reliance):
        quotes = {"RELIANCE": {"ltp": Decimal("2500"), "ts": MARKET_TS, "qty": 5, "volume": 1000}}
        ingestor = PollingIngestor(
            _provider_with(quotes), ["RELIANCE"], clock=_clock_at(), stale_seconds=900
        )
        summary = ingestor.poll_once()
        assert summary == {"polled": 1, "accepted": 1, "rejected": 0, "flagged": 0, "errors": []}
        assert Tick.objects.filter(symbol="RELIANCE").count() == 1
        assert get_latest_price("RELIANCE")["ltp"] == Decimal("2500")

    def test_outside_hours_goes_to_bad_ticks(self, reliance):
        night = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)  # 19:30 IST
        quotes = {"RELIANCE": {"ltp": Decimal("2500"), "ts": night, "volume": 10}}
        ingestor = PollingIngestor(
            _provider_with(quotes), ["RELIANCE"], clock=_clock_at(night), stale_seconds=900
        )
        summary = ingestor.poll_once()
        assert summary["accepted"] == 0
        assert summary["rejected"] == 1
        bad = BadTick.objects.get(symbol="RELIANCE")
        assert bad.reason == "OUTSIDE_MKT"

    def test_missing_instrument_row_never_drops_silently(self):
        quotes = {"TCS": {"ltp": Decimal("4000"), "ts": MARKET_TS, "volume": 10}}
        ingestor = PollingIngestor(
            _provider_with(quotes), ["TCS"], clock=_clock_at(), stale_seconds=900
        )
        summary = ingestor.poll_once()
        assert summary["rejected"] == 1
        assert BadTick.objects.filter(symbol="TCS").exists()
        assert Tick.objects.count() == 0

    def test_provider_failure_becomes_backoff_not_raise(self, reliance):
        provider = MagicMock()
        provider.name = "yahoo"
        provider.fetch_ltp.side_effect = ConnectionError("yahoo down")
        ingestor = PollingIngestor(provider, ["RELIANCE"], clock=_clock_at())
        summary = ingestor.poll_once()
        assert summary["accepted"] == 0
        assert summary["errors"] != []

    def test_subscription_limit_enforced(self):
        with pytest.raises(ValueError, match="allows"):
            PollingIngestor(MagicMock(), [f"S{i}" for i in range(MAX_SUBSCRIPTIONS + 1)])

    def test_heartbeat_detects_silence(self, reliance):
        quotes = {"RELIANCE": {"ltp": Decimal("2500"), "ts": MARKET_TS, "volume": 10}}
        ingestor = PollingIngestor(
            _provider_with(quotes), ["RELIANCE"], clock=_clock_at(), stale_seconds=60
        )
        ingestor.poll_once()
        later = MARKET_TS + timedelta(minutes=5)
        assert ingestor.silent_symbols(later) == ["RELIANCE"]
        assert ingestor.silent_symbols(MARKET_TS) == []

    def test_poll_maintains_1m_candles_and_closes_earlier_minute(self, reliance):
        from market_data.models import Candle

        first_ts = MARKET_TS
        second_ts = MARKET_TS + timedelta(minutes=1)
        provider = _provider_with(
            {"RELIANCE": {"ltp": Decimal("2500"), "ts": first_ts, "volume": 100}}
        )
        ingestor = PollingIngestor(provider, ["RELIANCE"], clock=_clock_at(first_ts))
        ingestor.poll_once()
        provider.fetch_ltp.return_value = {
            "RELIANCE": {"ltp": Decimal("2505"), "ts": second_ts, "volume": 150}
        }
        ingestor._prev["RELIANCE"]["exchange_ts"] = first_ts
        ingestor.clock.now = lambda: second_ts  # type: ignore[method-assign]
        ingestor.poll_once()

        candles = list(Candle.objects.filter(symbol="RELIANCE", timeframe="1m").order_by("open_ts"))
        assert len(candles) == 2
        assert candles[0].is_complete is True  # closed when the next minute arrived
        assert candles[1].close_price == Decimal("2505")
        assert candles[1].volume == 50


class TestStoreHistory:
    def test_idempotent_backfill(self, reliance):
        bars = [
            {
                "ts": datetime(2026, 1, 5, 3, 45, tzinfo=UTC),
                "open": Decimal("2500"),
                "high": Decimal("2510"),
                "low": Decimal("2495"),
                "close": Decimal("2505"),
                "volume": 1000,
            }
        ]
        first = store_history("RELIANCE", bars)
        second = store_history("RELIANCE", bars)
        assert first["created"] >= 1
        assert second["created"] == 0
        assert Candle.objects.filter(symbol="RELIANCE").count() == first["created"]

    def test_missing_instrument_raises_instead_of_silent_skip(self):
        with pytest.raises(ValueError, match="sync_master"):
            store_history("NOPE", [])

    def test_latest_price_db_fallback(self, reliance):
        from django.core.cache import cache

        cache.clear()
        Tick.objects.create(
            instrument=reliance,
            symbol="RELIANCE",
            exchange="NSE",
            exchange_ts=MARKET_TS,
            received_ts=MARKET_TS,
            ltp=Decimal("2510"),
            volume_traded=500,
            source="yahoo",
        )
        assert get_latest_price("RELIANCE")["ltp"] == Decimal("2510")
        assert get_latest_price("UNKNOWN") is None
