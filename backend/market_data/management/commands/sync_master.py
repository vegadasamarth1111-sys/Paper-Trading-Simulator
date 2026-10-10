"""Sync the instrument master from the configured provider."""

from django.core.management.base import BaseCommand

from instruments.loader import InstrumentLoader
from market_data.services import get_provider


class Command(BaseCommand):
    help = "Sync instrument master (Yahoo seed or Angel ScripMaster)."

    def handle(self, *args, **options):
        provider = get_provider()
        # Both providers speak the loader's instrument-dict shape.
        summary = InstrumentLoader(provider=provider).sync_instruments()  # type: ignore[arg-type]
        self.stdout.write(self.style.SUCCESS(f"Master sync: {summary}"))
