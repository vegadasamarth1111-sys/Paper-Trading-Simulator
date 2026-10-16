"""Backtest a baseline strategy over stored closed candles."""

from django.core.management.base import BaseCommand

from engine.loaders import load_closed_candles
from engine.management.commands.run_common import build_runner, parse_args


class Command(BaseCommand):
    help = "Backtest a strategy over closed candles (deterministic)."

    def add_arguments(self, parser):
        parser.add_argument("--account", type=str, required=True)
        parser.add_argument("--strategy", type=str, default="sma_cross")
        parser.add_argument("--symbols", type=str, default="RELIANCE")
        parser.add_argument("--timeframe", type=str, default="1m")
        parser.add_argument("--start", type=str, required=True)
        parser.add_argument("--end", type=str, required=True)
        parser.add_argument("--params", type=str, default="")
        parser.add_argument("--risk-pct", type=str, default="0.01")

    def handle(self, *args, **options):
        start, end, symbols, params = parse_args(options)
        runner, instruments, live_feed = build_runner(
            mode="backtest",
            account_id=options["account"],
            strategy_name=options["strategy"],
            params=params,
            symbols=symbols,
            risk_pct=options["risk_pct"],
            data_version=(f"candles:{options['timeframe']}:{start.isoformat()}:{end.isoformat()}"),
        )
        candles_by_symbol = {}
        total = 0
        for symbol in symbols:
            bars = load_closed_candles(symbol, options["timeframe"], start, end)
            candles_by_symbol[symbol] = bars
            total += len(bars)
        if total == 0:
            self.stdout.write(self.style.WARNING("No closed candles in range; nothing ran."))
            return
        result = runner.run_backtest(candles_by_symbol)
        self.stdout.write(
            self.style.SUCCESS(
                f"run={result.run_id} signals={result.signals} "
                f"orders={result.orders_placed}/{result.orders_filled} "
                f"trades={result.trades} realized={result.realized_pnl} "
                f"charges={result.total_charges} balance={result.final_balance}"
            )
        )
