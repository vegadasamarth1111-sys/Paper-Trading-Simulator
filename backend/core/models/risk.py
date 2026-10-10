"""
Paper Trading Simulator — Risk Models
=======================================
Risk configuration and risk event logging.
The risk engine sits between strategy and broker — no order reaches
the broker without passing through it.
"""

import uuid

from django.db import models

from core.enums import RiskAction, RiskRuleType
from core.validators import validate_percentage


class RiskConfig(models.Model):
    """
    Risk configuration rules. Configurable per strategy and per account.
    All risk limits are stored here, not hard-coded in the engine.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        "core.Account",
        on_delete=models.CASCADE,
        related_name="risk_configs",
    )
    strategy = models.ForeignKey(
        "core.Strategy",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="risk_configs",
        help_text="Strategy-specific config. Null = account-wide.",
    )
    rule_type = models.CharField(
        max_length=20,
        choices=RiskRuleType.choices,
    )
    is_enabled = models.BooleanField(default=True)

    # Numeric limits — interpretation depends on rule_type
    limit_value = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        help_text="Numeric limit (meaning depends on rule type)",
    )
    limit_percentage = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[validate_percentage],
        help_text="Percentage limit (e.g., max 1% risk per trade)",
    )

    # Descriptive
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "risk_configs"
        constraints = [
            models.UniqueConstraint(
                fields=["account", "strategy", "rule_type"],
                name="uq_risk_config_account_strategy_rule",
            ),
        ]

    def __str__(self):
        scope = self.strategy.name if self.strategy else "Account-wide"
        return f"[{scope}] {self.get_rule_type_display()}: {self.limit_value}"


class RiskEvent(models.Model):
    """
    Log of every risk engine decision.
    Every approval and rejection is recorded with the rule that triggered it.
    """

    id = models.BigAutoField(primary_key=True)
    account = models.ForeignKey(
        "core.Account",
        on_delete=models.PROTECT,
        related_name="risk_events",
    )
    order = models.ForeignKey(
        "core.Order",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="risk_events",
    )
    rule_type = models.CharField(
        max_length=20,
        choices=RiskRuleType.choices,
    )
    action = models.CharField(
        max_length=15,
        choices=RiskAction.choices,
    )
    rule_limit = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="The limit value at the time of evaluation",
    )
    actual_value = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="The actual value that was checked against the limit",
    )
    message = models.TextField(
        help_text="Human-readable explanation of the decision",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional context (order details, position state, etc.)",
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "risk_events"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["account", "timestamp"]),
            models.Index(fields=["rule_type", "action"]),
        ]

    def __str__(self):
        return f"[{self.action}] {self.get_rule_type_display()} — {self.message[:80]}"
