"""
Paper Trading Simulator — Risk Celery tasks (Phase 3)
=======================================================
Idempotent scheduled work: default-limit seeding and the kill switch
over the queue (dashboard button path in Phase 5).
"""

import logging

from celery import shared_task

logger = logging.getLogger("core")


@shared_task(name="risk.seed_defaults")
def seed_defaults_task(account_id: str) -> dict:
    """Seed account-wide risk limits (idempotent by version key)."""
    from risk.seed import seed_account_defaults

    return seed_account_defaults(account_id)


@shared_task(name="risk.kill_switch")
def kill_switch_task(account_id: str, reason: str) -> dict:
    """Halt an account from the queue (dashboard button in Phase 5)."""
    from core.models.accounts import Account
    from risk.kill import kill_switch

    account = Account.objects.get(id=account_id)
    return kill_switch(account=account, reason=reason)


@shared_task(name="risk.release")
def release_task(account_id: str, reason: str) -> dict:
    """Resume a halted account from the queue."""
    from core.models.accounts import Account
    from risk.kill import release_kill_switch

    account = Account.objects.get(id=account_id)
    release_kill_switch(account=account, reason=reason)
    return {"released": True}
