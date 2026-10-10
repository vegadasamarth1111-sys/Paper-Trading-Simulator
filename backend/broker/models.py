"""
Paper Trading Simulator — Cost-table model (Phase 2, plan section 5.4)
========================================================================
Transaction charges live in a versioned database table, NOT in code,
because broker and statutory rates change. The calculator reads the
active row; a new row with a later effective date takes over without a
deploy. Every charge on every fill references the table version used,
so any historical number can be reproduced exactly.
"""

from decimal import Decimal

from django.db import models


class CostTable(models.Model):
    """
    One version of the full charge schedule (Zerodha-style defaults).

    Rates are stored as decimals-as-written (e.g. 0.001 = 0.1%), never
    floats. Only one row is active at a time; history is kept for audit.
    Defaults mirror a discount-broker schedule — VERIFY against your
    broker's published charge sheet with `verify_costs` before relying
    on the numbers (rates change; the plan mandates the 20-trade check).
    """

    id = models.BigAutoField(primary_key=True)
    version = models.CharField(
        max_length=50,
        unique=True,
        help_text="Human version, e.g. 'zerodha-style-2026-01'",
    )
    effective_from = models.DateField(
        db_index=True,
        help_text="First trading day this schedule applies to",
    )
    is_active = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Exactly one row should be active; the calculator picks the latest",
    )

    # Brokerage: flat cap per order per side + percentage with cap.
    # Delivery is free; intraday and F&O pay min(0.03% of turnover, flat).
    brokerage_flat = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("20.0000"))
    brokerage_pct = models.DecimalField(max_digits=8, decimal_places=6, default=Decimal("0.000300"))
    brokerage_delivery_free = models.BooleanField(default=True)

    # STT (fraction of turnover / premium, per side as noted).
    stt_delivery = models.DecimalField(
        max_digits=8,
        decimal_places=6,
        default=Decimal("0.001000"),
        help_text="0.1% on buy AND sell (equity delivery)",
    )
    stt_intraday = models.DecimalField(
        max_digits=8,
        decimal_places=6,
        default=Decimal("0.000250"),
        help_text="0.025% on sell only (equity intraday)",
    )
    stt_futures = models.DecimalField(
        max_digits=8,
        decimal_places=6,
        default=Decimal("0.000200"),
        help_text="0.02% on sell only (equity futures)",
    )
    stt_options = models.DecimalField(
        max_digits=8,
        decimal_places=6,
        default=Decimal("0.001000"),
        help_text="0.1% of premium on sell only (options)",
    )
    stt_exercise = models.DecimalField(
        max_digits=8,
        decimal_places=6,
        default=Decimal("0.001250"),
        help_text="0.125% of intrinsic on exercised (ITM cash-settled) options",
    )

    # Exchange transaction charges (fraction of turnover / premium, both sides
    # unless noted). NSE defaults; BSE differs slightly — a second row can
    # encode that when BSE trading is enabled.
    exchange_equity = models.DecimalField(
        max_digits=10, decimal_places=8, default=Decimal("0.00002970")
    )
    exchange_futures = models.DecimalField(
        max_digits=10, decimal_places=8, default=Decimal("0.00001730")
    )
    exchange_options = models.DecimalField(
        max_digits=10,
        decimal_places=8,
        default=Decimal("0.00035030"),
        help_text="On premium (options)",
    )

    # SEBI turnover fee: Rs 10 per crore = 0.0001% (both sides).
    sebi_rate = models.DecimalField(max_digits=10, decimal_places=8, default=Decimal("0.00000100"))
    # GST: 18% on (brokerage + exchange + SEBI).
    gst_rate = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0.1800"))

    # Stamp duty (buy side only).
    stamp_delivery = models.DecimalField(
        max_digits=8,
        decimal_places=6,
        default=Decimal("0.000150"),
        help_text="0.015% on buy (delivery)",
    )
    stamp_intraday = models.DecimalField(
        max_digits=8,
        decimal_places=6,
        default=Decimal("0.000020"),
        help_text="0.002% on buy (intraday and F&O)",
    )

    # DP charges: flat per scrip per day on delivery sells (CDSL + GST).
    dp_charges = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("15.3400"))

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cost_tables"
        ordering = ["-effective_from"]

    def __str__(self) -> str:
        state = "active" if self.is_active else "archived"
        return f"CostTable({self.version}, from {self.effective_from}) [{state}]"

    @classmethod
    def active(cls) -> CostTable | None:
        """Latest active schedule, or None when the table was never seeded."""
        return cls.objects.filter(is_active=True).order_by("-effective_from").first()
