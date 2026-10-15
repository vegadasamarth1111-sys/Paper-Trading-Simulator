"""
Paper Trading Simulator — Risk engine (Phase 3, plan section 6)
=================================================================
The risk engine sits between strategy and broker: PaperBroker calls
check() before any margin is blocked, and a BLOCKED verdict rejects the
order. No order reaches matching without passing every enabled rule.

Design decisions (documented, not accidental):
- Evaluation order is cheapest-first, kill switch first. The FIRST breach
  wins — one rejection, one rule, one RiskEvent row. Callers never get an
  ambiguous multi-rule failure.
- Exits are never trapped: rules that limit NEW risk (window, mandatory
  stop, risk-per-trade, position size, open count, duplicate, rate, daily
  count) exempt orders that only reduce exposure. Kill/drawdown/stale
  still block everything except the engine's own square-off flows, which
  pass skip_risk=True (kill switch and EOD must be able to close into a
  halted account — that bypass is the documented exception).
- Limits come from RiskConfig (strategy row beats account row beats code
  DEFAULTS). Disabled rows (is_enabled=False) skip the rule entirely.
- Every BLOCKED verdict writes a RiskEvent with rule, limit, actual, and
  message (plan mandate). Approvals go to the structured log only — one
  row per order instead of eleven — with the full context in metadata.
- Timestamps are UTC; window logic converts to IST exactly once.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from core.enums import OrderSide, OrderStatus, OrderType, RiskAction, RiskRuleType
from core.models.accounts import Account
from core.models.instruments import Instrument
from core.models.orders import Order
from core.models.strategies import StrategyRun

from .defaults import CLOSE_CUTOFF_IST, DEFAULTS

logger = logging.getLogger("core")
IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class CheckRequest:
    """Everything the engine needs to judge one order."""

    account: Account
    instrument: Instrument
    side: str
    order_type: str
    quantity: int
    product_type: str
    ref_price: Decimal
    trigger_price: Decimal | None = None
    stop_loss_price: Decimal | None = None
    signal_id: str | None = None
    strategy_run: StrategyRun | None = None
    client_order_id: str = ""
    exclude_order_id: UUID | str | None = None
    now: datetime | None = None


@dataclass(frozen=True)
class Decision:
    """One verdict. approved=False always names the rule that fired."""

    approved: bool
    rule: str | None = None
    message: str = ""


def _now_utc(given: datetime | None) -> datetime:
    now = given or datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _limit(
    account: Account, strategy_run: StrategyRun | None, rule: str
) -> tuple[Decimal, Decimal | None, bool]:
    """
    Resolve (value, percentage, enabled) for a rule.

    Strategy row beats account-wide row beats code defaults. A present
    but disabled row skips the rule even when defaults exist.
    """
    from core.models.risk import RiskConfig

    strategy = strategy_run.strategy if strategy_run is not None else None
    row = RiskConfig.objects.filter(account=account, strategy=strategy, rule_type=rule).first()
    if row is None and strategy is not None:
        row = RiskConfig.objects.filter(
            account=account, strategy__isnull=True, rule_type=rule
        ).first()
    if row is not None:
        return row.limit_value, row.limit_percentage, row.is_enabled
    default = DEFAULTS[rule]
    return default.value, default.percentage, True


def _equity(account: Account) -> Decimal:
    return account.current_balance + account.unrealized_pnl


def _is_entry(account: Account, instrument: Instrument, side: str, quantity: int) -> bool:
    """True when the order adds exposure (opens or increases)."""
    from core.models.positions import Position

    row = Position.objects.filter(account=account, instrument=instrument, is_open=True).first()
    held = row.quantity if row else 0
    if held == 0:
        return True
    if side == OrderSide.BUY:
        return held >= 0  # adds to long, or flips a short (= new risk)
    return held <= 0


def _record(
    request: CheckRequest,
    *,
    rule: str,
    action: str,
    message: str,
    limit: Decimal | None = None,
    actual: Decimal | None = None,
) -> None:
    from core.models.risk import RiskEvent

    RiskEvent.objects.create(
        account=request.account,
        order=None,
        rule_type=rule,
        action=action,
        rule_limit=limit,
        actual_value=actual,
        message=message,
        metadata={
            "client_order_id": request.client_order_id,
            "symbol": request.instrument.symbol,
            "side": request.side,
            "quantity": request.quantity,
            "ref_price": str(request.ref_price),
            "signal_id": request.signal_id,
        },
    )


def _blocked(
    request: CheckRequest,
    rule: str,
    message: str,
    limit: Decimal | None = None,
    actual: Decimal | None = None,
) -> Decision:
    _record(
        request, rule=rule, action=RiskAction.BLOCKED, message=message, limit=limit, actual=actual
    )
    logger.warning("Risk BLOCK %s %s: %s", request.client_order_id, rule, message)
    return Decision(approved=False, rule=rule, message=message)


class RiskEngine:
    """Stateless evaluator. All state lives in the database, never here."""

    @staticmethod
    def check(request: CheckRequest) -> Decision:
        """Evaluate every enabled rule in fixed order. First breach wins."""
        now = _now_utc(request.now)
        equity = _equity(request.account)
        entry = _is_entry(request.account, request.instrument, request.side, request.quantity)

        # 1. Kill switch — nothing trades on a halted account.
        # This is the ONLY rule that also blocks exits: a halted account
        # closes solely through kill_switch()/EOD, which pass skip_risk=True.
        if request.account.kill_switch_active:
            return _blocked(request, RiskRuleType.KILL_SWITCH, "kill switch active on account")

        # Exits reduce risk and must never be trapped: every remaining rule
        # judges new exposure only.
        if not entry:
            logger.info(
                "Risk APPROVE (exit) %s %s %sx%s",
                request.client_order_id,
                request.instrument.symbol,
                request.side,
                request.quantity,
            )
            return Decision(approved=True)

        # 2. Drawdown halt — peak-relative, covers intraday and overnight.
        _, dd_pct, dd_on = _limit(request.account, request.strategy_run, RiskRuleType.MAX_DRAWDOWN)
        if dd_on and dd_pct is not None:
            drawdown = request.account.drawdown  # property: % from peak
            if drawdown >= dd_pct:
                return _blocked(
                    request,
                    RiskRuleType.MAX_DRAWDOWN,
                    f"drawdown {drawdown}% >= halt limit {dd_pct}%",
                    limit=dd_pct,
                    actual=drawdown,
                )

        # 3. Trading window — entries only inside the window; exits always pass.
        win_value, _, win_on = _limit(
            request.account, request.strategy_run, RiskRuleType.TRADING_WINDOW
        )
        if entry and win_on:
            ist = now.astimezone(IST)
            open_buffer = int(win_value)
            day_open = ist.replace(hour=9, minute=15, second=0, microsecond=0)
            cutoff_h, cutoff_m = (int(x) for x in CLOSE_CUTOFF_IST.split(":"))
            cutoff = ist.replace(hour=cutoff_h, minute=cutoff_m, second=0, microsecond=0)
            if ist < day_open + timedelta(minutes=open_buffer):
                return _blocked(
                    request,
                    RiskRuleType.TRADING_WINDOW,
                    f"entries blocked in the first {open_buffer} min after open",
                )
            if ist >= cutoff:
                return _blocked(
                    request,
                    RiskRuleType.TRADING_WINDOW,
                    f"no new entries at/after {CLOSE_CUTOFF_IST} IST",
                )

        # 4. Stale data — never trade a symbol the feed has abandoned.
        _, _, stale_on = _limit(request.account, request.strategy_run, RiskRuleType.STALE_DATA)
        if stale_on:
            stale_value, _, _ = _limit(
                request.account, request.strategy_run, RiskRuleType.STALE_DATA
            )
            age = _tick_age_seconds(request.instrument.symbol, now)
            if age is None:
                return _blocked(
                    request,
                    RiskRuleType.STALE_DATA,
                    f"no tick ever seen for {request.instrument.symbol}",
                )
            if age > float(stale_value):
                return _blocked(
                    request,
                    RiskRuleType.STALE_DATA,
                    f"latest tick for {request.instrument.symbol} is {age:.0f}s old "
                    f"(limit {stale_value}s)",
                    limit=stale_value,
                    actual=Decimal(str(int(age))),
                )

        # 5. Duplicate signal — the same signal never executes twice.
        _, _, dup_on = _limit(request.account, request.strategy_run, RiskRuleType.DUPLICATE_ORDER)
        if dup_on and request.signal_id and _signal_seen(request):
            return _blocked(
                request,
                RiskRuleType.DUPLICATE_ORDER,
                f"signal {request.signal_id} already executed",
            )

        # 6. Order rate — per-account rolling 60s cap (strategy loops included).
        rate_value, _, rate_on = _limit(
            request.account, request.strategy_run, RiskRuleType.ORDER_RATE_LIMIT
        )
        if rate_on:
            recent = _orders_since(request.account, now - timedelta(seconds=60))
            if recent >= int(rate_value):
                return _blocked(
                    request,
                    RiskRuleType.ORDER_RATE_LIMIT,
                    f"{recent} orders in the last 60s (limit {int(rate_value)})",
                    limit=rate_value,
                    actual=Decimal(recent),
                )

        # 7. Trades per day — runaway-loop guard on placed orders.
        day_value, _, day_on = _limit(
            request.account, request.strategy_run, RiskRuleType.MAX_TRADES_PER_DAY
        )
        if day_on:
            placed = _orders_on_day(request.account, now.date())
            if placed >= int(day_value):
                return _blocked(
                    request,
                    RiskRuleType.MAX_TRADES_PER_DAY,
                    f"{placed} orders placed today (limit {int(day_value)})",
                    limit=day_value,
                    actual=Decimal(placed),
                )

        # 8. Daily loss — block new risk after losing the day's allowance.
        _, dl_pct, dl_on = _limit(
            request.account, request.strategy_run, RiskRuleType.MAX_DAILY_LOSS
        )
        if entry and dl_on and dl_pct is not None:
            start_equity, day_pnl = _day_start_and_pnl(request.account, now.date())
            allowance = start_equity * dl_pct / Decimal("100")
            if day_pnl <= -allowance:
                return _blocked(
                    request,
                    RiskRuleType.MAX_DAILY_LOSS,
                    f"day PnL {day_pnl:,.2f} breached -{dl_pct}% of "
                    f"{start_equity:,.2f} start equity",
                    limit=-allowance,
                    actual=day_pnl,
                )

        # 9. Mandatory stop — every entry carries one. SL/SLM orders ARE
        #    their own stop mechanism and satisfy this by construction.
        _, _, sl_on = _limit(
            request.account, request.strategy_run, RiskRuleType.MANDATORY_STOP_LOSS
        )
        stop = request.stop_loss_price
        is_stop_order = request.order_type in (OrderType.STOP_LOSS, OrderType.STOP_LOSS_MARKET)
        if entry and sl_on and stop is None and not is_stop_order:
            return _blocked(
                request,
                RiskRuleType.MANDATORY_STOP_LOSS,
                "entry orders must carry a stop-loss (none provided)",
            )

        # 10. Risk per trade — stop distance x qty within % of equity.
        _, rt_pct, rt_on = _limit(
            request.account, request.strategy_run, RiskRuleType.MAX_RISK_PER_TRADE
        )
        effective_stop = stop
        if entry and rt_on and rt_pct is not None:
            if effective_stop is None and is_stop_order:
                effective_stop = request.trigger_price
            if effective_stop is not None:
                distance = abs(request.ref_price - effective_stop)
                risk_amount = distance * request.quantity
                allowance = equity * rt_pct / Decimal("100")
                if risk_amount > allowance:
                    return _blocked(
                        request,
                        RiskRuleType.MAX_RISK_PER_TRADE,
                        f"trade risk {risk_amount:,.2f} exceeds {rt_pct}% of "
                        f"equity ({allowance:,.2f})",
                        limit=allowance,
                        actual=risk_amount,
                    )

        # 11. Position size — value cap AND equity percentage, breach either.
        ps_value, ps_pct, ps_on = _limit(
            request.account, request.strategy_run, RiskRuleType.MAX_POSITION_SIZE
        )
        if entry and ps_on:
            from core.models.positions import Position

            row = Position.objects.filter(
                account=request.account, instrument=request.instrument, is_open=True
            ).first()
            held_value = abs(row.quantity) * request.ref_price if row else Decimal("0")
            projected = held_value + Decimal(request.quantity) * request.ref_price
            if projected > ps_value:
                return _blocked(
                    request,
                    RiskRuleType.MAX_POSITION_SIZE,
                    f"projected {projected:,.2f} exceeds Rs {ps_value:,.2f} cap",
                    limit=ps_value,
                    actual=projected,
                )
            if ps_pct is not None and equity > 0:
                pct_used = projected / equity * Decimal("100")
                if pct_used > ps_pct:
                    return _blocked(
                        request,
                        RiskRuleType.MAX_POSITION_SIZE,
                        f"projected {pct_used:.2f}% of equity exceeds {ps_pct}% cap",
                        limit=ps_pct,
                        actual=pct_used,
                    )

        # 12. Open positions — cap the count of concurrent exposures.
        op_value, _, op_on = _limit(
            request.account, request.strategy_run, RiskRuleType.MAX_OPEN_POSITIONS
        )
        if entry and op_on:
            from core.models.positions import Position

            open_count = (
                Position.objects.filter(account=request.account, is_open=True)
                .exclude(quantity=0)
                .count()
            )
            already_open = (
                Position.objects.filter(
                    account=request.account, instrument=request.instrument, is_open=True
                )
                .exclude(quantity=0)
                .exists()
            )
            projected_count = open_count + (0 if already_open else 1)
            if projected_count > int(op_value):
                return _blocked(
                    request,
                    RiskRuleType.MAX_OPEN_POSITIONS,
                    f"{projected_count} open positions exceeds limit {int(op_value)}",
                    limit=op_value,
                    actual=Decimal(projected_count),
                )

        logger.info(
            "Risk APPROVE %s %s %sx%s",
            request.client_order_id,
            request.instrument.symbol,
            request.side,
            request.quantity,
        )
        return Decision(approved=True)


# ------------------------------------------------------------------
# Helpers (DB reads the engine needs; kept here, not in models)
# ------------------------------------------------------------------
def _tick_age_seconds(symbol: str, now: datetime) -> float | None:
    """Age of the latest known price, or None when never seen."""
    try:
        from market_data.services import get_latest_price
    except Exception:  # noqa: BLE001 - market_data must never break risk
        return None
    try:
        quote = get_latest_price(symbol.upper())
    except Exception:  # noqa: BLE001 - cache/DB outage reads as stale, not fatal
        return None
    if quote is None:
        return None
    ts = quote["ts"]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (now - ts).total_seconds()


def _signal_seen(request: CheckRequest) -> bool:
    """Has this signal already produced a non-rejected order?"""
    from core.models.orders import OrderEvent

    qs = OrderEvent.objects.filter(
        order__account=request.account,
        metadata__signal_id=request.signal_id,
    ).exclude(to_status=OrderStatus.REJECTED)
    if request.exclude_order_id is not None:
        qs = qs.exclude(order_id=request.exclude_order_id)
    return qs.exists()


def _orders_since(account: Account, since: datetime) -> int:

    return Order.objects.filter(account=account, placed_at__gte=since).count()


def _orders_on_day(account: Account, day) -> int:

    return Order.objects.filter(account=account, placed_at__date=day).count()


def _day_start_and_pnl(account: Account, day) -> tuple[Decimal, Decimal]:
    """(day-start equity, today's realized PnL) via snapshots (eod.py math)."""
    from core.models.positions import PortfolioSnapshot

    previous = (
        PortfolioSnapshot.objects.filter(account=account, snapshot_date__lt=day)
        .order_by("-snapshot_date")
        .first()
    )
    if previous is None:
        start = account.initial_capital
        day_pnl = account.realized_pnl
    else:
        start = previous.total_equity
        day_pnl = account.realized_pnl - previous.cumulative_pnl
    return start, day_pnl
