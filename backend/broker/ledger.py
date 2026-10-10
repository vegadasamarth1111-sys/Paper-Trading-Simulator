"""
Paper Trading Simulator — Ledger writer + reconciler (Phase 2, plan 9.2)
==========================================================================
Money movement rules:

- BUY fill: TRADE_DR of -(value + charges). SELL fill: TRADE_CR of
  +(value - charges). Costs are inside the fill entries (no separate
  COST_DR needed per fill; the entry description itemizes them).
- Margin block/release: zero-amount MARGIN_BLK/MARGIN_REL rows so the
  margin lifecycle is auditable without moving the balance.
- Realized PnL on position close: MTM_ADJUSTMENT? No — realized PnL is
  already embedded in the sell credits (sell proceeds minus buy cost flows
  through balance automatically). The account.realized_pnl FIELD is updated
  for display; the LEDGER stays the balance truth.
- Balance invariant: current_balance == initial_capital + sum(amount).
  The nightly reconciler rebuilds both balance AND positions from fills
  and diffs them against stored values. Any difference is a critical
  alert, never an auto-fix.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from core.models.accounts import Account
from core.models.trades import Trade

UTC = ZoneInfo("UTC")
TOLERANCE = Decimal("0.0001")


@dataclass
class ReconcileMismatch:
    """One unexplained difference between stored and rebuilt state."""

    area: str  # "balance" | "position" | "charges"
    key: str
    stored: str
    rebuilt: str
    difference: str


@dataclass
class ReconcileReport:
    """Nightly reconciliation verdict."""

    account_id: str
    checked_at: datetime
    balance_ok: bool
    positions_ok: bool
    charges_ok: bool
    mismatches: list[ReconcileMismatch] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.balance_ok and self.positions_ok and self.charges_ok


def record_fill_entries(
    *,
    account: Account,
    trade: Trade,
    position_realized: Decimal,
    leveraged: bool = False,
) -> Decimal:
    """
    Write ledger rows for one fill. Returns the new account balance.

    Cash settlement differs by product (both conserve money exactly):
    - Delivery/options (leveraged=False): BUY debits value + charges
      (stock/option paid in full); SELL credits value - charges.
    - Leveraged intraday/futures (leveraged=True): margin (blocked
      separately) is the binding constraint, so fills move only PnL and
      costs — BUY/SELL entries equal position_realized - charges. A full
      round trip nets exactly gross PnL minus all costs, and intraday
      equity reads correctly instead of dropping by full notional.

    Must run inside the same transaction as the Trade row. Updates
    account.current_balance / realized_pnl and peak_balance in place
    (caller saves within the transaction).
    """
    from core.models.accounts import AccountLedgerEntry

    if leveraged:
        amount = position_realized - trade.total_charges  # type: ignore[attr-defined]
    elif trade.side == "BUY":  # type: ignore[attr-defined]
        amount = -(trade.value + trade.total_charges)  # type: ignore[attr-defined]
    else:
        amount = trade.value - trade.total_charges  # type: ignore[attr-defined]
    entry_type = (
        AccountLedgerEntry.EntryType.TRADE_DEBIT
        if trade.side == "BUY"  # type: ignore[attr-defined]
        else AccountLedgerEntry.EntryType.TRADE_CREDIT
    )

    account.current_balance = account.current_balance + amount  # type: ignore[attr-defined]
    if position_realized != 0:
        account.realized_pnl = account.realized_pnl + position_realized  # type: ignore[attr-defined]
    equity = account.current_balance + account.unrealized_pnl  # type: ignore[attr-defined]
    if equity > account.peak_balance:  # type: ignore[attr-defined]
        account.peak_balance = equity  # type: ignore[attr-defined]

    AccountLedgerEntry.objects.create(
        account=account,
        entry_type=entry_type,
        amount=amount,
        balance_after=account.current_balance,  # type: ignore[attr-defined]
        reference_type="trade",
        reference_id=str(trade.id),  # type: ignore[attr-defined]
        description=(
            f"{trade.side} {trade.quantity}x {trade.instrument.symbol} "  # type: ignore[attr-defined]
            f"@ {trade.price} (charges {trade.total_charges})"  # type: ignore[attr-defined]
        ),
    )
    return account.current_balance  # type: ignore[no-any-return]


def record_margin(account: Account, amount: Decimal, *, block: bool, reason: str) -> None:
    """Margin lifecycle rows (auditable). Margin totals come from refresh_margin."""
    log_margin_event(account, amount, block=block, reason=reason)


def log_margin_event(account: Account, amount: Decimal, *, block: bool, reason: str) -> None:
    """Zero-amount margin audit row. Never touches balances (see refresh_margin)."""
    from core.models.accounts import AccountLedgerEntry

    entry_type = (
        AccountLedgerEntry.EntryType.MARGIN_BLOCK
        if block
        else AccountLedgerEntry.EntryType.MARGIN_RELEASE
    )
    AccountLedgerEntry.objects.create(
        account=account,
        entry_type=entry_type,
        amount=Decimal("0.0000"),
        balance_after=account.current_balance,  # type: ignore[attr-defined]
        reference_type="margin",
        reference_id="",
        description=f"{'Blocked' if block else 'Released'} {amount}: {reason}",
    )


def rebuild_balance(account: Account) -> Decimal:
    """Replay the ledger: initial_capital + sum(amount). The balance truth."""
    from core.models.accounts import AccountLedgerEntry

    total = Decimal("0.0000")
    for row in AccountLedgerEntry.objects.filter(account=account).values_list("amount", flat=True):
        total += row
    return account.initial_capital + total  # type: ignore[attr-defined]


def rebuild_positions(account: Account) -> dict:
    """
    Replay immutable fills into net positions.

    Returns {(instrument_id): {"quantity": int, "avg": Decimal,
    "realized": Decimal, "charges": Decimal}}.
    """
    from broker.positions import PositionState, apply_fill
    from core.models.trades import Trade

    states: dict = {}
    fills = (
        Trade.objects.filter(account=account)
        .order_by("executed_at", "created_at")
        .select_related("instrument")
    )
    for fill in fills:
        key = fill.instrument_id
        state = states.get(key)
        if state is None:
            state = {
                "quantity": 0,
                "avg": Decimal("0.0000"),
                "realized": Decimal("0.0000"),
                "charges": Decimal("0.0000"),
            }
            states[key] = state
        ps = PositionState(
            quantity=state["quantity"],
            average_entry_price=state["avg"],
            realized_pnl=state["realized"],
        )
        apply_fill(ps, side=fill.side, quantity=fill.quantity, price=fill.price)
        state["quantity"] = ps.quantity
        state["avg"] = ps.average_entry_price
        state["realized"] = ps.realized_pnl
        state["charges"] += fill.total_charges
    return states


def reconcile_account(account: Account) -> ReconcileReport:
    """Nightly verdict: stored vs rebuilt balance, positions, and charges."""
    from datetime import datetime

    from django.db.models import Sum

    from core.models.positions import Position
    from core.models.trades import Trade

    now = datetime.now(UTC)
    mismatches: list[ReconcileMismatch] = []

    # --- Balance ---
    rebuilt_balance = rebuild_balance(account)
    balance_ok = abs(rebuilt_balance - account.current_balance) <= TOLERANCE  # type: ignore[attr-defined]
    if not balance_ok:
        mismatches.append(
            ReconcileMismatch(
                area="balance",
                key=str(account.id),  # type: ignore[attr-defined]
                stored=str(account.current_balance),  # type: ignore[attr-defined]
                rebuilt=str(rebuilt_balance),
                difference=str(account.current_balance - rebuilt_balance),  # type: ignore[attr-defined]
            )
        )

    # --- Positions ---
    rebuilt = rebuild_positions(account)
    stored = {p.instrument_id: p for p in Position.objects.filter(account=account, is_open=True)}
    positions_ok = True
    for key, state in rebuilt.items():
        if state["quantity"] == 0:
            if key in stored:
                positions_ok = False
                mismatches.append(
                    ReconcileMismatch(
                        area="position",
                        key=str(key),
                        stored="open",
                        rebuilt="flat (qty 0)",
                        difference="stale open row",
                    )
                )
            continue
        row = stored.get(key)
        if row is None:
            positions_ok = False
            mismatches.append(
                ReconcileMismatch(
                    area="position",
                    key=str(key),
                    stored="missing",
                    rebuilt=f"qty={state['quantity']} avg={state['avg']}",
                    difference="missing open row",
                )
            )
        elif (
            row.quantity != state["quantity"]
            or abs(row.average_entry_price - state["avg"]) > TOLERANCE
        ):
            positions_ok = False
            mismatches.append(
                ReconcileMismatch(
                    area="position",
                    key=str(key),
                    stored=f"qty={row.quantity} avg={row.average_entry_price}",
                    rebuilt=f"qty={state['quantity']} avg={state['avg']}",
                    difference=str(Decimal(row.quantity - state["quantity"])),
                )
            )
    for key in stored:
        if key not in rebuilt or rebuilt[key]["quantity"] == 0:
            if key in rebuilt:
                continue  # already reported above as stale
            positions_ok = False
            mismatches.append(
                ReconcileMismatch(
                    area="position",
                    key=str(key),
                    stored="open",
                    rebuilt="no fills",
                    difference="phantom open row",
                )
            )

    # --- Charges: order totals must equal the sum of their fills ---
    from core.models.orders import Order

    charges_ok = True
    for order in Order.objects.filter(account=account).prefetch_related("trades"):
        fills_total = sum((t.total_charges for t in order.trades.all()), Decimal("0.0000"))
        if abs(order.total_charges - fills_total) > TOLERANCE:
            charges_ok = False
            mismatches.append(
                ReconcileMismatch(
                    area="charges",
                    key=str(order.client_order_id),
                    stored=str(order.total_charges),
                    rebuilt=str(fills_total),
                    difference=str(order.total_charges - fills_total),
                )
            )

    total_trades = Trade.objects.filter(account=account).aggregate(n=Sum("quantity"))["n"]
    _ = total_trades  # counted for the report log line by the caller
    return ReconcileReport(
        account_id=str(account.id),  # type: ignore[attr-defined]
        checked_at=now,
        balance_ok=balance_ok,
        positions_ok=positions_ok,
        charges_ok=charges_ok,
        mismatches=mismatches,
    )
