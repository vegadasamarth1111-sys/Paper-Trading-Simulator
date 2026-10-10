"""
Print 20 sample trades with full charge breakdowns.

This is the human half of the plan's exit gate ("costs match broker
calculator on 20 trades"): run it, open your broker's online brokerage
calculator, and tick off each row. Code cannot browse that calculator,
so the gate is a checklist, not an assertion — the table below is the
checklist.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from broker.costs import active_rates, calculate

SAMPLES: list[tuple] = [
    # (side, qty, price, segment, product, label)
    ("BUY", 10, "2500.00", "EQ", "CNC", "delivery buy 10x2500"),
    ("SELL", 10, "2510.00", "EQ", "CNC", "delivery sell 10x2510"),
    ("BUY", 100, "150.00", "EQ", "MIS", "intraday buy 100x150"),
    ("SELL", 100, "151.00", "EQ", "MIS", "intraday sell 100x151"),
    ("BUY", 1, "45000.00", "FUT", "NRML", "future buy 1x45000"),
    ("SELL", 1, "45100.00", "FUT", "NRML", "future sell 1x45100"),
    ("BUY", 50, "120.00", "OPT", "NRML", "option buy 50x120"),
    ("SELL", 50, "125.00", "OPT", "NRML", "option sell 50x125"),
    ("BUY", 1, "100.00", "EQ", "CNC", "tiny delivery buy"),
    ("SELL", 500, "20.50", "EQ", "MIS", "penny intraday sell"),
    ("BUY", 25, "8000.00", "EQ", "CNC", "large delivery buy"),
    ("SELL", 25, "8100.00", "EQ", "CNC", "large delivery sell"),
    ("BUY", 200, "45.75", "EQ", "MIS", "mid intraday buy"),
    ("SELL", 200, "44.00", "EQ", "MIS", "loss intraday sell"),
    ("BUY", 2, "25000.00", "FUT", "MIS", "intraday future buy"),
    ("SELL", 2, "24900.00", "FUT", "MIS", "intraday future sell"),
    ("BUY", 75, "250.00", "OPT", "NRML", "lot-size option buy"),
    ("SELL", 75, "0.05", "OPT", "NRML", "worthless option sell"),
    ("BUY", 1000, "12.30", "EQ", "CNC", "bulk delivery buy"),
    ("SELL", 1000, "12.90", "EQ", "CNC", "bulk delivery sell"),
]


class Command(BaseCommand):
    help = "Print 20 sample trades for broker-calculator cross-check."

    def handle(self, *args, **options):
        rates = active_rates()
        total = Decimal("0.0000")
        self.stdout.write(
            "side qty price seg prod | brokerage stt exchange sebi gst stamp dp | TOTAL"
        )
        for side, qty, price, seg, prod, label in SAMPLES:
            breakdown = calculate(
                side=side,
                quantity=qty,
                price=Decimal(price),
                segment=seg,
                product=prod,
                rates=rates,
            )
            total += breakdown.total
            self.stdout.write(
                f"{label}: b={breakdown.brokerage} stt={breakdown.stt} "
                f"ex={breakdown.exchange_charges} sebi={breakdown.sebi_charges} "
                f"gst={breakdown.gst} stamp={breakdown.stamp_duty} dp={breakdown.dp_charges} "
                f"=> {breakdown.total}"
            )
        self.stdout.write(self.style.SUCCESS(f"20 trades, total charges {total}"))
