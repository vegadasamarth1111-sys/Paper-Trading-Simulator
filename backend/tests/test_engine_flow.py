"""
Tests for the signal-to-order flow and the determinism gate (Phase 4).

- Closed candles only: incomplete bars never reach strategies.
- Signal -> size -> risk -> broker fills end to end; skips carry reasons.
- THE exit gate: the same tick day replayed twice produces identical
  trades, charges, and balances (UUIDs excluded from the comparison).
"""

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from core.clock import Clock, ClockMode
from core.models import Instrument, Signal, Strategy
from engine.base import CandleView
from engine.runner import EventRunner, RunResult, RunSpec
from engine.strategies.registry import get_strategy

UTC = ZoneInfo("UTC")
DAY = datetime(2026, 1, 5, tzinfo=UTC)  # a Monday
OPEN_IST = datetime(2026, 1, 5, 4, 15, tzinfo=UTC)  # 09:45 IST

pytestmark = pytest.mark.django_db


def _instrument(symbol="RELIANCE"):
    return Instrument.objects.create(
        symbol=symbol,
        exchange="NSE",
        segment="EQ",
        name=symbol,
        exchange_token="1",
        lot_size=1,
        tick_size=Decimal("0.05"),
        effective_from=datetime(2024, 1, 1, tzinfo=UTC).date(),
    )


def _candles(symbol, closes, start_minute=30):
    """Closed 1m CandleViews from 09:30 IST upward."""
    from datetime import timedelta

    base = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)
    out = []
    for i, close in enumerate(closes):
        px = Decimal(str(close))
        ts = base + timedelta(minutes=start_minute + i)
        out.append(
            CandleView(
                symbol=symbol,
                timeframe="1m",
                open_ts=ts,
                open_price=px,
                high_price=px + Decimal("0.5"),
                low_price=px - Decimal("0.5"),
                close_price=px,
                volume=1000,
            )
        )
    return out


def _ticks(symbol, closes, start_minute=30):
    from datetime import timedelta

    from engine.base import TickView

    base = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)
    out = []
    vol = 0
    for i, close in enumerate(closes):
        vol += 100
        out.append(
            TickView(
                symbol=symbol,
                exchange_ts=base + timedelta(minutes=start_minute + i),
                ltp=Decimal(str(close)),
                volume_traded=vol,
            )
        )
    return out


def _runner_for(
    account,
    strategy_name="sma_cross",
    params=None,
    mode="backtest",
    risk_pct="0.01",
    symbols=("RELIANCE",),
):
    from broker.paper import PaperBroker

    clock = Clock(mode=ClockMode.BACKTEST if mode == "backtest" else ClockMode.REPLAY)
    feed: dict = {}
    quote_ts: dict = {}
    broker = PaperBroker(
        clock=clock,
        price_source=lambda inst: feed.get(inst.symbol),
        quote_ts_source=lambda inst: quote_ts.get(inst.symbol),
    )
    strategy = get_strategy(strategy_name, params)
    strategy_row, _ = Strategy.objects.get_or_create(
        account=account,
        name=f"{strategy_name}-test",
        defaults={"class_name": strategy.version, "allocated_capital": Decimal("1000000")},
    )
    spec = RunSpec(
        strategy_name=strategy_name,
        params=dict(strategy.params),
        symbols=list(symbols),
        mode=mode,
        risk_pct=Decimal(risk_pct),
        data_version="test",
        account_id=str(account.id),
    )
    runner = EventRunner(
        spec=spec,
        strategy=strategy,
        account=account,
        strategy_row=strategy_row,
        broker=broker,
        clock=clock,
        price_of=lambda s: feed.get(s.upper()),
        live_feed=feed,
    )
    runner._quote_ts = quote_ts  # simulated quote timestamps for the stale rule
    return runner


def _fingerprint(result: RunResult) -> tuple:
    """UUID-free comparison key (the determinism contract)."""
    return (
        result.orders_placed,
        result.orders_filled,
        result.signals,
        result.signals_skipped,
        result.trades,
        result.realized_pnl,
        result.total_charges,
        result.final_balance,
        tuple(result.fills),
    )


class TestClosedOnly:
    def test_incomplete_candles_ignored_by_loader(self, account):
        from engine.loaders import load_closed_candles
        from market_data.models import Candle

        inst = _instrument()
        for i, complete in enumerate((True, False)):
            Candle.objects.create(
                instrument=inst,
                symbol="RELIANCE",
                exchange="NSE",
                timeframe="1m",
                open_ts=datetime(2026, 1, 5, 4, 30 + i, tzinfo=UTC),
                open_price=Decimal("20"),
                high_price=Decimal("21"),
                low_price=Decimal("19"),
                close_price=Decimal("20"),
                volume=100,
                trades=1,
                is_complete=complete,
                source="yahoo",
            )
        bars = load_closed_candles("RELIANCE", "1m", DAY, DAY + timedelta(days=1))
        assert len(bars) == 1


class TestBacktestFlow:
    def test_cross_fills_and_links_signal(self, account):
        _instrument()
        # Steady climb then a dip: fast crosses slow on the way up.
        closes = [20] * 20 + [21, 22, 23, 24, 25]
        runner = _runner_for(account, params={"fast": 5, "slow": 20})
        result = runner.run_backtest({"RELIANCE": _candles("RELIANCE", closes)})
        assert result.signals >= 1
        assert result.orders_filled >= 1
        assert result.trades >= 1
        executed = Signal.objects.filter(was_executed=True)
        assert executed.exists()
        assert all(s.order_id is not None for s in executed)
        skipped = Signal.objects.filter(was_executed=False)
        assert all(s.skip_reason for s in skipped)

    def test_flat_market_trades_nothing(self, account):
        _instrument()
        runner = _runner_for(account)
        result = runner.run_backtest({"RELIANCE": _candles("RELIANCE", [20] * 30)})
        assert result.signals == 0
        assert result.orders_placed == 0
        assert result.final_balance == Decimal("1000000.0000")


class TestDeterminism:
    """The Phase 4 exit gate: same day twice -> identical results."""

    def _series(self):
        up = [20 + i * Decimal("0.2") for i in range(25)]
        down = [up[-1] - i * Decimal("0.2") for i in range(1, 16)]
        return [str(p) for p in ([*up, *down])]

    def _run_once(self, account):
        runner = _runner_for(
            account,
            params={"fast": 5, "slow": 20},
            mode="replay",
            risk_pct="0.001",
            symbols=("DET",),
        )
        ticks = _ticks("DET", self._series())
        return runner.run_replay({"DET": ticks})

    def test_replay_twice_identical(self):
        from django.contrib.auth import get_user_model

        from core.models import Account

        _instrument("DET")
        results = []
        for tag in ("A", "B"):
            user = get_user_model().objects.create_user(
                username=f"det-{tag}",
                email=f"{tag}@example.com",
                password="x",  # nosec B106 -- test fixture only
            )
            account = Account.objects.create(user=user)
            results.append(self._run_once(account))
        first, second = (_fingerprint(r) for r in results)
        assert first == second
        # Non-vacuous: the pipeline actually traded.
        assert results[0].trades > 0
        assert results[0].orders_filled > 0

    def test_backtest_twice_identical(self):
        from django.contrib.auth import get_user_model

        from core.models import Account

        _instrument("BTM")
        fingerprints = []
        orders_filled_total = 0
        for tag in ("C", "D"):
            user = get_user_model().objects.create_user(
                username=f"bt-{tag}",
                email=f"{tag}@example.com",
                password="x",  # nosec B106 -- test fixture only
            )
            account = Account.objects.create(user=user)
            runner = _runner_for(
                account,
                params={"fast": 5, "slow": 20},
                mode="backtest",
                risk_pct="0.001",
                symbols=("BTM",),
            )
            closes = self._series()
            result = runner.run_backtest({"BTM": _candles("BTM", closes)})
            fingerprints.append(_fingerprint(result))
            orders_filled_total += result.orders_filled
        assert fingerprints[0] == fingerprints[1]
        assert orders_filled_total > 0  # non-vacuous: it really traded


class TestRunRecords:
    def test_run_record_and_versions(self, account):
        from core.models import StrategyRun

        _instrument()
        runner = _runner_for(account)
        result = runner.run_backtest({"RELIANCE": _candles("RELIANCE", [20] * 25)})
        run = StrategyRun.objects.get(id=result.run_id)
        assert run.is_running is False
        assert run.ended_at is not None
        assert "@4.0.0" in run.strategy_version
        assert run.parameters_snapshot == runner.spec.params
        assert run.data_version == "test"

    def test_unknown_symbol_skips_without_poisoning_run(self, account):
        _instrument()
        runner = _runner_for(account, symbols=("RELIANCE", "NOPE"))
        rising = [20] * 20 + [21, 22, 23, 24, 25]
        result = runner.run_backtest(
            {
                "RELIANCE": _candles("RELIANCE", rising),
                "NOPE": _candles("NOPE", rising),
            }
        )
        # NOPE cannot persist (no master row) but is counted, logged, and
        # never stops RELIANCE from trading.
        assert result.signals_skipped >= 1
        assert result.orders_filled >= 1
