"""
Paper Trading Simulator — Risk-limit seeding (Phase 3)
=========================================================
Writes the documented DEFAULTS as account-wide RiskConfig rows.
Re-running never duplicates (get_or_create on the unique triple) and
never overwrites a hand-tuned limit — existing rows are left alone so a
tighter desk limit survives re-seeds.
"""

from core.enums import RiskRuleType
from core.models.accounts import Account
from core.models.risk import RiskConfig
from risk.defaults import DEFAULTS


def seed_account_defaults(account_id: str) -> dict:
    """Seed one account. Returns {"created": [...], "existing": [...] }."""
    account = Account.objects.get(id=account_id)
    created: list[str] = []
    existing: list[str] = []
    for rule in RiskRuleType:
        if rule == RiskRuleType.KILL_SWITCH:
            continue  # the switch is a flag, not a limit row
        default = DEFAULTS.get(rule)
        if default is None:  # pragma: no cover - enum/default drift guard
            raise RuntimeError(f"No documented default for risk rule {rule}")
        _, was_created = RiskConfig.objects.get_or_create(
            account=account,
            strategy=None,
            rule_type=rule,
            defaults={
                "limit_value": default.value,
                "limit_percentage": default.percentage,
                "description": default.description,
                "is_enabled": True,
            },
        )
        (created if was_created else existing).append(str(rule))
    return {"created": created, "existing": existing}
