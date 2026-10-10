"""
Paper Trading Simulator — Options helpers (Phase 2, plan section 5.6)
=======================================================================
- Lot sizes come from the instrument master (never hard-coded); the
  broker rejects non-multiple quantities at placement.
- Strike selection reads the live option-chain snapshot at signal time.
- Expiry: ITM index options cash-settle at intrinsic value (STT on
  exercise applies); OTM contracts expire worthless. Settlement is an
  immutable SELL Trade against the open position, so fills stay the
  single source of truth for PnL and reconciliation.
- Greeks/IV are analysis-only in v1 (computed elsewhere, never used
  for fills).
"""

import logging
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.enums import OrderSide
from core.models.accounts import Account

logger = logging.getLogger("core")


def assert_lot_multiple(*, quantity: int, lot_size: int, symbol: str) -> None:
    """Raise (→ rejection) when quantity is not a lot multiple."""
    if lot_size <= 0:
        raise ValueError(f"invalid lot_size {lot_size} for {symbol}")
    if quantity % lot_size != 0:
        raise ValueError(f"quantity {quantity} is not a multiple of lot size {lot_size} ({symbol})")


def intrinsic_value(*, option_type: str, spot: Decimal, strike: Decimal) -> Decimal:
    """Per-unit intrinsic value (never negative)."""
    if option_type == "CE":
        return max(Decimal("0.0000"), spot - strike)
    if option_type == "PE":
        return max(Decimal("0.0000"), strike - spot)
    raise ValueError(f"unknown option_type {option_type!r}")


@transaction.atomic
def settle_expired_options(*, account: Account, as_of: date, spot_of) -> dict:
    """
    Cash-settle expired long option positions.

    Args:
        account: account whose positions settle.
        as_of: settlement date (positions with expiry_date <= as_of).
        spot_of: callable(underlying_symbol) -> Decimal | None (latest spot).

    Returns {"settled": n, "worthless": n, "credited": Decimal}.
    ITM legs create SELL Trades at intrinsic (with exercise STT); OTM legs
    close worthless with no trade (premium already paid is the full loss,
    captured in realized PnL when the position row closes).
    """
    import uuid as uuid_lib

    from broker.costs import active_rates, calculate
    from broker.positions import PositionState, apply_fill
    from core.models.positions import Position
    from core.models.trades import Trade

    from .ledger import record_fill_entries

    summary: dict = {"settled": 0, "worthless": 0, "credited": Decimal("0.0000")}
    expired = Position.objects.filter(
        account=account,
        is_open=True,
        instrument__segment="OPT",
        instrument__expiry_date__lte=as_of,
    ).select_related("instrument")

    for position in expired:
        instrument = position.instrument
        if position.quantity < 0:
            # Cannot exist in v1 (naked writes blocked); close defensively
            # rather than inventing settlement economics for it.
            logger.error("Short option position %s survived to expiry; closing flat", position.id)
            position.is_open = False
            position.closed_at = timezone.now()
            position.save(update_fields=["is_open", "closed_at", "updated_at"])
            continue
        spot = spot_of(instrument.underlying_symbol or instrument.symbol)
        if spot is None:
            logger.warning("No spot for %s; expiry settlement deferred", instrument.symbol)
            continue
        if instrument.strike_price is None:
            # No strike on the master row: cannot price intrinsic. Defer (do
            # not invent a settlement value) and let the master sync fix it.
            logger.warning("No strike for %s; expiry settlement deferred", instrument.symbol)
            continue
        intrinsic = intrinsic_value(
            option_type=instrument.option_type, spot=spot, strike=instrument.strike_price
        )
        if intrinsic <= 0:
            position.is_open = False
            position.closed_at = timezone.now()
            position.save(update_fields=["is_open", "closed_at", "updated_at"])
            summary["worthless"] += 1
            logger.info("Option %s expired worthless", instrument.symbol)
            continue

        breakdown = calculate(
            side="SELL",
            quantity=position.quantity,
            price=intrinsic,
            segment="OPT",
            product=position.product_type,
            rates=active_rates(),
            is_exercise=True,
        )
        trade = Trade.objects.create(
            trade_id=f"TRD-{uuid_lib.uuid4().hex[:12].upper()}",
            order=None,  # settlement fills are order-less by design (see 0002)
            account=account,
            instrument=instrument,
            side=OrderSide.SELL,
            quantity=position.quantity,
            price=intrinsic,
            value=intrinsic * position.quantity,
            executed_at=timezone.now(),
            **breakdown.as_dict(),
        )
        state = PositionState(
            quantity=position.quantity,
            average_entry_price=position.average_entry_price,
            realized_pnl=position.realized_pnl,
        )
        realized_before = state.realized_pnl
        apply_fill(state, side="SELL", quantity=position.quantity, price=intrinsic)
        position.quantity = 0
        position.average_entry_price = Decimal("0.0000")
        position.realized_pnl = state.realized_pnl
        position.total_charges += breakdown.total
        position.margin_used = Decimal("0.0000")
        position.is_open = False
        position.closed_at = timezone.now()
        position.save()
        account.refresh_from_db()  # type: ignore[attr-defined]
        record_fill_entries(
            account=account,
            trade=trade,
            position_realized=state.realized_pnl - realized_before,
        )
        account.save(
            update_fields=["current_balance", "realized_pnl", "peak_balance", "updated_at"]
        )  # type: ignore[attr-defined]
        summary["settled"] += 1
        summary["credited"] += intrinsic * trade.quantity - breakdown.total
        logger.info("Option %s cash-settled @ %s (intrinsic)", instrument.symbol, intrinsic)
    return summary
