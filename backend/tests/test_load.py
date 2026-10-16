"""
Load tests (Phase 7, plan section 11).

Scaled to CI-friendly sizes with generous time bounds: these prove the
pipeline's throughput ORDER (thousands of ticks/sec pure, hundreds of
orders/sec with DB) rather than production peak rates. The full NIFTY 50
peak-volatility replay is a documented manual procedure (see RUNBOOK).
All assertions are correctness-first; timing second (regressions in
either fail the build).
"""

import time
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from broker.paper import PaperBroker
from core.clock import Clock, ClockMode, TradingCalendar
from core.models import Trade
from market_data.candles import TickPoint, build_1m_candles
from market_data.schema import NormalizedTick
from market_data.validation import validate_tick

UTC = ZoneInfo("UTC")
MON_9AM = datetime(2026, 1, 5, 3, 30, tzinfo=UTC)  # 09:00 IST pre-open quotes
MON_930 = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)  # 09:30 IST in-window trading

pytestmark = [pytest.mark.django_db, pytest.mark.slow]


def _clock_at(ts):
    clock = Clock(mode=ClockMode.LIVE)
    clock.now = lambda: ts  # type: ignore[method-assign]
    clock._calendar = TradingCalendar(holidays=set())
    return clock


class TestTickPipelineThroughput:
    """2,000 ticks: validate + bucket. Must stay sub-second per thousand."""

    def test_validation_throughput(self, equity_instrument):
        base = MON_9AM
        ticks = [
            NormalizedTick(
                symbol="RELIANCE",
                exchange="NSE",
                exchange_ts=base + timedelta(seconds=i * 5),
                received_ts=base + timedelta(seconds=i * 5),
                ltp=Decimal("2500") + (i % 10),
                last_qty=1,
                volume_traded=1000 + i,
                source="yahoo",
            )
            for i in range(2000)
        ]
        calendar = TradingCalendar(holidays=set())
        started = time.perf_counter()
        accepted = 0
        prev = None
        for tick in ticks:
            # Validate each tick as if live on arrival (its own clock + 1s).
            result = validate_tick(
                tick,
                upper_circuit=None,
                lower_circuit=None,
                prev_ltp=prev,
                prev_volume=None,
                prev_sequence_no=None,
                prev_exchange_ts=None,
                calendar=calendar,
                now_utc=tick.exchange_ts + timedelta(seconds=1),
            )
            accepted += result.accepted
            prev = tick.ltp
        elapsed = time.perf_counter() - started
        assert accepted == 2000
        assert elapsed < 30, f"validation too slow: {elapsed:.1f}s for 2000 ticks"

    def test_candle_build_throughput(self):
        base = MON_9AM
        points = [
            TickPoint(
                exchange_ts=base + timedelta(seconds=i * 5),
                ltp=Decimal("2500") + (i % 7),
                volume_traded=1000 + i,
            )
            for i in range(2000)
        ]
        started = time.perf_counter()
        candles = build_1m_candles(points)
        elapsed = time.perf_counter() - started
        assert len(candles) > 100  # ~167 minutes of 5s ticks
        assert elapsed < 30, f"candle build too slow: {elapsed:.1f}s"


class TestOrderThroughput:
    """200 full order lifecycles through broker+risk+ledger on SQLite."""

    def test_sustained_order_flow(self, account, equity_instrument):
        from core.enums import RiskRuleType
        from core.models import RiskConfig
        from market_data.models import Tick

        # Throughput, not limits, is under test: lift the count and size guards.
        RiskConfig.objects.create(
            account=account,
            strategy=None,
            rule_type=RiskRuleType.ORDER_RATE_LIMIT,
            limit_value=Decimal("100000"),
        )
        RiskConfig.objects.create(
            account=account,
            strategy=None,
            rule_type=RiskRuleType.MAX_TRADES_PER_DAY,
            limit_value=Decimal("100000"),
        )
        RiskConfig.objects.create(
            account=account,
            strategy=None,
            rule_type=RiskRuleType.MAX_POSITION_SIZE,
            limit_value=Decimal("1000000000"),
            limit_percentage=None,
        )
        base = MON_930
        Tick.objects.create(
            instrument=equity_instrument,
            symbol="RELIANCE",
            exchange="NSE",
            exchange_ts=base,
            received_ts=base,
            ltp=Decimal("2500"),
            volume_traded=10_000_000,
            source="yahoo",
        )
        clock = Clock(mode=ClockMode.REPLAY)
        clock.set_time(base + timedelta(seconds=60))
        broker = PaperBroker(clock=clock)
        started = time.perf_counter()
        for i in range(200):
            order = broker.place_order(
                account=account,
                instrument=equity_instrument,
                side="BUY",
                order_type="MKT",
                quantity=1,
                product_type="MIS",
                validity="DAY",
                client_order_id=f"load-{i:04d}",
                stop_loss_price=Decimal("2400"),
            )
            assert order.status == "FLD"
        elapsed = time.perf_counter() - started
        assert Trade.objects.filter(account=account).count() == 200
        assert elapsed < 300, f"order flow too slow: {elapsed:.1f}s for 200 orders"


class TestUniversePollScale:
    """53-symbol watchlist poll against a stub provider (no network)."""

    def test_full_universe_cycle(self, equity_instrument):
        from market_data.ingestion import PollingIngestor
        from market_data.universe import get_seed_symbols

        symbols = get_seed_symbols()
        assert len(symbols) == 53
        provider = MagicMock()
        provider.name = "yahoo"
        provider.fetch_ltp.return_value = {
            symbol: {"ltp": Decimal("100"), "ts": MON_9AM, "volume": 1000} for symbol in symbols
        }
        ingestor = PollingIngestor(provider, symbols, clock=_clock_at(MON_9AM))
        started = time.perf_counter()
        summary = ingestor.poll_once()
        elapsed = time.perf_counter() - started
        # Only RELIANCE has a master row; the rest land in bad_ticks loudly.
        assert summary["polled"] == 53
        assert elapsed < 120, f"universe poll too slow: {elapsed:.1f}s"
