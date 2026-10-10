"""Poll live prices once (Beat) or forever (dev/supervisor)."""

from decouple import config
from django.core.management.base import BaseCommand

from market_data.ingestion import PollingIngestor
from market_data.services import get_provider
from market_data.universe import get_seed_symbols


class Command(BaseCommand):
    help = "Poll market-data provider for the watchlist (Phase 1 free tier)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single poll cycle and exit (used by Celery Beat / CI).",
        )
        parser.add_argument(
            "--symbols",
            type=str,
            default="",
            help="Comma-separated symbols to poll (default: seed universe).",
        )

    def handle(self, *args, **options):
        provider = get_provider()
        symbols = [
            s.strip().upper() for s in options["symbols"].split(",") if s.strip()
        ] or get_seed_symbols()
        ingestor = PollingIngestor(
            provider,  # type: ignore[arg-type]
            symbols,
            poll_interval_seconds=config("POLL_INTERVAL_SECONDS", default=60, cast=int),
            stale_seconds=config("STALE_TICK_SECONDS", default=900, cast=int),
        )
        if options["once"]:
            summary = ingestor.poll_once()
            self.stdout.write(self.style.SUCCESS(f"Poll cycle: {summary}"))
            return
        ingestor.run_forever()
