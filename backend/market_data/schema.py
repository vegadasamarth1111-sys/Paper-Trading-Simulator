"""
Paper Trading Simulator — Normalized tick schema (Phase 1)
============================================================
Single internal Tick representation. Every provider (Yahoo, Angel One,
replay files) converts into this schema BEFORE validation/storage, so the
rest of the system never learns provider quirks.

Money is Decimal. Timestamps are UTC. Provider sequence numbers are kept
for duplicate/out-of-order detection.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class NormalizedTick(BaseModel):
    """Validated internal tick. Construct via `from_provider()` helpers."""

    symbol: str = Field(min_length=1, max_length=50)
    exchange: str = Field(min_length=2, max_length=10)
    exchange_ts: datetime
    received_ts: datetime
    ltp: Decimal = Field(gt=0, max_digits=12, decimal_places=4)
    last_qty: int = Field(default=0, ge=0)
    volume_traded: int = Field(default=0, ge=0)
    oi: int | None = Field(default=None, ge=0)
    best_bid: Decimal | None = None
    best_bid_qty: int | None = Field(default=None, ge=0)
    best_ask: Decimal | None = None
    best_ask_qty: int | None = Field(default=None, ge=0)
    sequence_no: int | None = None
    source: str = Field(default="yahoo", max_length=10)

    model_config = {"str_strip_whitespace": True}

    @field_validator("symbol")
    @classmethod
    def _upper_symbol(cls, value: str) -> str:
        return value.upper()

    @field_validator("exchange")
    @classmethod
    def _upper_exchange(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _received_not_before_exchange(self) -> NormalizedTick:
        # received_ts earlier than exchange_ts means a clock problem upstream;
        # validation.py turns this into a reject with details, but the schema
        # itself stays permissive so the raw row can still land in bad_ticks.
        return self

    def to_tick_kwargs(self) -> dict[str, Any]:
        """Kwargs for `market_data.models.Tick` (instrument added by caller)."""
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "exchange_ts": self.exchange_ts,
            "received_ts": self.received_ts,
            "ltp": self.ltp,
            "last_qty": self.last_qty,
            "volume_traded": self.volume_traded,
            "oi": self.oi,
            "best_bid": self.best_bid,
            "best_bid_qty": self.best_bid_qty,
            "best_ask": self.best_ask,
            "best_ask_qty": self.best_ask_qty,
            "sequence_no": self.sequence_no,
            "source": self.source,
        }
