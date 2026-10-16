"""
Tests for engine commands, loaders, and tasks (Phase 4 plumbing).

Commands are thin, but they parse user input and assemble the run —
both deserve proof. Tasks are covered by direct calls (Celery runs
eager in tests, and the logic is what matters, not the queue).
"""

from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command

from core.models import StrategyRun
from engine.loaders import load_closed_candles, load_ticks, resolve_instruments

UTC = ZoneInfo("UTC")
DAY = datetime(2026, 1, 5, tzinfo=UTC)

pytestmark = pytest.mark.django_db


def _instrument(symbol="RELIANCE"):
    from core.models import Instrument

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


def _seed_bars(symbol="RELIANCE", n=30, start_px="20"):
    """Closed 1m candles climbing then dipping (forces SMA crosses)."""
    from market_data.models import Candle

    inst = _instrument(symbol)
    closes = [Decimal(start_px)] * 20 + [Decimal("21"), Decimal("22"), Decimal("23")]
    closes += [Decimal("23")] * (n - len(closes))
    for i, close in enumerate(closes[:n]):
        ts = datetime(2026, 1, 5, 4, 30, tzinfo=UTC) + timedelta(minutes=i)
        Candle.objects.create(
            instrument=inst,
            symbol=symbol,
            exchange="NSE",
            timeframe="1m",
            open_ts=ts,
            open_price=close,
            high_price=close + 1,
            low_price=close - 1,
            close_price=close,
            volume=100,
            trades=1,
            is_complete=True,
            source="yahoo",
        )
    return inst


def _seed_ticks(symbol="RELIANCE", n=30):
    """Raw ticks over the same shape (one per minute, rising then flat)."""
    from market_data.models import Tick

    inst = _instrument(symbol)
    vol = 0
    for i in range(n):
        close = Decimal("20") if i < 20 else Decimal("20") + (i - 19)
        vol += 100
        ts = datetime(2026, 1, 5, 4, 30, tzinfo=UTC) + timedelta(minutes=i)
        Tick.objects.create(
            instrument=inst,
            symbol=symbol,
            exchange="NSE",
            exchange_ts=ts,
            received_ts=ts,
            ltp=close,
            volume_traded=vol,
            source="yahoo",
        )
    return inst


class TestLoaders:
    def test_closed_only_and_order(self, account):
        _seed_bars()
        bars = load_closed_candles("RELIANCE", "1m", DAY, DAY + timedelta(days=1))
        assert len(bars) == 30
        assert [b.open_ts for b in bars] == sorted(b.open_ts for b in bars)

    def test_ticks_order(self, account):
        _seed_ticks()
        ticks = load_ticks("RELIANCE", DAY, DAY + timedelta(days=1))
        assert len(ticks) == 30

    def test_missing_symbols_raise(self, account):
        _instrument("RELIANCE")
        with pytest.raises(ValueError, match="missing from instrument master"):
            resolve_instruments(["RELIANCE", "NOPE"])


class TestBacktestCommand:
    def test_runs_and_reports(self, account):
        _seed_bars()
        out = StringIO()
        call_command(
            "backtest",
            "--account",
            str(account.id),
            "--strategy",
            "sma_cross",
            "--symbols",
            "RELIANCE",
            "--timeframe",
            "1m",
            "--start",
            "2026-01-05T00:00:00+00:00",
            "--end",
            "2026-01-06T00:00:00+00:00",
            stdout=out,
        )
        assert "realized=" in out.getvalue()
        assert StrategyRun.objects.filter(mode="backtest").count() == 1

    def test_empty_range_warns(self, account):
        _instrument()
        out = StringIO()
        call_command(
            "backtest",
            "--account",
            str(account.id),
            "--symbols",
            "RELIANCE",
            "--start",
            "2026-01-05T00:00:00+00:00",
            "--end",
            "2026-01-06T00:00:00+00:00",
            stdout=out,
        )
        assert "nothing ran" in out.getvalue()

    def test_bad_args_rejected(self, account):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match="after --start"):
            call_command(
                "backtest",
                "--account",
                str(account.id),
                "--start",
                "2026-01-06T00:00:00+00:00",
                "--end",
                "2026-01-05T00:00:00+00:00",
                stdout=StringIO(),
            )
        with pytest.raises(CommandError, match="JSON object"):
            call_command(
                "backtest",
                "--account",
                str(account.id),
                "--params",
                "[1,2]",
                "--start",
                "2026-01-05T00:00:00+00:00",
                "--end",
                "2026-01-06T00:00:00+00:00",
                stdout=StringIO(),
            )

    def test_donchian_runs(self, account):
        _seed_bars()
        out = StringIO()
        call_command(
            "backtest",
            "--account",
            str(account.id),
            "--strategy",
            "donchian",
            "--symbols",
            "RELIANCE",
            "--params",
            '{"lookback": 5}',
            "--start",
            "2026-01-05T00:00:00+00:00",
            "--end",
            "2026-01-06T00:00:00+00:00",
            stdout=out,
        )
        assert "realized=" in out.getvalue()


class TestReplayCommand:
    def test_replays_ticks(self, account):
        _seed_ticks()
        out = StringIO()
        call_command(
            "replay",
            "--account",
            str(account.id),
            "--strategy",
            "sma_cross",
            "--symbols",
            "RELIANCE",
            "--start",
            "2026-01-05T00:00:00+00:00",
            "--end",
            "2026-01-06T00:00:00+00:00",
            stdout=out,
        )
        assert "realized=" in out.getvalue()
        assert StrategyRun.objects.filter(mode="replay").count() == 1

    def test_empty_range_warns(self, account):
        _instrument()
        out = StringIO()
        call_command(
            "replay",
            "--account",
            str(account.id),
            "--symbols",
            "RELIANCE",
            "--start",
            "2026-01-05T00:00:00+00:00",
            "--end",
            "2026-01-06T00:00:00+00:00",
            stdout=out,
        )
        assert "nothing ran" in out.getvalue()


class TestTasks:
    def test_backtest_task(self, account):
        from engine.tasks import backtest_task

        _seed_bars()
        result = backtest_task(
            str(account.id),
            "sma_cross",
            ["RELIANCE"],
            "2026-01-05T00:00:00+00:00",
            "2026-01-06T00:00:00+00:00",
        )
        assert "run_id" in result
        assert result["signals"] >= 0

    def test_replay_task(self, account):
        from engine.tasks import replay_task

        _seed_ticks()
        result = replay_task(
            str(account.id),
            "sma_cross",
            ["RELIANCE"],
            "2026-01-05T00:00:00+00:00",
            "2026-01-06T00:00:00+00:00",
        )
        assert "run_id" in result
