"""
Paper Trading Simulator — Model Validators
============================================
Reusable validators for model fields.
All monetary and trading-specific validation logic lives here.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError


def validate_positive_decimal(value):
    """Ensure a Decimal field value is strictly positive."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"Invalid decimal value: {value}") from exc

    if d <= Decimal("0"):
        raise ValidationError(f"Value must be positive, got {d}")


def validate_non_negative_decimal(value):
    """Ensure a Decimal field value is zero or positive."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"Invalid decimal value: {value}") from exc

    if d < Decimal("0"):
        raise ValidationError(f"Value must be non-negative, got {d}")


def validate_lot_size_multiple(value, lot_size):
    """Ensure quantity is a multiple of the instrument's lot size."""
    if lot_size and value % lot_size != 0:
        raise ValidationError(f"Quantity {value} must be a multiple of lot size {lot_size}")


def validate_price_tick_size(price, tick_size):
    """Ensure price is aligned to the instrument's tick size."""
    if tick_size and tick_size > Decimal("0"):
        remainder = price % tick_size
        if remainder != Decimal("0"):
            raise ValidationError(f"Price {price} must be a multiple of tick size {tick_size}")


def validate_percentage(value):
    """Ensure value is between 0 and 100."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"Invalid percentage value: {value}") from exc

    if not (Decimal("0") <= d <= Decimal("100")):
        raise ValidationError(f"Percentage must be between 0 and 100, got {d}")


def validate_no_float(value):
    """
    Reject float values for money fields.
    This is a safety net — the Money class also checks, but this
    catches issues at the Django model/form layer.
    """
    if isinstance(value, float):
        raise ValidationError(
            f"Float values are forbidden for monetary fields. Use Decimal('{value}') instead."
        )
