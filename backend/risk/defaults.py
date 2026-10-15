"""
Paper Trading Simulator — Risk defaults (Phase 3, plan section 6)
====================================================================
Documented starting limits for a Rs 10,00,000 paper account. These are
CODE defaults used when no RiskConfig row exists; the database (seeded
by `seed_risk_defaults`) always wins over these, and strategy-specific
rows win over account-wide rows. Change limits in the DB, not here —
a deploy must never be required to tighten risk.

Rule semantics (limit_value vs limit_percentage per rule):
- MAX_POS_SIZE: value cap (Rs) AND percentage of equity; breach either -> block.
- MAX_OPEN_POS: value = max open positions (percentage unused).
- MAX_RISK_TRADE: percentage of equity per trade from stop distance.
- MAX_DAILY_LOSS: percentage of day-start equity; breached -> block new orders.
- MAX_DRAWDOWN: percentage from peak equity; breached -> halt (block all).
- MAX_TRADES_DAY: value = max placed orders per day.
- MANDATORY_SL: value = 1 enabled / 0 disabled.
- DUPLICATE_ORD: value = 1 enabled / 0 disabled.
- ORD_RATE_LIMIT: value = max orders per rolling 60 seconds per account.
- TRADING_WINDOW: value = minutes after open with no entries AND the
  entries cutoff is window_cutoff_before_close_ist (time); encoded as
  "open_buffer_min,close_from_ist" in limit_value? No — kept simple:
  value = open buffer minutes; percentage field unused; close cutoff is
  CLOSE_CUTOFF_IST below (one constant, documented).
- KILL_SWITCH: not a config row; account.kill_switch_active is the switch.
- STALE_DATA: value = max tick age in seconds (defaults to the market-data
  stale threshold).
"""

from dataclasses import dataclass
from decimal import Decimal

from core.enums import RiskRuleType


@dataclass(frozen=True)
class RuleDefault:
    value: Decimal
    percentage: Decimal | None
    description: str


DEFAULTS: dict[str, RuleDefault] = {
    RiskRuleType.MAX_POSITION_SIZE: RuleDefault(
        value=Decimal("200000"),
        percentage=Decimal("20"),
        description="Max Rs 2,00,000 AND 20% of equity in one symbol",
    ),
    RiskRuleType.MAX_OPEN_POSITIONS: RuleDefault(
        value=Decimal("10"),
        percentage=None,
        description="Max 10 open positions",
    ),
    RiskRuleType.MAX_RISK_PER_TRADE: RuleDefault(
        value=Decimal("0"),
        percentage=Decimal("1"),
        description="Max 1% of equity risked per trade (from stop distance)",
    ),
    RiskRuleType.MAX_DAILY_LOSS: RuleDefault(
        value=Decimal("0"),
        percentage=Decimal("2"),
        description="Block new orders after losing 2% of day-start equity",
    ),
    RiskRuleType.MAX_DRAWDOWN: RuleDefault(
        value=Decimal("0"),
        percentage=Decimal("10"),
        description="Halt everything at 10% drawdown from peak equity",
    ),
    RiskRuleType.MAX_TRADES_PER_DAY: RuleDefault(
        value=Decimal("50"),
        percentage=None,
        description="Max 50 placed orders per day (runaway-loop guard)",
    ),
    RiskRuleType.MANDATORY_STOP_LOSS: RuleDefault(
        value=Decimal("1"),
        percentage=None,
        description="Every entry order must carry a stop (1 = enforced)",
    ),
    RiskRuleType.DUPLICATE_ORDER: RuleDefault(
        value=Decimal("1"),
        percentage=None,
        description="Same signal_id never executes twice (1 = enforced)",
    ),
    RiskRuleType.ORDER_RATE_LIMIT: RuleDefault(
        value=Decimal("10"),
        percentage=None,
        description="Max 10 orders per rolling 60 seconds per account",
    ),
    RiskRuleType.TRADING_WINDOW: RuleDefault(
        value=Decimal("5"),
        percentage=None,
        description="No new entries in the first 5 min after open",
    ),
    RiskRuleType.STALE_DATA: RuleDefault(
        value=Decimal("900"),
        percentage=None,
        description="Block symbols whose latest tick is older than 900s",
    ),
}

# No new entries at/after this IST time (auto square-off starts 15:15).
CLOSE_CUTOFF_IST = "15:15"
