"""Build the daily data-quality report (plan section 4.8)."""

from datetime import date
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand

from market_data.quality import build_daily_report

IST = ZoneInfo("Asia/Kolkata")


class Command(BaseCommand):
    help = "Build the daily data-quality report for a trading day."

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default="", help="YYYY-MM-DD (default: today IST)")
        parser.add_argument("--exchange", type=str, default="NSE")

    def handle(self, *args, **options):
        from datetime import datetime

        day = date.fromisoformat(options["date"]) if options["date"] else datetime.now(IST).date()
        report = build_daily_report(day, exchange=options["exchange"])
        verdict = "SUSPECT" if report.is_suspect else "OK"
        self.stdout.write(self.style.SUCCESS(f"{report} notes={report.notes} [{verdict}]"))
