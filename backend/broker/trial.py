"""
Paper Trading Simulator — Live-paper trial harness (Phase 8)
===============================================================
Phase 8 is time-gated (6-8 weeks of live data), so this module is the
machinery that makes those weeks COUNT:

- run_trial_day(): nightly evidence collection for one date (ticks,
  fills, PnL, reconcile verdicts, explainability counters, optional
  paper-vs-replay divergence). Writes exactly one TrialDay row.
- evaluate_acceptance(): the plan's acceptance criteria read back as
  PASS/FAIL/PENDING from TrialDay rows + rebuilt ledger state. Overall
  PASS is unreachable before min_days trading days exist — the gate is
  time, and the code refuses to pretend otherwise.

Replay accounts (usernames `trial-replay-*`) are trial evidence, not
traders: they are excluded from paper-side aggregates and performance
stats everywhere in this module.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

logger = logging.getLogger("core")
UTC = ZoneInfo("UTC")
REPLAY_PREFIX = "trial-replay"


@dataclass
class CriterionResult:
    """One acceptance criterion verdict."""

    name: str
    status: str  # PASS | FAIL | PENDING
    detail: str


@dataclass
class AcceptanceReport:
    """Full acceptance verdict (plan section 13, production-ready list)."""

    days: int
    criteria: list[CriterionResult] = field(default_factory=list)

    @property
    def overall(self) -> str:
        if any(c.status == "FAIL" for c in self.criteria):
            return "FAIL"
        if any(c.status == "PENDING" for c in self.criteria):
            return "IN PROGRESS"
        return "PASS"


def _real_accounts():
    """Active accounts minus replay scaffolding."""
    from core.models.accounts import Account

    return Account.objects.filter(is_active=True).exclude(user__username__startswith=REPLAY_PREFIX)


def run_trial_day(
    day: date, *, replay_strategy=None, replay_symbols=(), tolerance_pct="15"
) -> object:
    """
    Collect one day's trial evidence and store the TrialDay row.

    Idempotent per date (re-runs update in place). Never trades: the
    optional divergence replay runs on a dedicated replay account.
    """
    from django.db.models import Sum

    from broker.ledger import reconcile_account
    from core.models import TrialDay
    from core.models.orders import Order
    from core.models.trades import Trade
    from market_data.models import BadTick, Tick
    from market_data.quality import build_daily_report

    start_utc, end_utc = _day_bounds(day)
    ticks_total = Tick.objects.filter(exchange_ts__gte=start_utc, exchange_ts__lt=end_utc).count()
    bad_ticks = BadTick.objects.filter(created_at__gte=start_utc, created_at__lt=end_utc).count()
    quality = build_daily_report(day)

    orders = Order.objects.filter(placed_at__gte=start_utc, placed_at__lt=end_utc)
    fills = Trade.objects.filter(executed_at__gte=start_utc, executed_at__lt=end_utc)
    charges = fills.aggregate(n=Sum("total_charges"))["n"] or Decimal("0.0000")
    day_pnl = _paper_day_pnl(day)

    reconcile_ok = True
    mismatches = 0
    for account in _real_accounts():
        report = reconcile_account(account)
        if not report.ok:
            reconcile_ok = False
            mismatches += len(report.mismatches)
            logger.error("Trial reconcile mismatch %s: %s", day, report.mismatches)

    unexplained = _count_unexplained(start_utc, end_utc)

    divergence = None
    if replay_strategy and replay_symbols:
        divergence = compare_with_replay(
            day, replay_strategy, list(replay_symbols), tolerance_pct=tolerance_pct
        )

    row, _ = TrialDay.objects.update_or_create(
        trial_date=day,
        defaults={
            "ticks_total": ticks_total,
            "bad_ticks": bad_ticks,
            "data_suspect": quality.is_suspect,
            "orders_placed": orders.count(),
            "fills": fills.count(),
            "realized_pnl": day_pnl,
            "total_charges": charges,
            "reconcile_ok": reconcile_ok,
            "reconcile_mismatches": mismatches,
            "unexplained_rejects": unexplained,
            "divergence_pct": divergence,
        },
    )
    logger.info("Trial day %s recorded: %s", day, row)
    return row


def compare_with_replay(
    day: date, strategy_name: str, symbols: list[str], tolerance_pct="15"
) -> Decimal | None:
    """
    Replay the day's candles with the same strategy on a replay account
    and compare realized PnL against live paper (plan: divergence is a
    bug signal). Returns divergence percent or None when n/a.

    Same code path as production replays (EventRunner); the replay
    account is clearly labeled and excluded from paper aggregates.
    """
    from django.contrib.auth import get_user_model

    from core.models.accounts import Account
    from engine.loaders import load_closed_candles

    start_utc, end_utc = _day_bounds(day)
    candles = {s: load_closed_candles(s, "1m", start_utc, end_utc) for s in symbols}
    if not any(candles.values()):
        logger.info("Trial divergence: no candles for %s; n/a", day)
        return None
    username = f"{REPLAY_PREFIX}-{day.isoformat()}"
    user, created = get_user_model().objects.get_or_create(
        username=username, defaults={"email": f"{username}@example.com"}
    )
    if created:
        # Replay accounts must never authenticate: unusable password by design.
        user.set_unusable_password()
        user.save(update_fields=["password"])
    account, _ = Account.objects.get_or_create(user=user, defaults={"name": "Replay"})
    _reset_replay_account(account)

    from engine.management.commands.run_common import build_runner

    runner, _, _ = build_runner(
        mode="backtest",
        account_id=str(account.id),
        strategy_name=strategy_name,
        params={},
        symbols=symbols,
        risk_pct="0.01",
        data_version=f"trial-divergence:{day.isoformat()}",
    )
    result = runner.run_backtest(candles)
    paper_pnl = _paper_day_pnl(day)
    replay_pnl = result.realized_pnl
    if paper_pnl == 0 and replay_pnl == 0:
        return Decimal("0.0000")
    if paper_pnl == 0:
        logger.info("Trial divergence: paper flat, replay %s; n/a", replay_pnl)
        return None
    divergence = abs(paper_pnl - replay_pnl) / abs(paper_pnl) * Decimal("100")
    logger.info(
        "Trial divergence %s: paper=%s replay=%s -> %s%% (tolerance %s%%)",
        day,
        paper_pnl,
        replay_pnl,
        divergence,
        tolerance_pct,
    )
    return divergence.quantize(Decimal("0.0001"))


def evaluate_acceptance(*, min_days=30, tolerance_pct="15") -> AcceptanceReport:
    """
    Read the acceptance criteria off trial evidence.

    min_days=30 trading days ~= 6 weeks. Overall PASS is structurally
    unreachable before then — report IN PROGRESS honestly instead.
    """
    from core.models import TrialDay
    from core.models.trades import Trade

    rows = list(TrialDay.objects.order_by("trial_date"))
    report = AcceptanceReport(days=len(rows))
    tol = Decimal(str(tolerance_pct))

    def _add(name, status, detail):
        report.criteria.append(CriterionResult(name=name, status=status, detail=detail))

    if len(rows) < min_days:
        _add("trial_length", "PENDING", f"{len(rows)}/{min_days} trading days recorded")
    else:
        _add("trial_length", "PASS", f"{len(rows)} trading days (>= {min_days})")

    bad_days = [r for r in rows if not r.reconcile_ok or r.reconcile_mismatches]
    if bad_days:
        _add(
            "zero_reconciliation_mismatches",
            "FAIL",
            f"mismatches on {[str(r.trial_date) for r in bad_days]}",
        )
    elif not rows:
        _add("zero_reconciliation_mismatches", "PENDING", "no trial days yet")
    else:
        _add("zero_reconciliation_mismatches", "PASS", f"{len(rows)} days, 0 mismatches")

    crashes = sum(r.crash_count for r in rows)
    if crashes:
        _add("no_market_hours_crashes", "FAIL", f"{crashes} logged crashes")
    elif not rows:
        _add("no_market_hours_crashes", "PENDING", "no trial days yet")
    else:
        _add("no_market_hours_crashes", "PASS", "0 logged crashes")

    if any(r.restarts > 0 for r in rows):
        _add(
            "restart_recovery_verified",
            "PASS",
            f"{sum(r.restarts for r in rows)} verified restarts logged",
        )
    else:
        _add(
            "restart_recovery_verified",
            "PENDING",
            "no forced restart logged yet (recovery must be demonstrated, not assumed)",
        )

    unexplained = sum(r.unexplained_rejects for r in rows)
    if unexplained:
        _add("all_rejects_explained", "FAIL", f"{unexplained} reason-less rejects")
    elif not rows:
        _add("all_rejects_explained", "PENDING", "no trial days yet")
    else:
        _add("all_rejects_explained", "PASS", "every reject carries its rule")

    diverged = [r for r in rows if r.divergence_pct is not None and r.divergence_pct > tol]
    measured = [r for r in rows if r.divergence_pct is not None]
    if diverged:
        _add(
            "replay_divergence",
            "FAIL",
            f"{len(diverged)} days over {tol}%: {[str(r.trial_date) for r in diverged]}",
        )
    elif not measured:
        _add("replay_divergence", "PENDING", "no measured trading days yet")
    else:
        _add("replay_divergence", "PASS", f"{len(measured)} measured days within {tol}%")

    fills_total = sum(r.fills for r in rows)
    if not fills_total:
        _add("costs_and_slippage", "PENDING", "no fills yet to verify")
    else:
        charges_total = sum((r.total_charges for r in rows), Decimal("0.0000"))
        mkt_without_expected = Trade.objects.filter(
            order__order_type="MKT", expected_price__isnull=True
        ).count()
        if charges_total <= 0:
            _add("costs_and_slippage", "FAIL", "fills exist but zero charges recorded")
        elif mkt_without_expected:
            _add(
                "costs_and_slippage",
                "FAIL",
                f"{mkt_without_expected} market fills lack expected_price (slippage unmeasured)",
            )
        else:
            _add(
                "costs_and_slippage",
                "PASS",
                f"{fills_total} fills, {charges_total} charges, slippage measured",
            )
    return report


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _day_bounds(day: date):
    from datetime import timedelta

    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _paper_day_pnl(day: date) -> Decimal:
    """Sum of settled day PnL across real accounts (replay excluded)."""
    from core.models.positions import PortfolioSnapshot

    total = Decimal("0.0000")
    for row in PortfolioSnapshot.objects.filter(snapshot_date=day):
        if row.account.user.username.startswith(REPLAY_PREFIX):
            continue
        total += row.day_pnl
    return total


def _count_unexplained(start_utc, end_utc) -> int:
    """Rejects without reasons across all three rejection classes."""
    from core.models.orders import Order
    from core.models.risk import RiskEvent
    from market_data.models import BadTick

    unexplained_ticks = BadTick.objects.filter(
        created_at__gte=start_utc, created_at__lt=end_utc, details=""
    ).count()
    unexplained_orders = Order.objects.filter(
        placed_at__gte=start_utc,
        placed_at__lt=end_utc,
        status="REJ",
        rejection_reason="",
    ).count()
    unexplained_risk = RiskEvent.objects.filter(
        timestamp__gte=start_utc,
        timestamp__lt=end_utc,
        action="BLOCKED",
        message="",
    ).count()
    return unexplained_ticks + unexplained_orders + unexplained_risk


def _reset_replay_account(account) -> None:
    """Fresh ledger for a replay account (trial evidence stays readable)."""
    from core.models.accounts import AccountLedgerEntry
    from core.models.orders import Order, OrderEvent
    from core.models.positions import Position
    from core.models.trades import Trade

    Trade.objects.filter(account=account).delete()
    OrderEvent.objects.filter(order__account=account).delete()
    Order.objects.filter(account=account).delete()
    Position.objects.filter(account=account).delete()
    AccountLedgerEntry.objects.filter(account=account).delete()
    account.current_balance = account.initial_capital
    account.margin_used = Decimal("0.0000")
    account.realized_pnl = Decimal("0.0000")
    account.unrealized_pnl = Decimal("0.0000")
    account.save()
