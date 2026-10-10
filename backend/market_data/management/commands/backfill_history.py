"""Backfill OHLC history for symbols (idempotent)."""

from django.core.management.base import BaseCommand

from market_data.services import get_provider, store_history


class Command(BaseCommand):
    help = "Backfill candle history from the configured provider."

    def add_arguments(self, parser):
        parser.add_argument("--symbols", type=str, default="RELIANCE,TCS,INFY,NIFTY")
        parser.add_argument("--interval", type=str, default="1d")
        parser.add_argument("--days", type=int, default=30)

    def handle(self, *args, **options):
        provider = get_provider()
        symbols = [s.strip().upper() for s in options["symbols"].split(",") if s.strip()]
        for symbol in symbols:
            bars = provider.fetch_history(  # type: ignore[attr-defined]
                symbol, interval=options["interval"], period_days=options["days"]
            )
            result = store_history(symbol, bars, source=getattr(provider, "name", "yahoo"))
            self.stdout.write(self.style.SUCCESS(f"{symbol}: {result}"))
