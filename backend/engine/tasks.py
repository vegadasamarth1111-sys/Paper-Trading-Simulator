"""
Paper Trading Simulator — Engine Celery tasks (Phase 4)
=========================================================
Backtest/replay over the queue (Phase 5 dashboard triggers these).
Deterministic and idempotent per input: re-running the same range
creates a NEW StrategyRun (runs are append-only history, never updated).
"""

import logging

from celery import shared_task

logger = logging.getLogger("core")


@shared_task(name="engine.backtest")
def backtest_task(
    account_id: str,
    strategy_name: str,
    symbols: list[str],
    start: str,
    end: str,
    timeframe: str = "1m",
    params: dict | None = None,
    risk_pct: str = "0.01",
) -> dict:
    """Run a backtest over stored closed candles."""
    from datetime import datetime

    from engine.loaders import load_closed_candles
    from engine.management.commands.run_common import build_runner

    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    runner, _, _ = build_runner(
        mode="backtest",
        account_id=account_id,
        strategy_name=strategy_name,
        params=params or {},
        symbols=symbols,
        risk_pct=risk_pct,
        data_version=f"candles:{timeframe}:{start}:{end}",
    )
    candles = {s: load_closed_candles(s, timeframe, start_dt, end_dt) for s in symbols}
    result = runner.run_backtest(candles)
    return _summarize(result)


@shared_task(name="engine.replay")
def replay_task(
    account_id: str,
    strategy_name: str,
    symbols: list[str],
    start: str,
    end: str,
    params: dict | None = None,
    risk_pct: str = "0.01",
) -> dict:
    """Replay recorded ticks for a past session."""
    from datetime import datetime

    from engine.loaders import load_ticks
    from engine.management.commands.run_common import build_runner

    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    runner, _, _ = build_runner(
        mode="replay",
        account_id=account_id,
        strategy_name=strategy_name,
        params=params or {},
        symbols=symbols,
        risk_pct=risk_pct,
        data_version=f"ticks:{start}:{end}",
    )
    ticks = {s: load_ticks(s, start_dt, end_dt) for s in symbols}
    result = runner.run_replay(ticks)
    return _summarize(result)


def _summarize(result) -> dict:
    return {
        "run_id": result.run_id,
        "signals": result.signals,
        "signals_skipped": result.signals_skipped,
        "orders_placed": result.orders_placed,
        "orders_filled": result.orders_filled,
        "trades": result.trades,
        "realized_pnl": str(result.realized_pnl),
        "total_charges": str(result.total_charges),
        "final_balance": str(result.final_balance),
    }
