"""
Paper Trading Simulator — Instrument Models
=============================================
Instrument master, corporate actions, and trading calendar.
Lot sizes, tick sizes, and expiry dates come from the instrument master —
NEVER hard-coded anywhere in the system.
"""

from decimal import Decimal

from django.db import models

from core.enums import Exchange, OptionType, Segment


class Instrument(models.Model):
    """
    Financial instrument (stock, future, or option).

    This is a slowly changing dimension — instruments have effective dates
    and are versioned. When lot sizes or tick sizes change, a new row is
    created; the old row's effective_to is set.
    """

    id = models.BigAutoField(primary_key=True)
    symbol = models.CharField(
        max_length=50,
        db_index=True,
        help_text="Trading symbol (e.g., RELIANCE, NIFTY24OCTFUT)",
    )
    exchange = models.CharField(
        max_length=3,
        choices=Exchange.choices,
        db_index=True,
    )
    segment = models.CharField(
        max_length=3,
        choices=Segment.choices,
        db_index=True,
    )
    name = models.CharField(
        max_length=200,
        help_text="Full instrument name",
    )
    # Provider-specific token for data subscription
    exchange_token = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Exchange instrument token (provider-specific)",
    )
    isin = models.CharField(
        max_length=12,
        blank=True,
        default="",
        help_text="ISIN code for equities",
    )

    # Lot and tick sizing — from instrument master, never hard-coded
    lot_size = models.PositiveIntegerField(
        default=1,
        help_text="Minimum tradeable quantity (1 for equities, varies for F&O)",
    )
    tick_size = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=Decimal("0.05"),
        help_text="Minimum price movement",
    )

    # Derivatives-specific fields
    expiry_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Expiry date for F&O contracts",
    )
    strike_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Strike price for options",
    )
    option_type = models.CharField(
        max_length=2,
        choices=OptionType.choices,
        blank=True,
        default="",
        help_text="CE or PE for options",
    )
    underlying_symbol = models.CharField(
        max_length=50,
        blank=True,
        default="",
        db_index=True,
        help_text="Underlying instrument symbol (for derivatives)",
    )

    # Circuit limits
    upper_circuit = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Upper circuit limit",
    )
    lower_circuit = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Lower circuit limit",
    )

    # Slowly changing dimension fields
    effective_from = models.DateField(
        help_text="Date from which this instrument data is effective",
    )
    effective_to = models.DateField(
        null=True,
        blank=True,
        help_text="Date until which this data is effective (null = current)",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this instrument is currently tradeable",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "instruments"
        ordering = ["symbol"]
        indexes = [
            models.Index(fields=["symbol", "exchange", "segment"]),
            models.Index(fields=["underlying_symbol", "expiry_date"]),
            models.Index(fields=["exchange_token"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["symbol", "exchange", "effective_from"],
                name="uq_instrument_symbol_exchange_effective",
            ),
        ]

    def __str__(self):
        parts = [f"{self.symbol} ({self.exchange}:{self.segment})"]
        if self.expiry_date:
            parts.append(f"Exp:{self.expiry_date}")
        if self.strike_price:
            parts.append(f"Strike:{self.strike_price}{self.option_type}")
        return " ".join(parts)

    @property
    def is_derivative(self) -> bool:
        """Whether this instrument is a derivative (future or option)."""
        return self.segment in (Segment.FUTURES, Segment.OPTIONS)

    @property
    def is_option(self) -> bool:
        """Whether this instrument is an option contract."""
        return self.segment == Segment.OPTIONS

    @property
    def is_expired(self) -> bool:
        """Whether this derivative has expired."""
        from django.utils import timezone

        if self.expiry_date is None:
            return False
        return self.expiry_date < timezone.now().date()


class CorporateAction(models.Model):
    """
    Corporate actions (splits, bonuses, dividends).
    Raw data is never overwritten — adjustment factors are applied when needed.
    """

    class ActionType(models.TextChoices):
        SPLIT = "SPLIT", "Stock Split"
        BONUS = "BONUS", "Bonus Issue"
        DIVIDEND = "DIVIDEND", "Dividend"
        RIGHTS = "RIGHTS", "Rights Issue"

    id = models.BigAutoField(primary_key=True)
    instrument = models.ForeignKey(
        Instrument,
        on_delete=models.PROTECT,
        related_name="corporate_actions",
    )
    action_type = models.CharField(max_length=10, choices=ActionType.choices)
    ex_date = models.DateField(
        db_index=True,
        help_text="Ex-date for the corporate action",
    )
    record_date = models.DateField(
        null=True,
        blank=True,
    )
    # For splits: old_ratio:new_ratio (e.g., 1:2 split → ratio=0.5)
    adjustment_factor = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal("1.000000"),
        help_text="Price adjustment factor (multiply old prices by this)",
    )
    # For dividends
    dividend_amount = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Dividend per share in INR",
    )
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "corporate_actions"
        ordering = ["-ex_date"]
        indexes = [
            models.Index(fields=["instrument", "ex_date"]),
        ]

    def __str__(self):
        return f"{self.instrument.symbol} {self.action_type} on {self.ex_date}"


class TradingHoliday(models.Model):
    """
    NSE/BSE trading holidays.
    Loaded from official NSE/BSE announcements — never hard-coded.
    """

    id = models.BigAutoField(primary_key=True)
    date = models.DateField(unique=True, db_index=True)
    exchange = models.CharField(max_length=3, choices=Exchange.choices)
    description = models.CharField(
        max_length=200,
        help_text="Holiday name (e.g., 'Republic Day', 'Muhurat Trading')",
    )
    is_half_day = models.BooleanField(
        default=False,
        help_text="True for special sessions like Muhurat trading",
    )
    close_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Closing time for half-day sessions (IST)",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "trading_holidays"
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(
                fields=["date", "exchange"],
                name="uq_holiday_date_exchange",
            ),
        ]

    def __str__(self):
        suffix = " (half day)" if self.is_half_day else ""
        return f"{self.date} — {self.description}{suffix}"
