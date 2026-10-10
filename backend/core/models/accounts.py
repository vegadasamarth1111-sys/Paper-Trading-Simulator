"""
Paper Trading Simulator — Account Models
==========================================
Virtual trading accounts with a full double-entry ledger.
Balance can be rebuilt from ledger entries at any time for reconciliation.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models

from core.validators import validate_non_negative_decimal


class Account(models.Model):
    """
    Virtual trading account with a capital balance.

    Each user has one primary account. Capital is tracked as a Decimal
    value using DECIMAL(18,4) in MySQL — NEVER as float.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,  # Never cascade-delete accounts
        related_name="trading_account",
    )
    name = models.CharField(max_length=100, default="Primary")
    initial_capital = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("1000000.0000"),  # 10 lakh
        validators=[validate_non_negative_decimal],
        help_text="Starting capital in INR",
    )
    current_balance = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("1000000.0000"),
        validators=[validate_non_negative_decimal],
        help_text="Current available balance (updated on each trade)",
    )
    margin_used = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[validate_non_negative_decimal],
        help_text="Capital currently blocked as margin",
    )
    realized_pnl = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Total realized profit/loss",
    )
    unrealized_pnl = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Total unrealized (mark-to-market) profit/loss",
    )
    peak_balance = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("1000000.0000"),
        help_text="Highest balance ever reached (for drawdown calculation)",
    )
    is_active = models.BooleanField(default=True)
    kill_switch_active = models.BooleanField(
        default=False,
        help_text="When True, all trading is halted for this account",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} — {self.name} (₹{self.current_balance:,.2f})"

    @property
    def available_margin(self) -> Decimal:
        """Capital available for new positions."""
        return self.current_balance - self.margin_used

    @property
    def total_equity(self) -> Decimal:
        """Net account value including unrealized PnL."""
        return self.current_balance + self.unrealized_pnl

    @property
    def drawdown(self) -> Decimal:
        """Current drawdown from peak balance, as a percentage."""
        if self.peak_balance <= Decimal("0"):
            return Decimal("0")
        return ((self.peak_balance - self.total_equity) / self.peak_balance * 100).quantize(
            Decimal("0.01")
        )


class AccountLedgerEntry(models.Model):
    """
    Append-only ledger for account balance changes.
    Nothing that affects money is ever updated silently.
    Balance can be rebuilt from the sum of all ledger entries.
    """

    class EntryType(models.TextChoices):
        INITIAL_DEPOSIT = "INIT_DEP", "Initial Deposit"
        TRADE_DEBIT = "TRADE_DR", "Trade Debit"
        TRADE_CREDIT = "TRADE_CR", "Trade Credit"
        COST_DEBIT = "COST_DR", "Transaction Cost"
        MARGIN_BLOCK = "MARGIN_BLK", "Margin Blocked"
        MARGIN_RELEASE = "MARGIN_REL", "Margin Released"
        MTM_ADJUSTMENT = "MTM_ADJ", "Mark-to-Market Adjustment"
        DIVIDEND_CREDIT = "DIV_CR", "Dividend Credit"
        ADJUSTMENT = "ADJUST", "Manual Adjustment"

    id = models.BigAutoField(primary_key=True)
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )
    entry_type = models.CharField(max_length=12, choices=EntryType.choices)
    amount = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        help_text="Positive = credit, Negative = debit",
    )
    balance_after = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        help_text="Account balance after this entry",
    )
    reference_type = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Type of entity this entry references (e.g., 'order', 'trade')",
    )
    reference_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="ID of the referenced entity",
    )
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "account_ledger"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["account", "created_at"]),
            models.Index(fields=["reference_type", "reference_id"]),
        ]
        # Ledger entries are append-only — never updated or deleted
        # This is enforced at the application layer, not the database layer

    def __str__(self):
        sign = "+" if self.amount >= 0 else ""
        return f"[{self.entry_type}] {sign}{self.amount} → {self.balance_after}"
