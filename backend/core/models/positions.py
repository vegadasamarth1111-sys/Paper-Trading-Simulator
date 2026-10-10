"""
Paper Trading Simulator — Position & Portfolio Models
======================================================
Tracks open positions and daily portfolio snapshots.
Positions are rebuilt and verified from trades nightly.
"""

import uuid
from decimal import Decimal

from django.db import models

from core.enums import PositionSide, ProductType
from core.validators import validate_non_negative_decimal


class Position(models.Model):
    """
    Current open position in an instrument.

    Positions are derived from trades but maintained for fast access.
    A nightly reconciliation job rebuilds positions from fills and
    compares them with stored values — any difference triggers a
    critical alert.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        "core.Account",
        on_delete=models.PROTECT,
        related_name="positions",
    )
    instrument = models.ForeignKey(
        "core.Instrument",
        on_delete=models.PROTECT,
        related_name="positions",
    )
    strategy_run = models.ForeignKey(
        "core.StrategyRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="positions",
    )

    # Position details
    side = models.CharField(
        max_length=5,
        choices=PositionSide.choices,
        default=PositionSide.FLAT,
    )
    product_type = models.CharField(
        max_length=4,
        choices=ProductType.choices,
    )
    quantity = models.IntegerField(
        default=0,
        help_text="Net quantity (positive=long, negative=short, 0=flat)",
    )
    average_entry_price = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Volume-weighted average entry price",
    )
    last_price = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Last traded price of the instrument",
    )

    # PnL tracking — realized and unrealized kept separate
    realized_pnl = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="PnL from closed portions of this position",
    )
    unrealized_pnl = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Mark-to-market PnL on open quantity",
    )
    total_charges = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[validate_non_negative_decimal],
        help_text="Total transaction costs for this position",
    )

    # Margin
    margin_used = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[validate_non_negative_decimal],
    )

    # Metadata
    opened_at = models.DateTimeField(help_text="When the position was first opened")
    closed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the position was fully closed",
    )
    is_open = models.BooleanField(default=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "positions"
        ordering = ["-opened_at"]
        indexes = [
            models.Index(fields=["account", "is_open"]),
            models.Index(fields=["instrument", "is_open"]),
            models.Index(fields=["strategy_run", "is_open"]),
        ]

    def __str__(self):
        direction = "LONG" if self.quantity > 0 else "SHORT" if self.quantity < 0 else "FLAT"
        return (
            f"{direction} {abs(self.quantity)}x {self.instrument.symbol} "
            f"@ ₹{self.average_entry_price} | PnL: ₹{self.unrealized_pnl}"
        )

    def update_unrealized_pnl(self, current_price: Decimal) -> None:
        """Recalculate unrealized PnL from current market price."""
        self.last_price = current_price
        if self.quantity != 0:
            self.unrealized_pnl = (
                (current_price - self.average_entry_price) * self.quantity
            ).quantize(Decimal("0.0001"))
        else:
            self.unrealized_pnl = Decimal("0.0000")


class PortfolioSnapshot(models.Model):
    """
    Daily portfolio snapshot for equity curve and performance reporting.
    Taken at market close each trading day.
    """

    id = models.BigAutoField(primary_key=True)
    account = models.ForeignKey(
        "core.Account",
        on_delete=models.PROTECT,
        related_name="snapshots",
    )
    snapshot_date = models.DateField(db_index=True)

    # Account state at snapshot time
    cash_balance = models.DecimalField(max_digits=18, decimal_places=4)
    margin_used = models.DecimalField(max_digits=18, decimal_places=4)
    total_equity = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        help_text="Cash + unrealized PnL",
    )
    day_pnl = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        help_text="Today's realized PnL",
    )
    cumulative_pnl = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        help_text="Total PnL since inception",
    )

    # Position summary
    open_positions_count = models.PositiveIntegerField(default=0)
    total_unrealized_pnl = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
    )

    # Risk metrics
    peak_equity = models.DecimalField(max_digits=18, decimal_places=4)
    drawdown_pct = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Drawdown from peak as percentage",
    )

    # Trade statistics for the day
    trades_count = models.PositiveIntegerField(default=0)
    winning_trades = models.PositiveIntegerField(default=0)
    losing_trades = models.PositiveIntegerField(default=0)
    total_charges_today = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "portfolio_snapshots"
        ordering = ["-snapshot_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "snapshot_date"],
                name="uq_snapshot_account_date",
            ),
        ]

    def __str__(self):
        equity = f"₹{self.total_equity:,.2f}"
        return f"{self.account.user.username} | {self.snapshot_date} | Equity: {equity}"
