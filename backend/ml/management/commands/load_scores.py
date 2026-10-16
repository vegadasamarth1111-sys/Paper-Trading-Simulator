"""Ingest a published ranking file into the score store (idempotent)."""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from ml.ingest import ingest_scores


class Command(BaseCommand):
    help = (
        "Load a ranking artifact: --file scores.csv --model-version v "
        "--score-date D --horizon 1d --features-as-of F"
    )

    def add_arguments(self, parser):
        parser.add_argument("--file", type=str, required=True)
        parser.add_argument("--model-version", type=str, required=True)
        parser.add_argument("--score-date", type=str, required=True)
        parser.add_argument("--horizon", type=str, default="1d")
        parser.add_argument("--features-as-of", type=str, required=True)

    def handle(self, *args, **options):
        try:
            score_date = date.fromisoformat(options["score_date"])
            features_as_of = date.fromisoformat(options["features_as_of"])
        except ValueError as exc:
            raise CommandError(f"dates must be YYYY-MM-DD: {exc}") from exc
        try:
            summary = ingest_scores(
                path=options["file"],
                model_version=options["model_version"],
                score_date=score_date,
                horizon=options["horizon"],
                features_as_of=features_as_of,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"{summary['symbols']} symbols ({summary['created']} new, "
                f"{summary['updated']} updated) [{summary['model_version']}]"
            )
        )
