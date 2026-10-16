"""
Paper Trading Simulator — Strategy interface (Phase 4, plan section 7)
=========================================================================
Strategies output SIGNALS, never orders. A signal names direction, entry,
stop, target, and confidence; the runner (sizing -> risk -> broker) turns
it into an order or records exactly why it did not.

Lifecycle: on_start -> (on_tick)* -> (on_candle)* -> on_order_update* ->
on_stop. Only CLOSED candles reach on_candle (look-ahead prevention is
structural, not a convention). Strategies are pure decision logic: no DB,
no clock, no broker inside strategy code — everything arrives via the
context. That is what makes replay deterministic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

ENGINE_VERSION = "4.0.0"


@dataclass(frozen=True)
class CandleView:
    """What a strategy sees of one closed candle (Decimal prices, UTC open)."""

    symbol: str
    timeframe: str
    open_ts: datetime  # UTC interval open
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int


@dataclass(frozen=True)
class TickView:
    """What a strategy sees of one tick (replay/live only, never backtest)."""

    symbol: str
    exchange_ts: datetime  # UTC
    ltp: Decimal
    volume_traded: int


@dataclass
class EmittedSignal:
    """One strategy decision, before sizing and risk."""

    symbol: str
    direction: str  # "BUY" (long) or "SELL" (short/cover signaled explicitly)
    entry_price: Decimal
    stop_loss: Decimal | None = None
    target: Decimal | None = None
    confidence: Decimal = Decimal("0.5000")
    model_version: str = ""
    metadata: dict = field(default_factory=dict)

    def validate(self) -> None:
        """Structural checks. Violations are strategy bugs — raise loudly."""
        if self.direction not in ("BUY", "SELL"):
            raise ValueError(f"direction must be BUY/SELL, got {self.direction!r}")
        if self.entry_price <= 0:
            raise ValueError("entry_price must be positive")
        if not (Decimal("0") <= self.confidence <= Decimal("1")):
            raise ValueError(f"confidence {self.confidence} outside [0, 1]")
        if self.stop_loss is not None and self.stop_loss <= 0:
            raise ValueError("stop_loss must be positive when given")
        if self.target is not None and self.target <= 0:
            raise ValueError("target must be positive when given")
        if self.stop_loss is not None:
            if self.direction == "BUY" and self.stop_loss >= self.entry_price:
                raise ValueError("BUY stop must sit below entry")
            if self.direction == "SELL" and self.stop_loss <= self.entry_price:
                raise ValueError("SELL stop must sit above entry")


class BaseStrategy(ABC):
    """
    Strategy contract. Subclasses implement decision logic only.

    Lifecycle: on_start -> (on_tick)* -> (on_candle)* -> on_order_update* ->
    on_stop. Callbacks receive history ending at the current bar
    (inclusive) and return zero or more signals. State across bars lives in
    instance attributes the subclass owns. Class attribute
    `default_params` documents every knob with its type.
    """

    name: str = "base"
    default_params: dict = {}
    min_confidence: Decimal = Decimal("0.3000")

    def __init__(self, params: dict | None = None) -> None:
        merged = dict(self.default_params)
        merged.update(params or {})
        unknown = set(merged) - set(self.default_params)
        if unknown:
            raise ValueError(f"unknown params for {self.name}: {sorted(unknown)}")
        self.params = merged
        self._started = False

    @property
    def version(self) -> str:
        """Reproducibility stamp recorded on every StrategyRun."""
        return f"{type(self).__module__}.{type(self).__name__}@{ENGINE_VERSION}"

    def on_start(self, symbols: list[str]) -> None:
        """Called once before the first bar. Validate universe here."""
        self._started = True

    @abstractmethod
    def on_candle(self, candle: CandleView, history: list[CandleView]) -> list[EmittedSignal]:
        """React to one newly CLOSED candle. History is oldest-first, inclusive."""
        ...

    def on_tick(self, tick: TickView) -> list[EmittedSignal]:
        """Optional tick-level logic (replay/live). Default: candles only."""
        _ = tick
        return []

    def on_order_update(self, order: object, event: str) -> None:
        """Fills, rejections, cancels involving this strategy's orders."""
        _ = (order, event)

    def on_stop(self) -> None:
        """Called once at run end. Flush state, log summaries."""
        self._started = False
