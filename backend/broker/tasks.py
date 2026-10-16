"""
Paper Trading Simulator — Broker Celery tasks (Phase 2)
=========================================================
All tasks are idempotent: re-running them updates rows instead of
duplicating, so a Beat retry never corrupts money state.
"""

import logging
from datetime import date

from celery import shared_task

logger = logging.getLogger("core")


@shared_task(name="broker.settle_day")
def settle_day_task(account_id: str, day: str | None = None) -> dict:
    """Full EOD: expire DAY orders, square off MIS, settle options, snapshot."""
    from datetime import date as date_cls

    from broker.eod import expire_day_orders, settle_day, square_off_intraday
    from broker.options import settle_expired_options
    from broker.paper import PaperBroker
    from core.models.accounts import Account
    from market_data.services import get_latest_price

    account = Account.objects.get(id=account_id)
    trading_day = date_cls.fromisoformat(day) if day else date_cls.today()
    broker = PaperBroker()

    def _price_of(instrument) -> object:
        quote = get_latest_price(instrument.symbol)
        return quote["ltp"] if quote else None

    expired = expire_day_orders(as_of=trading_day)
    sqoff = square_off_intraday(account=account, price_of=_price_of, broker=broker)
    expiry = settle_expired_options(account=account, as_of=trading_day, spot_of=_price_of)
    snapshot = settle_day(account=account, day=trading_day, price_of=_price_of)
    try:
        from alerts.service import notify_eod_summary

        notify_eod_summary(account, snapshot)
    except Exception:  # noqa: BLE001 - alerts never break settlement
        logger.exception("EOD summary alert failed")
    return {
        "expired_orders": expired,
        "squared_off": sqoff["squared"],
        "options_settled": expiry["settled"],
        "equity": str(snapshot.total_equity),
    }


@shared_task(name="broker.reconcile")
def reconcile_task(account_id: str) -> dict:
    """Nightly money reconciliation. Mismatches are critical alerts, not fixes."""
    from broker.ledger import reconcile_account
    from core.models.accounts import Account

    account = Account.objects.get(id=account_id)
    report = reconcile_account(account)
    if not report.ok:
        logger.error(
            "RECONCILIATION MISMATCH account=%s: %s",
            account_id,
            [(m.area, m.key, m.stored, m.rebuilt) for m in report.mismatches],
        )
        try:
            from alerts.service import notify_reconciliation

            notify_reconciliation(account, report.mismatches)
        except Exception:  # noqa: BLE001 - alerts never break the job
            logger.exception("Reconciliation alert failed")
    return {
        "ok": report.ok,
        "mismatches": [
            {"area": m.area, "key": m.key, "stored": m.stored, "rebuilt": m.rebuilt}
            for m in report.mismatches
        ],
    }


@shared_task(name="broker.load_cost_table")
def load_cost_table_task() -> dict:
    """Seed the Zerodha-style cost schedule (idempotent by version)."""
    from datetime import date as date_cls

    from broker.models import CostTable

    row, created = CostTable.objects.get_or_create(
        version="zerodha-style-2026-01",
        defaults={"effective_from": date_cls(2026, 1, 1), "is_active": True},
    )
    if created:
        logger.info("Seeded %s", row)
    return {"version": row.version, "created": created}


def today_in_ist() -> date:
    """Trading-day helper (module-level for testability)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Kolkata")).date()
