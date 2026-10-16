"""
Tests for the ranking strategy + version propagation (Phase 6 exit gate).

- One ranking per trading day (never per bar): the store is hit once
  per date, keeping models out of the hot loop by construction.
- Every emitted signal carries the publication's model_version, the
  Signal row persists it, and the StrategyRun records it.
- A full replay run on published scores trades deterministically.
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from core.models import Signal, StrategyRun
from engine.base import CandleView
from engine.strategies.registry import get_strategy
from ml.models import ModelScore

UTC = ZoneInfo("UTC")

pytestmark = pytest.mark.django_db


def _candle(symbol, day, minute=30, close="20"):
    px = Decimal(close)
    return CandleView(
        symbol=symbol,
        timeframe="1m",
        open_ts=datetime(2026, 1, day, 4, minute, tzinfo=UTC),
        open_price=px,
        high_price=px + 1,
        low_price=px - 1,
        close_price=px,
        volume=1000,
    )


def _publish(symbols=("RELIANCE", "TCS", "INFY"), version="xgb-rank@v9"):
    for i, symbol in enumerate(symbols):
        ModelScore.objects.create(
            score_date=datetime(2026, 1, 8, tzinfo=UTC).date(),
            horizon="1d",
            symbol=symbol,
            score=Decimal(f"0.{9 - i}"),
            model_version=version,
            features_as_of=datetime(2026, 1, 7, tzinfo=UTC).date(),
        )


class TestRankingStrategy:
    def test_top_n_long_only(self):
        _publish()
        strat = get_strategy("ranking", {"top_n": 2, "bottom_n": 0})
        history = [_candle("RELIANCE", 9)]
        (signal,) = strat.on_candle(_candle("RELIANCE", 9, minute=31), history)
        assert signal.symbol == "RELIANCE"
        assert signal.direction == "BUY"
        assert signal.model_version == "xgb-rank@v9"
        assert signal.stop_loss < signal.entry_price < signal.target

    def test_unranked_symbol_silent(self):
        _publish()
        strat = get_strategy("ranking", {"top_n": 1, "bottom_n": 0})
        # Only RELIANCE ranks first; TCS hears nothing on its own bar.
        assert strat.on_candle(_candle("TCS", 9, minute=31), [_candle("TCS", 9)]) == []

    def test_shorts_when_enabled(self):
        _publish()
        strat = get_strategy("ranking", {"top_n": 1, "bottom_n": 1})
        buy = strat.on_candle(_candle("RELIANCE", 9, minute=31), [_candle("RELIANCE", 9)])
        assert [s.direction for s in buy] == ["BUY"]
        sell = strat.on_candle(_candle("INFY", 9, minute=31), [_candle("INFY", 9)])
        assert [s.direction for s in sell] == ["SELL"]

    def test_once_per_symbol_per_day(self):
        _publish()
        strat = get_strategy("ranking", {"top_n": 2})
        history = [_candle("RELIANCE", 9)]
        first = strat.on_candle(_candle("RELIANCE", 9, minute=31), history)
        history.append(_candle("RELIANCE", 9, minute=31))
        assert strat.on_candle(_candle("RELIANCE", 9, minute=32), history) == []
        assert len(first) == 1

    def test_unpublished_day_silent(self):
        strat = get_strategy("ranking", {"top_n": 2})
        history = [_candle("RELIANCE", 9)]
        assert strat.on_candle(_candle("RELIANCE", 9, minute=31), history) == []
        assert strat.run_model_version == ""

    def test_provider_cached_per_date(self):
        _publish()
        strat = get_strategy("ranking", {"top_n": 1})
        history = [_candle("RELIANCE", 9)]
        strat.on_candle(_candle("RELIANCE", 9, minute=31), history)
        history.append(_candle("RELIANCE", 9, minute=31))
        strat.on_candle(_candle("RELIANCE", 9, minute=32), history)
        # One store hit per date: the provider caches by trade date, so the
        # second bar reuses the ranking instead of re-querying.
        assert list(strat.provider._cache) == [datetime(2026, 1, 9, tzinfo=UTC).date()]

    def test_registry_knows_ranking(self):
        from engine.strategies.registry import REGISTRY

        assert REGISTRY["ranking"].__name__ == "RankingStrategy"


class TestVersionPropagation:
    """Exit gate: signals logged with model version, end to end."""

    def test_run_records_model_version(self, account):
        from broker.paper import PaperBroker
        from core.clock import Clock, ClockMode
        from core.models import Account, Instrument
        from engine.runner import EventRunner, RunSpec

        for symbol in ("RELIANCE", "TCS", "INFY"):
            Instrument.objects.create(
                symbol=symbol,
                exchange="NSE",
                segment="EQ",
                name=symbol,
                exchange_token="1",
                lot_size=1,
                tick_size=Decimal("0.05"),
                effective_from=datetime(2024, 1, 1, tzinfo=UTC).date(),
            )
        _publish()
        strategy = get_strategy("ranking", {"top_n": 2})
        clock = Clock(mode=ClockMode.BACKTEST)
        feed: dict = {}
        quote_ts: dict = {}
        broker = PaperBroker(
            clock=clock,
            price_source=lambda inst: feed.get(inst.symbol),
            quote_ts_source=lambda inst: quote_ts.get(inst.symbol),
        )
        strategy_row, _ = strategy_row_for(account, strategy)
        spec = RunSpec(
            strategy_name="ranking",
            params=dict(strategy.params),
            symbols=["RELIANCE", "TCS", "INFY"],
            mode="backtest",
            risk_pct=Decimal("0.001"),
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
        runner._quote_ts = quote_ts
        history: dict[str, list] = {"RELIANCE": [], "TCS": [], "INFY": []}
        candles: dict[str, list] = {}
        for symbol in history:
            candles[symbol] = [
                _candle(symbol, 9, minute=30 + i, close=str(20 + i * 0.1)) for i in range(5)
            ]
        result = runner.run_backtest(candles)
        assert result.signals >= 2
        assert result.orders_placed >= 2
        executed = Signal.objects.filter(was_executed=True)
        assert executed.exists()
        assert {s.model_version for s in executed} == {"xgb-rank@v9"}
        run = StrategyRun.objects.get(id=result.run_id)
        assert run.model_version == "xgb-rank@v9"
        _ = Account


def strategy_row_for(account, strategy):
    from core.models import Strategy

    return Strategy.objects.get_or_create(
        account=account,
        name="ranking-test",
        defaults={"class_name": strategy.version, "allocated_capital": Decimal("1000000")},
    )
