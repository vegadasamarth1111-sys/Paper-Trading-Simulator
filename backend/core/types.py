"""
Paper Trading Simulator — Custom Types
========================================
Decimal-safe money handling and type aliases.
Rule: NEVER use float for prices, quantities, or balances.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pydantic import BaseModel, field_validator


class Money:
    """
    Decimal wrapper for monetary values.
    Enforces precision and prevents float contamination.
    """

    PRECISION = Decimal("0.0001")  # DECIMAL(18,4) in MySQL
    ZERO = Decimal("0.0000")

    @staticmethod
    def from_value(value) -> Decimal:
        """
        Convert a value to a Decimal with correct precision.
        Raises TypeError if a float is passed (floats corrupt money values).
        """
        if isinstance(value, float):
            raise TypeError(
                f"Float values are forbidden for money calculations. "
                f"Use Decimal or string: Decimal('{value}') instead of {value}"
            )
        if isinstance(value, Decimal):
            return value.quantize(Money.PRECISION, rounding=ROUND_HALF_UP)
        if isinstance(value, (int, str)):
            try:
                return Decimal(str(value)).quantize(Money.PRECISION, rounding=ROUND_HALF_UP)
            except InvalidOperation as exc:
                raise ValueError(f"Cannot convert '{value}' to a monetary value") from exc
        raise TypeError(f"Unsupported type for money: {type(value)}")

    @staticmethod
    def validate_positive(value: Decimal, field_name: str = "value") -> Decimal:
        """Ensure a monetary value is positive."""
        decimal_value = Money.from_value(value)
        if decimal_value <= Money.ZERO:
            raise ValueError(f"{field_name} must be positive, got {decimal_value}")
        return decimal_value

    @staticmethod
    def validate_non_negative(value: Decimal, field_name: str = "value") -> Decimal:
        """Ensure a monetary value is zero or positive."""
        decimal_value = Money.from_value(value)
        if decimal_value < Money.ZERO:
            raise ValueError(f"{field_name} must be non-negative, got {decimal_value}")
        return decimal_value


class TickData(BaseModel):
    """
    Internal tick schema — normalized from any provider's raw format.
    Pydantic model for runtime validation.
    """

    symbol_id: int
    exchange_ts: float  # Unix timestamp from exchange
    received_ts: float  # Unix timestamp when we received it
    ltp: Decimal  # Last traded price
    last_qty: int
    volume_traded: int
    oi: int = 0  # Open interest
    best_bid: Decimal | None = None
    best_bid_qty: int | None = None
    best_ask: Decimal | None = None
    best_ask_qty: int | None = None
    sequence_no: int | None = None

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("ltp", "best_bid", "best_ask", mode="before")
    @classmethod
    def validate_decimal_prices(cls, v):
        """Ensure prices are Decimal, never float."""
        if v is None:
            return v
        if isinstance(v, float):
            raise ValueError(f"Float price detected: {v}. Use Decimal or string.")
        return Decimal(str(v))


class CandleData(BaseModel):
    """Internal candle representation."""

    symbol_id: int
    timeframe: str
    open_ts: float  # Unix timestamp of candle open
    close_ts: float  # Unix timestamp of candle close
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    oi: int = 0
    tick_count: int = 0
    is_closed: bool = False

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("open", "high", "low", "close", mode="before")
    @classmethod
    def validate_decimal_prices(cls, v):
        if isinstance(v, float):
            raise ValueError(f"Float price in candle: {v}. Use Decimal or string.")
        return Decimal(str(v))


class SignalData(BaseModel):
    """
    Trading signal output from a strategy.
    Strategies produce signals, NOT orders directly.
    The position-sizing module converts signals to orders.
    """

    strategy_id: str
    symbol_id: int
    direction: str  # BUY or SELL
    entry_price: Decimal | None = None
    stop_loss: Decimal | None = None
    target: Decimal | None = None
    confidence: Decimal = Decimal("0.5")
    model_version: str | None = None
    metadata: dict | None = None

    model_config = {"arbitrary_types_allowed": True}
