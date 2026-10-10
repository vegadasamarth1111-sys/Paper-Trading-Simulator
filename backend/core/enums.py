"""
Paper Trading Simulator — Domain Enumerations
===============================================
All enums used across the trading domain. Defined as Django TextChoices
so they integrate with model fields, serializers, and admin.

These enums are the single source of truth for valid states, types,
and categories throughout the system.
"""

from django.db import models


class Exchange(models.TextChoices):
    """Supported stock exchanges."""

    NSE = "NSE", "National Stock Exchange"
    BSE = "BSE", "Bombay Stock Exchange"


class Segment(models.TextChoices):
    """Market segments."""

    EQUITY = "EQ", "Equity"
    FUTURES = "FUT", "Futures"
    OPTIONS = "OPT", "Options"
    INDEX = "IDX", "Index"


class OptionType(models.TextChoices):
    """Option contract types."""

    CALL = "CE", "Call"
    PUT = "PE", "Put"


class OrderSide(models.TextChoices):
    """Buy or sell."""

    BUY = "BUY", "Buy"
    SELL = "SELL", "Sell"


class OrderType(models.TextChoices):
    """Order execution types supported by the paper broker."""

    MARKET = "MKT", "Market"
    LIMIT = "LMT", "Limit"
    STOP_LOSS = "SL", "Stop Loss"
    STOP_LOSS_MARKET = "SLM", "Stop Loss Market"


class OrderStatus(models.TextChoices):
    """
    Order lifecycle states — strict state machine.
    Valid transitions are enforced in the order model, not just documented.

    NEW → VALIDATED → OPEN → PARTIALLY_FILLED → FILLED
    Any state → REJECTED, CANCELLED, EXPIRED (terminal states)
    """

    NEW = "NEW", "New"
    VALIDATED = "VAL", "Validated"
    OPEN = "OPN", "Open"
    PARTIALLY_FILLED = "PFL", "Partially Filled"
    FILLED = "FLD", "Filled"
    REJECTED = "REJ", "Rejected"
    CANCELLED = "CXL", "Cancelled"
    EXPIRED = "EXP", "Expired"

    @classmethod
    def terminal_states(cls):
        """States from which no further transition is allowed."""
        return {cls.FILLED, cls.REJECTED, cls.CANCELLED, cls.EXPIRED}

    @classmethod
    def valid_transitions(cls):
        """
        Map of (from_state) → set of (valid_to_states).
        Any transition not in this map is illegal and must raise an error.
        """
        return {
            cls.NEW: {cls.VALIDATED, cls.REJECTED},
            cls.VALIDATED: {cls.OPEN, cls.REJECTED},
            cls.OPEN: {cls.PARTIALLY_FILLED, cls.FILLED, cls.CANCELLED, cls.EXPIRED},
            cls.PARTIALLY_FILLED: {cls.PARTIALLY_FILLED, cls.FILLED, cls.CANCELLED},
        }


class ProductType(models.TextChoices):
    """Trading product types."""

    CNC = "CNC", "Cash and Carry (Delivery)"
    MIS = "MIS", "Margin Intraday Settlement"
    NRML = "NRML", "Normal (F&O Positional)"


class OrderValidity(models.TextChoices):
    """How long an order remains active."""

    DAY = "DAY", "Day Order"
    IOC = "IOC", "Immediate or Cancel"


class PositionSide(models.TextChoices):
    """Direction of a position."""

    LONG = "LONG", "Long"
    SHORT = "SHORT", "Short"
    FLAT = "FLAT", "Flat (No Position)"


class TradingMode(models.TextChoices):
    """System operating mode — environment guard."""

    PAPER = "paper", "Paper Trading"
    BACKTEST = "backtest", "Backtest"
    REPLAY = "replay", "Replay"
    LIVE = "live", "Live (Disabled in V1)"


class RiskAction(models.TextChoices):
    """Actions taken by the risk engine."""

    APPROVED = "APPROVED", "Approved"
    BLOCKED = "BLOCKED", "Blocked"
    POSITION_REDUCED = "POS_REDUCED", "Position Reduced"
    KILL_SWITCH = "KILL_SWITCH", "Kill Switch Activated"
    WARNING = "WARNING", "Warning Issued"


class RiskRuleType(models.TextChoices):
    """Types of risk rules that can trigger."""

    MAX_POSITION_SIZE = "MAX_POS_SIZE", "Maximum Position Size"
    MAX_OPEN_POSITIONS = "MAX_OPEN_POS", "Maximum Open Positions"
    MAX_RISK_PER_TRADE = "MAX_RISK_TRADE", "Maximum Risk Per Trade"
    MAX_DAILY_LOSS = "MAX_DAILY_LOSS", "Maximum Daily Loss"
    MAX_DRAWDOWN = "MAX_DRAWDOWN", "Maximum Drawdown"
    MAX_TRADES_PER_DAY = "MAX_TRADES_DAY", "Maximum Trades Per Day"
    MANDATORY_STOP_LOSS = "MANDATORY_SL", "Mandatory Stop Loss"
    DUPLICATE_ORDER = "DUPLICATE_ORD", "Duplicate Order Protection"
    ORDER_RATE_LIMIT = "ORD_RATE_LIMIT", "Order Rate Limit"
    TRADING_WINDOW = "TRADING_WINDOW", "Trading Window"
    KILL_SWITCH = "KILL_SWITCH", "Kill Switch"
    STALE_DATA = "STALE_DATA", "Stale Data Guard"


class TickRejectReason(models.TextChoices):
    """Reasons for rejecting a market data tick during validation."""

    NEGATIVE_PRICE = "NEG_PRICE", "Negative or Zero Price"
    CIRCUIT_BREACH = "CIRCUIT_BREACH", "Outside Circuit Limits"
    FUTURE_TIMESTAMP = "FUTURE_TS", "Timestamp in the Future"
    STALE_TIMESTAMP = "STALE_TS", "Timestamp Too Old"
    OUTSIDE_MARKET_HOURS = "OUTSIDE_MKT", "Outside Market Hours"
    VOLUME_DECREASE = "VOL_DECREASE", "Volume Decreased"
    DUPLICATE_TICK = "DUPLICATE", "Duplicate Tick"
    OUT_OF_ORDER = "OUT_OF_ORDER", "Out of Order Sequence"
    INVALID_SPREAD = "INVALID_SPREAD", "Bid Greater Than Ask"
    EXTREME_MOVE = "EXTREME_MOVE", "Extreme Price Movement"


class CandleTimeframe(models.TextChoices):
    """Candle aggregation intervals."""

    M1 = "1m", "1 Minute"
    M5 = "5m", "5 Minutes"
    M15 = "15m", "15 Minutes"
    H1 = "1h", "1 Hour"
    D1 = "1d", "1 Day"


class AuditAction(models.TextChoices):
    """Types of auditable actions."""

    ORDER_PLACED = "ORD_PLACED", "Order Placed"
    ORDER_MODIFIED = "ORD_MODIFIED", "Order Modified"
    ORDER_CANCELLED = "ORD_CANCELLED", "Order Cancelled"
    ORDER_FILLED = "ORD_FILLED", "Order Filled"
    ORDER_REJECTED = "ORD_REJECTED", "Order Rejected"
    POSITION_OPENED = "POS_OPENED", "Position Opened"
    POSITION_CLOSED = "POS_CLOSED", "Position Closed"
    POSITION_SQUARED_OFF = "POS_SQOFF", "Position Squared Off"
    BALANCE_CREDIT = "BAL_CREDIT", "Balance Credit"
    BALANCE_DEBIT = "BAL_DEBIT", "Balance Debit"
    RISK_TRIGGERED = "RISK_TRIGGER", "Risk Rule Triggered"
    KILL_SWITCH = "KILL_SWITCH", "Kill Switch Activated"
    STRATEGY_STARTED = "STRAT_START", "Strategy Started"
    STRATEGY_STOPPED = "STRAT_STOP", "Strategy Stopped"
    CONFIG_CHANGED = "CONFIG_CHANGE", "Configuration Changed"
    RECONCILIATION_MISMATCH = "RECON_MISMATCH", "Reconciliation Mismatch"
    DATA_QUALITY_ISSUE = "DATA_QUALITY", "Data Quality Issue"
