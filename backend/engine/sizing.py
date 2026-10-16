"""
Paper Trading Simulator — Position sizer (Phase 4, plan section 7)
=====================================================================
Converts signals to order quantities. One formula, no discretion:

    risk_amount = capital * risk_pct
    qty = floor(risk_amount / stop_distance) rounded DOWN to a lot multiple

No stop distance (or zero distance) means no size — an unsized order is a
rejected order, never a guessed one. Notional is additionally capped at
the strategy's capital slice; the broker's risk engine still has the
final word downstream.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class SizeResult:
    """Sizing verdict for one signal."""

    quantity: int  # in units (lots already expanded)
    skipped: bool = False
    skip_reason: str = ""
    risk_amount: Decimal = Decimal("0.0000")
    notional: Decimal = Decimal("0.0000")


def size_from_signal(
    *,
    capital: Decimal,
    risk_pct: Decimal,
    entry_price: Decimal,
    stop_loss: Decimal | None,
    lot_size: int,
    max_notional: Decimal | None = None,
) -> SizeResult:
    """
    Risk-based size. All inputs Decimal except counts.

    Args:
        capital: strategy capital slice for this run.
        risk_pct: fraction of capital risked (e.g. Decimal("0.01") = 1%).
        entry_price: expected fill price.
        stop_loss: protective stop (None = cannot size -> skip).
        lot_size: instrument lot (1 for equities).
        max_notional: hard notional cap (defaults to full capital).
    """
    if capital <= 0:
        return SizeResult(quantity=0, skipped=True, skip_reason="no capital allocated")
    if entry_price <= 0:
        return SizeResult(quantity=0, skipped=True, skip_reason="bad entry price")
    if lot_size <= 0:
        return SizeResult(quantity=0, skipped=True, skip_reason="bad lot size")
    if stop_loss is None:
        return SizeResult(quantity=0, skipped=True, skip_reason="no stop, no size")
    distance = abs(entry_price - stop_loss)
    if distance <= 0:
        return SizeResult(quantity=0, skipped=True, skip_reason="zero stop distance")

    risk_amount = (capital * risk_pct).quantize(Decimal("0.0001"))
    raw_qty = int(risk_amount // distance)
    lots = raw_qty // lot_size
    if lots < 1:
        return SizeResult(
            quantity=0,
            skipped=True,
            skip_reason=f"risk allows {raw_qty} units < 1 lot of {lot_size}",
            risk_amount=risk_amount,
        )
    quantity = lots * lot_size
    notional = (entry_price * quantity).quantize(Decimal("0.0001"))
    cap = max_notional if max_notional is not None else capital
    if notional > cap:
        capped_lots = int(cap // (entry_price * lot_size))
        if capped_lots < 1:
            return SizeResult(
                quantity=0,
                skipped=True,
                skip_reason="single lot exceeds capital cap",
                risk_amount=risk_amount,
                notional=notional,
            )
        quantity = capped_lots * lot_size
        notional = (entry_price * quantity).quantize(Decimal("0.0001"))
    return SizeResult(quantity=quantity, risk_amount=risk_amount, notional=notional)
