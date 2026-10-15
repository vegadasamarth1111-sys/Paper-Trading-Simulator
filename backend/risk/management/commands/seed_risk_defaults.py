"""Seed documented risk-limit defaults for an account (idempotent)."""

from django.core.management.base import BaseCommand

from risk.seed import seed_account_defaults


class Command(BaseCommand):
    help = "Seed account-wide risk limits (never overwrites tuned rows)."

    def add_arguments(self, parser):
        parser.add_argument("--account", type=str, required=True, help="Account UUID")

    def handle(self, *args, **options):
        result = seed_account_defaults(options["account"])
        self.stdout.write(
            self.style.SUCCESS(
                f"created={len(result['created'])} existing={len(result['existing'])}"
            )
        )
