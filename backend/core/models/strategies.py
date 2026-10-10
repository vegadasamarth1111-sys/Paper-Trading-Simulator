"""
Paper Trading Simulator — Strategy Models
===========================================
Strategy definitions, run tracking, and signal logging.
Each strategy runs in isolation with its own capital allocation.
"""

import uuid
from decimal import Decimal

from django.db import models

from core.enums import OrderSide, TradingMode


class Strategy(models.Model):
    """
    A trading strategy definition.
    Strategy code lives in the engine module — this model holds the
    configuration and metadata.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        "core.Account",
        on_delete=models.PROTECT,
        related_name="strategies",
    )
    name = models.CharField(
        max_length=100,
        help_text="Human-readable strategy name",
    )
    class_name = models.CharField(
        max_length=200,
        help_text=(
            "Fully qualified Python class path (e.g., 'strategies.momentum.MomentumStrategy')"
        ),
    )
    description = models.TextField(blank=True, default="")

    # Capital allocation — each strategy gets its own slice
    allocated_capital = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Capital allocated to this strategy",
    )

    # Parameters (stored as JSON for flexibility)
    parameters = models.JSONField(
        default=dict,
        blank=True,
        help_text="Strategy parameters (timeframe, thresholds, etc.)",
    )

    is_active = models.BooleanField(
        default=False,
        help_text="Whether this strategy is currently enabled",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "strategies"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "name"],
                name="uq_strategy_account_name",
            ),
        ]

    def __str__(self):
        status = "Active" if self.is_active else "Inactive"
        return f"{self.name} [{status}] (₹{self.allocated_capital:,.0f})"


class StrategyRun(models.Model):
    """
    A single execution session of a strategy.
    Tracks the exact version, parameters, and data version so any
    result can be reproduced.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    strategy = models.ForeignKey(
        Strategy,
        on_delete=models.PROTECT,
        related_name="runs",
    )
    mode = models.CharField(
        max_length=10,
        choices=TradingMode.choices,
        help_text="Live paper, backtest, or replay",
    )

    # Versioning for reproducibility
    strategy_version = models.CharField(
        max_length=50,
        help_text="Git commit hash or version tag of the strategy code",
    )
    parameters_snapshot = models.JSONField(
        help_text="Exact parameters used in this run (immutable snapshot)",
    )
    data_version = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Identifier for the data set used (for backtests)",
    )
    model_version = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="ML model version used for signal generation",
    )

    # Run state
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    is_running = models.BooleanField(default=True, db_index=True)
    exit_reason = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Why the run ended (manual stop, error, kill switch, etc.)",
    )

    # PnL for this run specifically
    realized_pnl = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
    )
    total_charges = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
    )
    trades_count = models.PositiveIntegerField(default=0)
    winning_trades = models.PositiveIntegerField(default=0)
    losing_trades = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "strategy_runs"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["strategy", "started_at"]),
            models.Index(fields=["is_running"]),
        ]

    def __str__(self):
        status = "Running" if self.is_running else "Ended"
        return f"{self.strategy.name} [{self.mode}] {status} since {self.started_at}"

    @property
    def win_rate(self) -> Decimal:
        """Percentage of winning trades."""
        total = self.winning_trades + self.losing_trades
        if total == 0:
            return Decimal("0")
        return (Decimal(self.winning_trades) / Decimal(total) * 100).quantize(Decimal("0.01"))


class Signal(models.Model):
    """
    Trading signal produced by a strategy.
    Strategies output signals, NOT orders.
    A separate position-sizing module converts signals to orders.
    """

    id = models.BigAutoField(primary_key=True)
    strategy_run = models.ForeignKey(
        StrategyRun,
        on_delete=models.PROTECT,
        related_name="signals",
    )
    instrument = models.ForeignKey(
        "core.Instrument",
        on_delete=models.PROTECT,
        related_name="signals",
    )

    # Signal details
    direction = models.CharField(max_length=4, choices=OrderSide.choices)
    entry_price = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
    )
    stop_loss = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
    )
    target = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
    )
    confidence = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=Decimal("0.5000"),
        help_text="Signal confidence score (0 to 1)",
    )

    # ML model provenance — which model version produced this signal
    model_version = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="ML model version that produced this signal",
    )

    # Was this signal acted upon?
    order = models.ForeignKey(
        "core.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_signal",
        help_text="Order created from this signal (null if not acted upon)",
    )
    was_executed = models.BooleanField(
        default=False,
        help_text="Whether this signal was converted into an order",
    )
    skip_reason = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Why this signal was not executed (risk rule, low confidence, etc.)",
    )

    metadata = models.JSONField(default=dict, blank=True)
    generated_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "signals"
        ordering = ["-generated_at"]
        indexes = [
            models.Index(fields=["strategy_run", "generated_at"]),
            models.Index(fields=["instrument", "generated_at"]),
            models.Index(fields=["was_executed"]),
        ]

    def __str__(self):
        action = "Executed" if self.was_executed else "Skipped"
        return f"{self.direction} {self.instrument.symbol} (conf={self.confidence}) [{action}]"
