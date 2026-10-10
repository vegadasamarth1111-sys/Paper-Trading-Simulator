"""
Paper Trading Simulator — Audit & Config Models
=================================================
Append-only audit log and system configuration store.
Nothing that affects money is ever updated silently.
"""

from django.conf import settings
from django.db import models

from core.enums import AuditAction


class AuditLog(models.Model):
    """
    Append-only audit trail for all significant system events.
    Structured with correlation IDs so you can trace:
      signal_id → order_id → fill_id
    """

    id = models.BigAutoField(primary_key=True)
    action = models.CharField(
        max_length=20,
        choices=AuditAction.choices,
        db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    account = models.ForeignKey(
        "core.Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )

    # Correlation IDs for tracing
    correlation_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
        help_text="Links related events (e.g., signal → order → fill chain)",
    )

    # What entity was affected
    entity_type = models.CharField(
        max_length=50,
        help_text="Type of entity (order, trade, position, account, etc.)",
    )
    entity_id = models.CharField(
        max_length=100,
        help_text="ID of the affected entity",
    )

    # Details
    description = models.TextField(
        help_text="Human-readable description of what happened",
    )
    before_state = models.JSONField(
        null=True,
        blank=True,
        help_text="State before the change (for debugging)",
    )
    after_state = models.JSONField(
        null=True,
        blank=True,
        help_text="State after the change",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional context",
    )

    # Client info (for API-triggered actions)
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Client IP for API-triggered actions",
    )
    user_agent = models.CharField(
        max_length=500,
        blank=True,
        default="",
    )

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "audit_log"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["action", "timestamp"]),
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["correlation_id"]),
            models.Index(fields=["user", "timestamp"]),
        ]
        # Audit logs are APPEND-ONLY
        # Application-level enforcement: no update/delete operations

    def __str__(self):
        actor = self.user.username if self.user else "system"
        return f"[{self.timestamp}] {actor}: {self.action} on {self.entity_type}({self.entity_id})"


class SystemConfig(models.Model):
    """
    System-wide configuration stored in the database.
    Used for values that change at runtime (risk limits, cost rates, etc.)
    without requiring a deploy.
    """

    key = models.CharField(
        max_length=100,
        primary_key=True,
        help_text="Configuration key (e.g., 'cost.brokerage_rate', 'risk.max_daily_loss')",
    )
    value = models.TextField(
        help_text="Configuration value (stored as string, parsed by consumer)",
    )
    value_type = models.CharField(
        max_length=20,
        default="string",
        help_text="Type hint: string, int, decimal, bool, json",
    )
    category = models.CharField(
        max_length=50,
        db_index=True,
        help_text="Grouping category (e.g., 'cost', 'risk', 'trading', 'system')",
    )
    description = models.TextField(blank=True, default="")
    is_sensitive = models.BooleanField(
        default=False,
        help_text="If True, value is masked in API responses and logs",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "system_config"
        ordering = ["category", "key"]

    def __str__(self):
        display_value = "***" if self.is_sensitive else self.value[:50]
        return f"{self.key} = {display_value}"
