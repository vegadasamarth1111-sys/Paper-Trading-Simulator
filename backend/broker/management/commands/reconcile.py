"""Nightly money reconciliation across all active accounts."""

from django.core.management.base import BaseCommand

from broker.ledger import reconcile_account
from core.models.accounts import Account


class Command(BaseCommand):
    help = "Rebuild balances/positions from fills and report mismatches."

    def handle(self, *args, **options):
        failures = 0
        for account in Account.objects.filter(is_active=True):
            report = reconcile_account(account)
            verdict = "OK" if report.ok else "MISMATCH"
            self.stdout.write(f"{account.id} [{verdict}]")
            for mismatch in report.mismatches:
                failures += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"  {mismatch.area} {mismatch.key}: "
                        f"stored={mismatch.stored} rebuilt={mismatch.rebuilt}"
                    )
                )
        if failures:
            raise SystemExit(f"{failures} reconciliation mismatches (see above)")
        self.stdout.write(self.style.SUCCESS("All accounts reconcile."))
