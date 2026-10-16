"""Restore an encrypted backup after hash verification (needs --force)."""

from decouple import config
from django.core.management.base import BaseCommand, CommandError

from core.backups import restore_backup


class Command(BaseCommand):
    help = "Restore a backup file (verifies manifest hash first)."

    def add_arguments(self, parser):
        parser.add_argument("--file", type=str, required=True)
        parser.add_argument(
            "--force",
            action="store_true",
            help="Required: restore wipes current state first.",
        )

    def handle(self, *args, **options):
        if not options["force"]:
            raise CommandError("Refusing: restore wipes the database; pass --force deliberately.")
        key = config("BACKUP_ENCRYPTION_KEY", default="")
        try:
            summary = restore_backup(key=key, path=options["file"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Restored {summary['objects']} objects."))
