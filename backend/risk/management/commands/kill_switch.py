"""Halt or release an account from the command line (kill-switch path)."""

from django.core.management.base import BaseCommand

from core.models.accounts import Account
from risk.kill import kill_switch, release_kill_switch


class Command(BaseCommand):
    help = "Activate (--reason) or release (--release) the kill switch."

    def add_arguments(self, parser):
        parser.add_argument("--account", type=str, required=True, help="Account UUID")
        parser.add_argument("--reason", type=str, default="manual halt")
        parser.add_argument("--release", action="store_true")

    def handle(self, *args, **options):
        account = Account.objects.get(id=options["account"])
        if options["release"]:
            release_kill_switch(account=account, reason=options["reason"])
            self.stdout.write(self.style.SUCCESS("Kill switch released."))
            return
        summary = kill_switch(account=account, reason=options["reason"])
        self.stdout.write(self.style.WARNING(f"Halted: {summary}"))
