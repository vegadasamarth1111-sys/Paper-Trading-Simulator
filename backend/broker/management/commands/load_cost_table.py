"""Seed the versioned cost schedule (idempotent)."""

from datetime import date

from django.core.management.base import BaseCommand

from broker.models import CostTable


class Command(BaseCommand):
    help = "Seed the Zerodha-style cost table (safe to re-run)."

    def handle(self, *args, **options):
        row, created = CostTable.objects.get_or_create(
            version="zerodha-style-2026-01",
            defaults={"effective_from": date(2026, 1, 1), "is_active": True},
        )
        verb = "Seeded" if created else "Already present"
        self.stdout.write(self.style.SUCCESS(f"{verb}: {row}"))
