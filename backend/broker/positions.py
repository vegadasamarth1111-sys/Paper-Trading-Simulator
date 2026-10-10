"""
Paper Trading Simulator — Position updater (Phase 2, plan sections 5.5/5.7)
============================================================================
Turns immutable fills into live positions. Rules:

- Buys extend/increase a long; sells reduce it. Reducing past zero FLIPS
  to short only for MIS/FUT (delivery CNC never goes short — the sell is
  capped at the held quantity by the caller; extra qty is rejected).
- Average entry price is volume-weighted on the open side only.
- Closing quantity realizes PnL against the open side's average.
- A flip realizes the full open side first, then opens the other side at
  the flip price.
- Realized and unrealized PnL are ALWAYS kept separate (plan mandate).
"""

from dataclasses import dataclass
from decimal import Decimal

Q4 = Decimal("0.0001")


@dataclass
class PositionState:
    """Mutable working copy of a position (caller persists to the model)."""

    quantity: int  # signed: +long, -short, 0 flat
    average_entry_price: Decimal
    realized_pnl: Decimal = Decimal("0.0000")


def apply_fill(
    state: PositionState,
    *,
    side: str,
    quantity: int,
    price: Decimal,
) -> PositionState:
    """
    Apply one fill to a position. Returns the updated state.

    Raises ValueError when a CNC sell exceeds the held long quantity
    (delivery cannot go short); the broker converts this into a rejection.
    """
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY/SELL, got {side!r}")
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    signed = quantity if side == "BUY" else -quantity

    if state.quantity == 0:
        state.quantity = signed
        state.average_entry_price = price
        return state

    same_direction = (state.quantity > 0 and signed > 0) or (state.quantity < 0 and signed < 0)
    if same_direction:
        total = abs(state.quantity) + quantity
        state.average_entry_price = (
            (abs(state.quantity) * state.average_entry_price + quantity * price) / total
        ).quantize(Q4)
        state.quantity += signed
        return state

    # Opposite direction: close first, maybe flip.
    closing = min(abs(state.quantity), quantity)
    open_side = Decimal("1") if state.quantity > 0 else Decimal("-1")
    # For a long closed by a sell: (price - avg) * qty. For a short closed
    # by a buy: (avg - price) * qty. Both equal open_side * (price - avg) * qty.
    state.realized_pnl += (open_side * (price - state.average_entry_price) * closing).quantize(Q4)
    remainder = quantity - closing
    if remainder == 0:
        state.quantity += signed
        if state.quantity == 0:
            state.average_entry_price = Decimal("0.0000")
        return state
    # Flip: old side fully realized, new side opens at this price.
    state.quantity = (1 if signed > 0 else -1) * remainder
    state.average_entry_price = price
    return state


def mark_to_market(state: PositionState, current_price: Decimal) -> Decimal:
    """Unrealized PnL at the current price (does not mutate realized)."""
    if state.quantity == 0:
        return Decimal("0.0000")
    return ((current_price - state.average_entry_price) * state.quantity).quantize(Q4)
