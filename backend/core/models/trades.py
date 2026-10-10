"""
Paper Trading Simulator — Trade (Fill) Models
===============================================
Trades are immutable records of order fills.
Corrections are new adjusting entries, never edits to existing trades.
"""

import uuid
from decimal import Decimal

from django.db import models

from core.enums import OrderSide
from core.validators import validate_non_negative_decimal, validate_positive_decimal


class Trade(models.Model):
    """
    An executed fill (trade).

    Trades are IMMUTABLE — once created, they are never updated or deleted.
    If a correction is needed, a new adjusting Trade entry is created.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trade_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="System-generated unique trade ID",
    )
    order = models.ForeignKey(
        "core.Order",
        on_delete=models.PROTECT,
        related_name="trades",
    )
    account = models.ForeignKey(
        "core.Account",
        on_delete=models.PROTECT,
        related_name="trades",
    )
    instrument = models.ForeignKey(
        "core.Instrument",
        on_delete=models.PROTECT,
        related_name="trades",
    )

    # Fill details
    side = models.CharField(max_length=4, choices=OrderSide.choices)
    quantity = models.PositiveIntegerField(help_text="Filled quantity in this trade")
    price = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        validators=[validate_positive_decimal],
        help_text="Execution price",
    )
    value = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        help_text="Trade value (price × quantity)",
    )

    # Itemized transaction costs — not hard-coded, from CostConfig
    brokerage = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[validate_non_negative_decimal],
    )
    stt = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[validate_non_negative_decimal],
        help_text="Securities Transaction Tax",
    )
    exchange_charges = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[validate_non_negative_decimal],
    )
    sebi_charges = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[validate_non_negative_decimal],
    )
    gst = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[validate_non_negative_decimal],
    )
    stamp_duty = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[validate_non_negative_decimal],
    )
    dp_charges = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[validate_non_negative_decimal],
        help_text="Depository participant charges (delivery sells)",
    )
    total_charges = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[validate_non_negative_decimal],
        help_text="Sum of all charges",
    )

    # Slippage tracking
    expected_price = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Expected fill price (for slippage measurement)",
    )
    slippage = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Difference between expected and actual fill price",
    )

    # Is this a correction entry?
    is_adjustment = models.BooleanField(
        default=False,
        help_text="True if this trade is a correction/adjustment entry",
    )
    adjustment_reference = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="adjustments",
        help_text="Reference to the original trade being adjusted",
    )

    executed_at = models.DateTimeField(db_index=True, help_text="Fill timestamp")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "trades"
        ordering = ["-executed_at"]
        indexes = [
            models.Index(fields=["account", "executed_at"]),
            models.Index(fields=["order", "executed_at"]),
            models.Index(fields=["instrument", "executed_at"]),
        ]

    def __str__(self):
        return (
            f"Trade({self.trade_id}) "
            f"{self.side} {self.quantity}x {self.instrument.symbol} "
            f"@ ₹{self.price}"
        )

    @property
    def net_value(self) -> Decimal:
        """Trade value minus all charges."""
        return self.value - self.total_charges

    def save(self, *args, **kwargs):
        """Compute value and total_charges before saving."""
        self.value = (self.price * self.quantity).quantize(Decimal("0.0001"))
        self.total_charges = (
            self.brokerage
            + self.stt
            + self.exchange_charges
            + self.sebi_charges
            + self.gst
            + self.stamp_duty
            + self.dp_charges
        )
        if self.expected_price is not None:
            self.slippage = (self.price - self.expected_price).quantize(Decimal("0.0001"))
        super().save(*args, **kwargs)
