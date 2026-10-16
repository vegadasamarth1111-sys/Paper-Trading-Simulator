"""
Paper Trading Simulator — End-of-day engine (Phase 2, plan section 5.7)
=========================================================================
Mirrors real broker behavior after close:

1. Expire DAY-validity resting orders (EXPIRED, never silently dropped).
2. Auto square-off intraday (MIS) positions at 15:15-15:20 IST semantics:
   market orders against the closing price. Delivery (CNC/NRML) positions
   carry overnight.
3. Mark-to-market every open position at the latest price.
4. Daily settlement: finalize realized PnL, write the PortfolioSnapshot,
   update peak equity. The snapshot is the equity-curve source of truth.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Sum

from core.enums import OrderStatus, ProductType
from core.models.accounts import Account
from core.models.positions import PortfolioSnapshot

if TYPE_CHECKING:
    from broker.paper import PaperBroker

logger = logging.getLogger("core")
UTC = ZoneInfo("UTC")


def as_of_stamp() -> str:
    """Unique-per-run stamp for generated client order IDs."""
    import uuid as uuid_lib

    return uuid_lib.uuid4().hex[:8].upper()


@transaction.atomic
def expire_day_orders(*, as_of: date) -> int:
    """Expire resting DAY orders. Returns the count expired."""
    from core.models.orders import Order, OrderEvent

    resting = Order.objects.filter(
        validity="DAY",
        status__in=[OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED],
        placed_at__date__lte=as_of,
    )
    count = 0
    accounts = set()
    for order in resting:
        previous = order.status
        order.transition_to(OrderStatus.EXPIRED, reason="day validity elapsed")
        order.save(update_fields=["status", "updated_at"])
        OrderEvent.objects.create(
            order=order,
            from_status=previous,
            to_status=OrderStatus.EXPIRED,
            reason="day validity elapsed",
        )
        accounts.add(order.account_id)
        count += 1
    if count:
        # Terminal orders release their blocks: margin is derived, so
        # recompute per touched account (missing this leaks blocked margin
        # forever — found as a 4.7L drift on a live demo account).
        from broker.margin import refresh_margin
        from core.models.accounts import Account

        for account_id in accounts:
            refresh_margin(Account.objects.get(id=account_id))
        logger.info("Expired %d DAY orders for %s", count, as_of)
    return count


@transaction.atomic
def square_off_intraday(*, account: Account, price_of, broker: PaperBroker) -> dict:
    """
    Auto square-off all open MIS positions (15:15-15:20 semantics).

    Args:
        price_of: callable(instrument) -> Decimal | None (closing price).
        broker: PaperBroker (for immediate market fills).

    Returns {"squared": n, "skipped": [(symbol, reason)]}.
    """
    from core.models.positions import Position

    from .fills import MarketSnapshot

    summary: dict = {"squared": 0, "skipped": []}
    legs = Position.objects.filter(
        account=account, is_open=True, product_type=ProductType.MIS
    ).select_related("instrument")
    for position in legs:
        if position.quantity == 0:
            continue
        price = price_of(position.instrument)
        if price is None or price <= 0:
            summary["skipped"].append((position.instrument.symbol, "no closing price"))
            continue
        side = "SELL" if position.quantity > 0 else "BUY"
        snapshot = MarketSnapshot(bid=price, ask=price, ltp=price)
        order = broker.place_order(  # type: ignore[attr-defined]
            account=account,
            instrument=position.instrument,
            side=side,
            order_type="MKT",
            quantity=abs(position.quantity),
            product_type=ProductType.MIS,
            validity="DAY",
            client_order_id=(
                f"sqoff-{account.id}-{position.instrument.symbol}-{as_of_stamp()}"  # type: ignore[attr-defined]
            ),
            # EOD exits must close into any halt state (same bypass as kill).
            skip_risk=True,
        )
        if order.status == OrderStatus.REJECTED:  # type: ignore[attr-defined]
            summary["skipped"].append(
                (position.instrument.symbol, order.rejection_reason)  # type: ignore[attr-defined]
            )
            continue
        # Market orders fill at placement; if anything is still pending (no
        # depth), force-match against the closing snapshot once more.
        if order.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED):  # type: ignore[attr-defined]
            broker.process_snapshot(position.instrument, snapshot)  # type: ignore[attr-defined]
        summary["squared"] += 1
    logger.info("Intraday square-off: %s", summary)
    return summary


@transaction.atomic
def settle_day(*, account: Account, day: date, price_of) -> PortfolioSnapshot:
    """
    Daily settlement: MTM opens, snapshot equity, update peak.

    Args:
        price_of: callable(instrument) -> Decimal | None.
    Returns the PortfolioSnapshot row (updated in place when re-run).
    """
    from core.models.positions import PortfolioSnapshot, Position
    from core.models.trades import Trade

    opens = list(
        Position.objects.filter(account=account, is_open=True).select_related("instrument")
    )
    total_unrealized = Decimal("0.0000")
    for position in opens:
        price = price_of(position.instrument)
        if price is not None and price > 0:
            position.update_unrealized_pnl(price)
            position.save(update_fields=["last_price", "unrealized_pnl", "updated_at"])
        total_unrealized += position.unrealized_pnl

    account.refresh_from_db()  # type: ignore[attr-defined]
    account.unrealized_pnl = total_unrealized  # type: ignore[attr-defined]
    equity = account.current_balance + total_unrealized  # type: ignore[attr-defined]
    if equity > account.peak_balance:  # type: ignore[attr-defined]
        account.peak_balance = equity  # type: ignore[attr-defined]
    account.save(update_fields=["unrealized_pnl", "peak_balance", "updated_at"])  # type: ignore[attr-defined]

    day_trades = Trade.objects.filter(account=account, executed_at__date=day)
    agg = day_trades.aggregate(charges=Sum("total_charges"))

    # Day PnL = lifetime realized now minus lifetime realized at the previous
    # snapshot. Exact under daily snapshots; first snapshot uses lifetime.
    previous = (
        PortfolioSnapshot.objects.filter(account=account, snapshot_date__lt=day)
        .order_by("-snapshot_date")
        .first()
    )
    previous_cumulative = previous.cumulative_pnl if previous else Decimal("0.0000")
    day_pnl = account.realized_pnl - previous_cumulative  # type: ignore[attr-defined]

    snapshot, _ = PortfolioSnapshot.objects.update_or_create(
        account=account,
        snapshot_date=day,
        defaults={
            "cash_balance": account.current_balance,  # type: ignore[attr-defined]
            "margin_used": account.margin_used,  # type: ignore[attr-defined]
            "total_equity": equity,
            "day_pnl": day_pnl,
            "cumulative_pnl": account.realized_pnl,  # type: ignore[attr-defined]
            "open_positions_count": len([p for p in opens if p.quantity != 0]),
            "total_unrealized_pnl": total_unrealized,
            "peak_equity": account.peak_balance,  # type: ignore[attr-defined]
            "drawdown_pct": account.drawdown,  # type: ignore[attr-defined]
            "trades_count": day_trades.count(),
            # Win/loss attribution needs per-fill vs average-at-close pairing;
            # the reports layer (Phase 5) computes it from OrderEvents. Zero
            # here is explicit, not missing.
            "winning_trades": 0,
            "losing_trades": 0,
            "total_charges_today": agg["charges"] or Decimal("0.0000"),
        },
    )
    logger.info(
        "Settled %s for %s: equity=%s day_pnl=%s",
        day,
        account,
        equity,
        day_pnl,  # type: ignore[attr-defined]
    )
    return snapshot
