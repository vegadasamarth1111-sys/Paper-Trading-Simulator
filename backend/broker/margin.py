"""
Paper Trading Simulator — Margin calculator (Phase 2, plan section 5.5)
=========================================================================
Simplified SPAN-like upfront margin. This is a stated simplification, not
an exchange SPAN replication: it blocks the obviously-underfunded trade
and sizes F&O realistically, while the docstring says exactly what it is
not. Option buying needs full premium; naked option writing is BLOCKED
in v1 (user decision) with an explicit reason.

Equity MIS uses 20% of trade value (5x). SEBI peak-margin rules mean real
brokers may ask more intraday — the constant is one env var away.
"""

from dataclasses import dataclass
from decimal import Decimal

from core.models.accounts import Account
from core.models.instruments import Instrument


@dataclass(frozen=True)
class MarginRequirement:
    """Upfront margin blocked for one order (excludes estimated costs)."""

    margin: Decimal
    basis: str  # human explanation, stored in OrderEvent metadata


# Simplified schedule (fractions of contract value).
MIS_EQUITY_FRACTION = Decimal("0.20")
FUT_NRML_FRACTION = Decimal("0.12")
FUT_MIS_FRACTION = Decimal("0.06")  # half of NRML, intraday


def required_margin(
    *,
    quantity: int,
    price: Decimal,
    segment: str,
    product: str,
    side: str,
) -> MarginRequirement:
    """
    Upfront margin for an order. Raises for naked option writes (v1 rule).

    Args:
        quantity: order units. price: order/last price. segment: EQ/FUT/OPT.
        product: CNC/MIS/NRML. side: BUY/SELL.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if price <= 0:
        raise ValueError("price must be positive")
    value = price * quantity

    if segment == "EQ":
        if product == "CNC":
            return MarginRequirement(margin=value, basis="EQ CNC: 100% of value")
        return MarginRequirement(
            margin=value * MIS_EQUITY_FRACTION, basis="EQ MIS: 20% of value (5x)"
        )
    if segment == "FUT":
        if product == "NRML":
            return MarginRequirement(
                margin=value * FUT_NRML_FRACTION, basis="FUT NRML: 12% (simplified SPAN)"
            )
        return MarginRequirement(
            margin=value * FUT_MIS_FRACTION, basis="FUT MIS: 6% (half of NRML)"
        )
    if segment == "OPT":
        if side == "BUY":
            return MarginRequirement(margin=value, basis="OPT BUY: full premium")
        raise NakedShortBlocked(
            "Naked option writing is disabled in v1. Buy options or spread with "
            "defined risk instead."
        )
    raise ValueError(f"unknown segment {segment!r}")


class NakedShortBlocked(RuntimeError):
    """Raised when an order would write an uncovered option (v1 guard)."""


def refresh_margin(account: Account) -> Decimal:
    """
    Recompute account.margin_used from open positions + resting orders.

    Margin is DERIVED, never incrementally tracked: every placement, fill,
    and cancel ends by recomputing from the same two queries, so a missed
    release path cannot leak blocked margin forever. Returns the new total.
    """
    from core.enums import OrderStatus
    from core.models.orders import Order
    from core.models.positions import Position

    total = Decimal("0.0000")

    for position in Position.objects.filter(account=account, is_open=True).select_related(
        "instrument"
    ):
        if position.quantity == 0:
            continue
        price = position.average_entry_price or position.last_price
        if price <= 0:
            continue
        try:
            need = required_margin(
                quantity=abs(position.quantity),
                price=price,
                segment=position.instrument.segment,
                product=position.product_type,
                side="BUY" if position.quantity > 0 else "SELL",
            )
        except NakedShortBlocked:
            continue  # cannot exist in v1; a Phase-3 guard reports it instead
        total += need.margin
        if position.margin_used != need.margin:
            position.margin_used = need.margin
            position.save(update_fields=["margin_used", "updated_at"])

    for order in Order.objects.filter(
        account=account,
        status__in=[OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED],
    ).select_related("instrument"):
        pending = order.pending_quantity
        if pending <= 0:
            continue
        ref = order.price or _latest_ref(order.instrument)
        if ref is None or ref <= 0:
            continue
        try:
            need = required_margin(
                quantity=pending,
                price=ref,
                segment=order.instrument.segment,
                product=order.product_type,
                side=order.side,
            )
        except NakedShortBlocked:
            continue
        total += need.margin

    account.margin_used = total  # type: ignore[attr-defined]
    account.save(update_fields=["margin_used", "updated_at"])  # type: ignore[attr-defined]
    return total


def _latest_ref(instrument: Instrument) -> Decimal | None:
    """Latest known price, or None when the feed is silent (stale guard)."""
    try:
        from market_data.services import get_latest_price

        quote = get_latest_price(instrument.symbol)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - broker must not import-fail without market_data
        return None
    return quote["ltp"] if quote else None
