"""Operator log for crashes, forced restarts, and interventions."""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from core.models import TrialDay


class Command(BaseCommand):
    help = "Log trial incidents: --crash | --restart | --note TEXT (exactly one)."

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default="", help="YYYY-MM-DD (default: today)")
        parser.add_argument("--crash", action="store_true")
        parser.add_argument("--restart", action="store_true")
        parser.add_argument("--note", type=str, default="")

    def handle(self, *args, **options):
        flags = [options["crash"], options["restart"], bool(options["note"])]
        if sum(flags) != 1:
            raise CommandError("Pass exactly one of --crash, --restart, --note TEXT.")
        day = date.fromisoformat(options["date"]) if options["date"] else date.today()
        row, _ = TrialDay.objects.get_or_create(trial_date=day)
        if options["crash"]:
            row.crash_count += 1
            row.operator_notes += f"\n[{day}] CRASH logged by operator."
        elif options["restart"]:
            row.restarts += 1
            row.operator_notes += f"\n[{day}] Forced restart verified recovered."
        else:
            row.operator_notes += f"\n[{day}] {options['note']}"
        row.save()
        self.stdout.write(
            self.style.SUCCESS(
                f"Logged on {row}: crashes={row.crash_count} restarts={row.restarts}"
            )
        )
