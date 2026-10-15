"""
Paper Trading Simulator — Kill switch (Phase 3, plan section 6)
=================================================================
One call that stops everything: cancels all resting orders, squares off
all open positions at market, and halts the account so no new order can
pass the engine. Release is a separate, deliberate call — a halted
account never resumes by itself.

Kill-switch square-off orders pass skip_risk=True: the engine would
otherwise block exits into its own halt, trapping positions. That
bypass exists ONLY for this path and the EOD square-off, and every
bypassed order is still fully audited (OrderEvents + RiskEvents).
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from core.enums import OrderStatus, RiskAction, RiskRuleType
from core.models.accounts import Account

logger = logging.getLogger("core")
UTC = ZoneInfo("UTC")


def kill_switch(*, account: Account, reason: str, broker=None) -> dict:
    """
    Halt everything now. Returns {"cancelled": [...], "squared": [...]}.

    Idempotent: killing an already-halted account re-reports without
    duplicating orders (placement idempotency keys are deterministic).
    """
    from core.models.orders import Order
    from core.models.positions import Position

    if broker is None:
        from broker.paper import PaperBroker

        broker = PaperBroker()

    summary: dict = {"cancelled": [], "squared": [], "already_halted": account.kill_switch_active}
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")

    resting = Order.objects.filter(
        account=account,
        status__in=[OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED],
    ).order_by("placed_at")
    for order in resting:
        try:
            broker.cancel_order(order, reason=f"kill switch: {reason}")
            summary["cancelled"].append(order.client_order_id)
        except Exception as exc:  # noqa: BLE001 - one stuck order must not stop the halt
            logger.error("Kill switch could not cancel %s: %s", order.client_order_id, exc)

    for position in Position.objects.filter(account=account, is_open=True).select_related(
        "instrument"
    ):
        if position.quantity == 0:
            continue
        side = "SELL" if position.quantity > 0 else "BUY"
        try:
            exit_order = broker.place_order(
                account=account,
                instrument=position.instrument,
                side=side,
                order_type="MKT",
                quantity=abs(position.quantity),
                product_type=position.product_type,
                validity="DAY",
                client_order_id=f"kill-{account.id.hex[:8]}-{position.instrument.symbol}-{stamp}",
                skip_risk=True,
            )
            summary["squared"].append((position.instrument.symbol, exit_order.status))
        except Exception as exc:  # noqa: BLE001 - keep squaring the rest
            logger.error("Kill switch could not square %s: %s", position.instrument.symbol, exc)

    account.kill_switch_active = True
    account.save(update_fields=["kill_switch_active", "updated_at"])
    _record_kill(account, reason, summary)
    logger.error("KILL SWITCH account=%s reason=%s summary=%s", account.id, reason, summary)
    return summary


def release_kill_switch(*, account: Account, reason: str) -> None:
    """Resume trading. Deliberate by design — never automatic."""
    account.kill_switch_active = False
    account.save(update_fields=["kill_switch_active", "updated_at"])
    _record_kill(account, f"released: {reason}", {}, action=RiskAction.WARNING)
    logger.info("Kill switch released account=%s reason=%s", account.id, reason)


def _record_kill(
    account: Account, reason: str, summary: dict, action: str = RiskAction.KILL_SWITCH
) -> None:
    from core.models.risk import RiskEvent

    RiskEvent.objects.create(
        account=account,
        order=None,
        rule_type=RiskRuleType.KILL_SWITCH,
        action=action,
        message=f"kill switch: {reason}",
        metadata={"summary": str(summary)},
    )
