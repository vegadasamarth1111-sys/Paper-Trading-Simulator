"""Collect one trial day's evidence (nightly pipeline step)."""

from datetime import date

from django.core.management.base import BaseCommand

from broker.trial import run_trial_day


class Command(BaseCommand):
    help = "Record trial evidence for a trading day (idempotent per date)."

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default="", help="YYYY-MM-DD (default: today)")
        parser.add_argument("--replay-strategy", type=str, default="")
        parser.add_argument("--replay-symbols", type=str, default="")
        parser.add_argument("--tolerance", type=str, default="15")

    def handle(self, *args, **options):
        day = date.fromisoformat(options["date"]) if options["date"] else date.today()
        symbols = [s.strip().upper() for s in options["replay_symbols"].split(",") if s.strip()]
        row = run_trial_day(
            day,
            replay_strategy=options["replay_strategy"] or None,
            replay_symbols=symbols,
            tolerance_pct=options["tolerance"],
        )
        self.stdout.write(self.style.SUCCESS(f"Trial day recorded: {row}"))
