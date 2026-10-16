"""Write an encrypted database backup (refuses plaintext)."""

from decouple import config
from django.core.management.base import BaseCommand, CommandError

from core.backups import create_backup


class Command(BaseCommand):
    help = "Write an encrypted backup + manifest (needs BACKUP_ENCRYPTION_KEY)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            type=str,
            default="",
            help="Backup directory (default: BACKUP_DIR setting)",
        )

    def handle(self, *args, **options):
        key = config("BACKUP_ENCRYPTION_KEY", default="")
        directory = options["dir"] or config("BACKUP_DIR", default="backups")
        try:
            summary = create_backup(key=key, directory=directory)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"{summary['path']}: {summary['objects']} objects, "
                f"{summary['bytes']} bytes, sha256 {summary['sha256'][:16]}..."
            )
        )
