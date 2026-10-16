"""Shared run-command plumbing (backtest + replay)."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.management.base import CommandError

UTC = ZoneInfo("UTC")


def parse_args(options) -> tuple[datetime, datetime, list[str], dict]:
    """Common --symbols/--start/--end/--params parsing for run commands."""
    import json

    try:
        start = datetime.fromisoformat(options["start"])
        end = datetime.fromisoformat(options["end"])
    except ValueError as exc:
        raise CommandError(f"bad --start/--end (ISO format): {exc}") from exc
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    if end <= start:
        raise CommandError("--end must be after --start")
    symbols = [s.strip().upper() for s in options["symbols"].split(",") if s.strip()]
    if not symbols:
        raise CommandError("--symbols must name at least one symbol")
    try:
        params = json.loads(options["params"]) if options["params"] else {}
    except ValueError as exc:
        raise CommandError(f"bad --params (JSON object): {exc}") from exc
    if not isinstance(params, dict):
        raise CommandError("--params must be a JSON object")
    return start, end, symbols, params


def build_runner(*, mode, account_id, strategy_name, params, symbols, risk_pct, data_version):
    """Assemble strategy + broker + clock + runner (commands and tasks share this)."""
    from broker.paper import PaperBroker
    from core.clock import Clock, ClockMode
    from core.models.accounts import Account
    from core.models.strategies import Strategy
    from engine.loaders import resolve_instruments
    from engine.runner import EventRunner, RunSpec
    from engine.strategies.registry import get_strategy

    account = Account.objects.get(id=account_id)
    strategy = get_strategy(strategy_name, params)
    instruments = resolve_instruments(symbols)
    live_feed: dict[str, Decimal] = {}

    def price_of(symbol: str) -> Decimal | None:
        return live_feed.get(symbol.upper())

    clock_mode = ClockMode.BACKTEST if mode == "backtest" else ClockMode.REPLAY
    clock = Clock(mode=clock_mode)
    broker = PaperBroker(clock=clock, price_source=lambda inst: price_of(inst.symbol))
    strategy_row, _ = Strategy.objects.get_or_create(
        account=account,
        name=f"{strategy_name}",
        defaults={
            "class_name": strategy.version,
            "allocated_capital": account.current_balance + account.unrealized_pnl,
        },
    )
    spec = RunSpec(
        strategy_name=strategy_name,
        params=dict(strategy.params),
        symbols=[s.upper() for s in symbols],
        mode=mode,
        risk_pct=Decimal(str(risk_pct)),
        data_version=data_version,
        account_id=str(account.id),
    )
    runner = EventRunner(
        spec=spec,
        strategy=strategy,
        account=account,
        strategy_row=strategy_row,
        broker=broker,
        clock=clock,
        price_of=price_of,
        live_feed=live_feed,
    )
    return runner, instruments, live_feed
