"""Full end-of-day run: expire, square off, settle expiries, snapshot."""

from datetime import date

from django.core.management.base import BaseCommand

from broker.eod import expire_day_orders, settle_day, square_off_intraday
from broker.options import settle_expired_options
from broker.paper import PaperBroker
from core.models.accounts import Account
from market_data.services import get_latest_price


class Command(BaseCommand):
    help = "Run the full EOD pipeline for a trading day."

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default="", help="YYYY-MM-DD (default: today)")

    def handle(self, *args, **options):
        day = date.fromisoformat(options["date"]) if options["date"] else date.today()
        broker = PaperBroker()

        def _price_of(instrument):
            quote = get_latest_price(instrument.symbol)
            return quote["ltp"] if quote else None

        expired = expire_day_orders(as_of=day)
        self.stdout.write(f"Expired {expired} DAY orders.")
        for account in Account.objects.filter(is_active=True):
            sqoff = square_off_intraday(account=account, price_of=_price_of, broker=broker)
            expiry = settle_expired_options(account=account, as_of=day, spot_of=_price_of)
            snapshot = settle_day(account=account, day=day, price_of=_price_of)
            self.stdout.write(
                self.style.SUCCESS(
                    f"{account.id}: squared={sqoff['squared']} expired_opts={expiry} "
                    f"equity={snapshot.total_equity}"
                )
            )
