"""
Paper Trading Simulator — Order Models
========================================
Order lifecycle with strict state machine transitions.
Every transition is a logged event with timestamp and reason.
Illegal transitions raise an error and are covered by tests.
"""

import uuid
from decimal import Decimal

from django.db import models

from core.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    OrderValidity,
    ProductType,
)
from core.exceptions import InvalidOrderTransitionError
from core.validators import validate_non_negative_decimal, validate_positive_decimal


class Order(models.Model):
    """
    Trading order with strict state machine lifecycle.

    State transitions:
        NEW → VALIDATED → OPEN → PARTIALLY_FILLED → FILLED
        Any non-terminal state → REJECTED, CANCELLED, EXPIRED

    Every transition creates an OrderEvent for the audit trail.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Client order ID for idempotency — prevents duplicate orders from retries
    client_order_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Client-generated unique ID for idempotent order handling",
    )

    account = models.ForeignKey(
        "core.Account",
        on_delete=models.PROTECT,
        related_name="orders",
    )
    instrument = models.ForeignKey(
        "core.Instrument",
        on_delete=models.PROTECT,
        related_name="orders",
    )
    strategy_run = models.ForeignKey(
        "core.StrategyRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        help_text="Which strategy run generated this order",
    )

    # Order specification
    side = models.CharField(max_length=4, choices=OrderSide.choices)
    order_type = models.CharField(max_length=3, choices=OrderType.choices)
    product_type = models.CharField(max_length=4, choices=ProductType.choices)
    validity = models.CharField(
        max_length=3,
        choices=OrderValidity.choices,
        default=OrderValidity.DAY,
    )

    # Quantities — NEVER float
    quantity = models.PositiveIntegerField(help_text="Total order quantity")
    filled_quantity = models.PositiveIntegerField(
        default=0,
        help_text="Quantity filled so far",
    )
    pending_quantity = models.PositiveIntegerField(
        default=0,
        help_text="Quantity still pending",
    )

    # Prices — DECIMAL(18,4), NEVER float
    price = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[validate_positive_decimal],
        help_text="Limit price (null for market orders)",
    )
    trigger_price = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[validate_positive_decimal],
        help_text="Stop-loss trigger price (for SL/SL-M orders)",
    )
    average_fill_price = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Volume-weighted average fill price",
    )

    # Bracket order fields
    is_bracket_order = models.BooleanField(default=False)
    target_price = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Target price for bracket orders",
    )
    stoploss_price = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Stop-loss price for bracket orders",
    )
    parent_order = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="child_orders",
        help_text="Parent order for bracket order legs",
    )

    # Order status — strict state machine
    status = models.CharField(
        max_length=3,
        choices=OrderStatus.choices,
        default=OrderStatus.NEW,
        db_index=True,
    )
    rejection_reason = models.TextField(
        blank=True,
        default="",
        help_text="Reason for rejection (if rejected by broker or risk engine)",
    )

    # Transaction costs (populated after fill)
    total_charges = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[validate_non_negative_decimal],
        help_text="Total transaction costs (brokerage + taxes + fees)",
    )

    # Timestamps
    placed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    filled_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "orders"
        ordering = ["-placed_at"]
        indexes = [
            models.Index(fields=["account", "status"]),
            models.Index(fields=["instrument", "placed_at"]),
            models.Index(fields=["strategy_run", "placed_at"]),
        ]

    def __str__(self):
        return (
            f"Order({self.client_order_id}) "
            f"{self.side} {self.quantity}x {self.instrument.symbol} "
            f"@ {self.price or 'MKT'} [{self.status}]"
        )

    def transition_to(self, new_status: str, reason: str = "") -> None:
        """
        Transition the order to a new state with strict validation.
        Raises InvalidOrderTransitionError for illegal transitions.

        This method does NOT save the model — the caller must save
        within a database transaction.
        """
        current = self.status

        # Cannot transition from terminal states
        if current in OrderStatus.terminal_states():
            raise InvalidOrderTransitionError(
                message=f"Cannot transition from terminal state '{current}' to '{new_status}'",
                details={"order_id": str(self.id), "current": current, "target": new_status},
            )

        # Validate the transition is in the allowed map
        valid_transitions = OrderStatus.valid_transitions()
        allowed_targets = valid_transitions.get(current, set())

        if new_status not in allowed_targets:
            raise InvalidOrderTransitionError(
                message=f"Invalid transition: '{current}' → '{new_status}'. "
                f"Allowed: {sorted(allowed_targets)}",
                details={"order_id": str(self.id), "current": current, "target": new_status},
            )

        self.status = new_status
        if new_status == OrderStatus.REJECTED:
            self.rejection_reason = reason

    @property
    def is_terminal(self) -> bool:
        """Whether this order is in a final state."""
        return self.status in OrderStatus.terminal_states()

    @property
    def is_active(self) -> bool:
        """Whether this order can still receive fills."""
        return self.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)

    def save(self, *args, **kwargs):
        """Override save to maintain pending_quantity consistency."""
        if self.pending_quantity == 0 and self.filled_quantity == 0:
            self.pending_quantity = self.quantity
        super().save(*args, **kwargs)


class OrderEvent(models.Model):
    """
    Append-only log of order state transitions.
    Every transition is recorded with timestamp and reason.
    This is the source of truth for order history.
    """

    id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="events",
    )
    from_status = models.CharField(
        max_length=3,
        choices=OrderStatus.choices,
    )
    to_status = models.CharField(
        max_length=3,
        choices=OrderStatus.choices,
    )
    reason = models.TextField(blank=True, default="")
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional context (e.g., risk rule that triggered rejection)",
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "order_events"
        ordering = ["timestamp"]
        indexes = [
            models.Index(fields=["order", "timestamp"]),
        ]

    def __str__(self):
        return f"Order({self.order.client_order_id}): {self.from_status} → {self.to_status}"
