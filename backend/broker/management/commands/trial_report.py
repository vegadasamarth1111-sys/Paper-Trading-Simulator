"""Print the acceptance verdict (plan section 13, production-ready list)."""

from django.core.management.base import BaseCommand

from broker.trial import evaluate_acceptance


class Command(BaseCommand):
    help = "Evaluate live-paper trial acceptance criteria."

    def add_arguments(self, parser):
        parser.add_argument("--min-days", type=int, default=30)
        parser.add_argument("--tolerance", type=str, default="15")

    def handle(self, *args, **options):
        report = evaluate_acceptance(
            min_days=options["min_days"], tolerance_pct=options["tolerance"]
        )
        for criterion in report.criteria:
            # "ok" below is a display marker, not a credential.
            marker = {"PASS": "ok", "FAIL": "FAIL", "PENDING": "...."}.get(  # nosec B105
                criterion.status, "??"
            )
            self.stdout.write(f"[{marker}] {criterion.name}: {criterion.detail}")
        style = self.style.SUCCESS if report.overall == "PASS" else self.style.WARNING
        self.stdout.write(style(f"Overall after {report.days} days: {report.overall}"))
