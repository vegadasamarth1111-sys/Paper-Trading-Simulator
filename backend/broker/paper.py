"""
Paper Trading Simulator — Paper broker (Phase 2, plan section 5)
===================================================================
Realistic simulated execution against virtual capital:

- Full lifecycle NEW -> VALIDATED -> OPEN -> [PARTIALLY_FILLED] -> FILLED,
  with REJECTED / CANCELLED / EXPIRED terminals. Every transition writes
  an OrderEvent (timestamp + reason + metadata). Illegal transitions raise
  (the model enforces the map; this module never bypasses it).
- Idempotent placement: repeating a client_order_id returns the original
  order instead of creating a duplicate (retry-safe).
- Margin is blocked at placement (value + estimated costs check) and
  released on terminal states; fills transfer margin into the position.
- Market orders fill immediately against the latest known price; limit and
  stop orders rest until process_snapshot() matches them. IOC leftovers
  cancel after one matching pass.
- Bracket orders: on full entry fill, target (limit) and stop (SL) legs
  spawn; the first leg to fill cancels its sibling (OCO).
- Delivery (CNC) sells are capped at the held quantity — delivery never
  goes short. Naked option writes are rejected (v1 rule).
- All money moves through Ledger + PositionUpdater inside ONE transaction
  per fill. Fills are immutable Trade rows; corrections are adjusting
  entries, never edits.

Latency never invents prices: matching uses the quote visible after the
configured delay (passed in by the caller); latency_ms is audit metadata.
"""

import logging
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from core.clock import Clock, ClockMode
from core.enums import OrderSide, OrderStatus, OrderType, OrderValidity, ProductType
from core.exceptions import InvalidOrderTransitionError
from core.models.accounts import Account
from core.models.instruments import Instrument
from core.models.orders import Order
from core.models.strategies import StrategyRun

from . import fills as fill_sim
from .costs import CostRates, active_rates, calculate
from .fills import FillConfig, MarketSnapshot
from .interface import BrokerInterface
from .ledger import log_margin_event
from .margin import NakedShortBlocked, refresh_margin, required_margin

logger = logging.getLogger("core")
UTC = ZoneInfo("UTC")


def _fill_config() -> FillConfig:
    """Realism knobs from env (safe defaults when unset)."""
    from decouple import config

    return FillConfig(
        slippage_bps=Decimal(str(config("PAPER_SLIPPAGE_BPS", default="2"))),
        slippage_ticks=config("PAPER_SLIPPAGE_TICKS", default=0, cast=int),
        tick_size=Decimal("0.05"),
        max_depth_ratio=Decimal(str(config("PAPER_FILL_MAX_DEPTH_RATIO", default="0.5"))),
        conservative_limit=config("PAPER_CONSERVATIVE_LIMIT", default=True, cast=bool),
        min_bid_qty=config("PAPER_MIN_DEPTH_QTY", default=0, cast=int),
        min_volume=config("PAPER_MIN_VOLUME", default=0, cast=int),
    )


def _latency_ms() -> int:
    from decouple import config

    return config("PAPER_LATENCY_MS", default=300, cast=int)


def _cost_rates() -> CostRates:
    """Active CostTable row, or Zerodha-style defaults when never seeded."""
    return active_rates()


def _snapshot_for(instrument: Instrument) -> MarketSnapshot | None:
    """
    Best current quote for immediate market-order pricing.

    Free tier has LTP only (no depth feed), so bid == ask == ltp with zero
    depth — market orders fill in full at ltp +/- slippage. Depth-aware
    partial fills engage as soon as a depth feed provides quantities.
    """
    from market_data.services import get_latest_price

    quote = get_latest_price(instrument.symbol)  # type: ignore[attr-defined]
    if quote is None:
        return None
    ltp = quote["ltp"]
    return MarketSnapshot(
        bid=ltp,
        ask=ltp,
        ltp=ltp,
        upper_circuit=getattr(instrument, "upper_circuit", None),
        lower_circuit=getattr(instrument, "lower_circuit", None),
        latency_ms=_latency_ms(),
    )


class PaperBroker(BrokerInterface):
    """Simulated broker. All state changes are transactional + audited."""

    def __init__(self, clock: Clock | None = None) -> None:
        # Single source of time (plan section 2): the risk engine judges
        # the trading window against this clock. Tests inject a REPLAY
        # clock frozen inside market hours; production uses wall time.
        self._clock = clock or Clock(mode=ClockMode.LIVE)

    # ------------------------------------------------------------------
    # Placement
    # ------------------------------------------------------------------
    @transaction.atomic
    def place_order(
        self,
        *,
        account: Account,
        instrument: Instrument,
        side: str,
        order_type: str,
        quantity: int,
        price: Decimal | None = None,
        trigger_price: Decimal | None = None,
        product_type: str = "MIS",
        validity: str = "DAY",
        client_order_id: str,
        target_price: Decimal | None = None,
        stoploss_price: Decimal | None = None,
        strategy_run: StrategyRun | None = None,
        signal_id: str | None = None,
        stop_loss_price: Decimal | None = None,
        skip_risk: bool = False,
    ) -> Order:
        from core.models.orders import Order, OrderEvent

        # Idempotency first: retries return the original, never a duplicate.
        existing = Order.objects.filter(client_order_id=client_order_id).first()
        if existing is not None:
            logger.info("Idempotent replay: returning %s", client_order_id)
            return existing

        order = Order(
            client_order_id=client_order_id,
            account=account,
            instrument=instrument,
            side=side,
            order_type=order_type,
            product_type=product_type,
            validity=validity,
            quantity=quantity,
            price=price,
            trigger_price=trigger_price,
            strategy_run=strategy_run,
            is_bracket_order=target_price is not None and stoploss_price is not None,
            target_price=target_price,
            stoploss_price=stoploss_price,
        )
        order.save()  # NEW; pending_quantity initialized by save()

        def _reject(reason: str) -> Order:
            order.transition_to(OrderStatus.REJECTED, reason=reason)
            order.save(update_fields=["status", "rejection_reason", "updated_at"])
            OrderEvent.objects.create(
                order=order,
                from_status=OrderStatus.NEW,
                to_status=OrderStatus.REJECTED,
                reason=reason,
            )
            logger.info("Order %s REJECTED: %s", client_order_id, reason)
            return order

        # --- Structural validation (before any money moves) ---
        if side not in (OrderSide.BUY, OrderSide.SELL):
            return _reject(f"invalid side {side!r}")
        if quantity <= 0:
            return _reject("quantity must be positive")
        if order_type in (OrderType.LIMIT, OrderType.STOP_LOSS) and price is None:
            return _reject(f"{order_type} requires a limit price")
        if (
            order_type in (OrderType.STOP_LOSS, OrderType.STOP_LOSS_MARKET)
            and trigger_price is None
        ):
            return _reject(f"{order_type} requires a trigger price")
        if getattr(account, "kill_switch_active", False):
            return _reject("kill switch active on account")

        # Lot-size rule (plan 5.6): F&O quantity must be a lot multiple.
        lot_size = getattr(instrument, "lot_size", 1) or 1
        segment = getattr(instrument, "segment", "EQ")
        if segment in ("FUT", "OPT") and quantity % lot_size != 0:
            return _reject(f"quantity {quantity} is not a multiple of lot size {lot_size}")

        # Naked option writes are disabled in v1 (user decision).
        if segment == "OPT" and side == OrderSide.SELL:
            try:
                required_margin(
                    quantity=quantity,
                    price=price or Decimal("1"),
                    segment=segment,
                    product=product_type,
                    side=side,
                )
            except NakedShortBlocked as exc:
                return _reject(str(exc))

        # Delivery can never go short: cap the sell at the held long.
        if segment == "EQ" and product_type == ProductType.CNC and side == OrderSide.SELL:
            held = self._open_quantity(account, instrument)
            if quantity > held:
                return _reject(f"CNC sell {quantity} exceeds held quantity {held}")

        # Circuit guard for priced orders.
        upper = getattr(instrument, "upper_circuit", None)
        lower = getattr(instrument, "lower_circuit", None)
        if price is not None:
            if upper is not None and price > upper:
                return _reject(f"limit {price} above upper circuit {upper}")
            if lower is not None and price < lower:
                return _reject(f"limit {price} below lower circuit {lower}")

        # --- Reference price (margin + risk need a price to judge value) ---
        ref_price = price or self._reference_price(instrument)
        if ref_price is None:
            return _reject("no market price available (stale data guard)")

        # --- Risk engine (sits between strategy and broker; plan section 6).
        # Skipped ONLY for the engine's own exits (kill switch, EOD), which
        # must be able to close into a halted account. Every bypassed order
        # is still fully audited via OrderEvents.
        if not skip_risk:
            from risk.engine import CheckRequest, RiskEngine

            decision = RiskEngine.check(
                CheckRequest(
                    account=account,
                    instrument=instrument,
                    side=side,
                    order_type=order_type,
                    quantity=quantity,
                    product_type=product_type,
                    ref_price=ref_price,
                    trigger_price=trigger_price,
                    stop_loss_price=stop_loss_price or stoploss_price,
                    signal_id=signal_id,
                    strategy_run=strategy_run,
                    client_order_id=client_order_id,
                    exclude_order_id=order.id,
                    now=self._clock.now(),
                )
            )
            if not decision.approved:
                return _reject(f"risk {decision.rule}: {decision.message}")

        # --- Margin check (value + estimated costs must fit) ---
        try:
            need = required_margin(
                quantity=quantity,
                price=ref_price,
                segment=segment,
                product=product_type,
                side=side,
            )
        except NakedShortBlocked as exc:
            return _reject(str(exc))
        from .costs import estimate_for_margin

        est_costs = estimate_for_margin(
            side=side,
            quantity=quantity,
            price=ref_price,
            segment=segment,
            product=product_type,
            rates=_cost_rates(),
        )
        available = account.available_margin  # type: ignore[attr-defined]
        if need.margin + est_costs > available:
            return _reject(
                f"insufficient margin: need {need.margin + est_costs:,.2f}, have {available:,.2f}"
            )

        order.transition_to(OrderStatus.VALIDATED, reason="checks passed")
        order.save(update_fields=["status", "updated_at"])
        OrderEvent.objects.create(
            order=order,
            from_status=OrderStatus.NEW,
            to_status=OrderStatus.VALIDATED,
            reason="checks passed",
            metadata={
                "margin_blocked": str(need.margin),
                "margin_basis": need.basis,
                "signal_id": signal_id,
            },
        )
        order.transition_to(OrderStatus.OPEN, reason="sent to matching engine")
        order.opened_at = timezone.now()
        order.save(update_fields=["status", "opened_at", "updated_at"])
        OrderEvent.objects.create(
            order=order,
            from_status=OrderStatus.VALIDATED,
            to_status=OrderStatus.OPEN,
            reason="sent to matching engine",
        )
        # Refresh AFTER the order is OPEN: margin is derived from resting
        # orders, and refreshing while NEW would unblock the just-checked
        # amount (the order is invisible to the recompute until OPEN).
        log_margin_event(account, need.margin, block=True, reason=f"order {client_order_id}")
        refresh_margin(account)

        # Market orders match immediately against the visible quote.
        if order_type == OrderType.MARKET:
            snapshot = _snapshot_for(instrument)
            if snapshot is None:
                refresh_margin(account)
                order.transition_to(OrderStatus.REJECTED, reason="no quote (stale data)")
                order.save(update_fields=["status", "rejection_reason", "updated_at"])
                OrderEvent.objects.create(
                    order=order,
                    from_status=OrderStatus.OPEN,
                    to_status=OrderStatus.REJECTED,
                    reason="no quote (stale data)",
                )
                return order
            self._match(order, snapshot, _fill_config())
            if order.validity == OrderValidity.IOC and order.status == OrderStatus.OPEN:
                self._cancel_remainder(order, reason="IOC: unfilled quantity cancelled")
        return order

    # ------------------------------------------------------------------
    # Modify / cancel
    # ------------------------------------------------------------------
    @transaction.atomic
    def modify_order(
        self,
        order: Order,
        *,
        price: Decimal | None = None,
        trigger_price: Decimal | None = None,
        quantity: int | None = None,
    ) -> Order:
        from core.models.orders import OrderEvent

        if order.status != OrderStatus.OPEN:  # type: ignore[attr-defined]
            raise InvalidOrderTransitionError(
                message=f"Only OPEN orders can be modified (is {order.status})",  # type: ignore[attr-defined]
                details={"order_id": str(order.id)},  # type: ignore[attr-defined]
            )
        if price is not None:
            order.price = price  # type: ignore[attr-defined]
        if trigger_price is not None:
            order.trigger_price = trigger_price  # type: ignore[attr-defined]
        if quantity is not None:
            if quantity <= order.filled_quantity:  # type: ignore[attr-defined]
                raise InvalidOrderTransitionError(
                    message="New quantity must exceed already-filled quantity",
                    details={"order_id": str(order.id)},  # type: ignore[attr-defined]
                )
            order.quantity = quantity  # type: ignore[attr-defined]
            order.pending_quantity = quantity - order.filled_quantity  # type: ignore[attr-defined]
        order.save()  # type: ignore[attr-defined]
        OrderEvent.objects.create(
            order=order,
            from_status=OrderStatus.OPEN,
            to_status=OrderStatus.OPEN,
            reason="modified",
            metadata={"price": str(price), "trigger": str(trigger_price), "qty": quantity},
        )
        return order

    @transaction.atomic
    def cancel_order(self, order: Order, *, reason: str = "") -> Order:
        from core.models.orders import OrderEvent

        if order.status not in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED):  # type: ignore[attr-defined]
            raise InvalidOrderTransitionError(
                message=f"Only open orders can be cancelled (is {order.status})",  # type: ignore[attr-defined]
                details={"order_id": str(order.id)},  # type: ignore[attr-defined]
            )
        previous = order.status  # type: ignore[attr-defined]
        order.transition_to(OrderStatus.CANCELLED, reason=reason or "user cancel")  # type: ignore[attr-defined]
        order.cancelled_at = timezone.now()  # type: ignore[attr-defined]
        order.save(update_fields=["status", "cancelled_at", "updated_at"])  # type: ignore[attr-defined]
        OrderEvent.objects.create(
            order=order,
            from_status=previous,
            to_status=OrderStatus.CANCELLED,
            reason=reason or "user cancel",
        )
        self._release_order_margin(order)
        return order

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------
    @transaction.atomic
    def process_snapshot(self, instrument: Instrument, snapshot: MarketSnapshot) -> dict:
        """Match every resting OPEN order on the instrument against one quote."""
        from core.models.orders import Order

        outcome: dict = {"fills": [], "rejected": [], "triggered": []}
        resting = (
            Order.objects.filter(
                instrument=instrument,
                status__in=[OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED],
            )
            .select_related("account")
            .order_by("placed_at")
        )
        config = _fill_config()
        for order in resting:
            # Stop legs wait for their trigger; nothing to do yet. A missing
            # trigger never rests silently: _match rejects it as malformed.
            if (
                order.order_type in (OrderType.STOP_LOSS, OrderType.STOP_LOSS_MARKET)
                and order.trigger_price is not None
                and not fill_sim.stop_triggered(
                    side=order.side, trigger=order.trigger_price, snapshot=snapshot
                )
            ):
                continue
            fills_before = order.filled_quantity
            self._match(order, snapshot, config)
            if order.filled_quantity != fills_before:
                outcome["fills"].append(str(order.client_order_id))
            if order.validity == OrderValidity.IOC and order.status in (
                OrderStatus.OPEN,
                OrderStatus.PARTIALLY_FILLED,
            ):
                self._cancel_remainder(order, reason="IOC: unfilled quantity cancelled")
        return outcome

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _reject_malformed(order: Order, reason: str) -> None:
        """Reject a resting order that violates placement invariants (corruption guard)."""
        from core.models.orders import OrderEvent

        previous = order.status
        order.transition_to(OrderStatus.REJECTED, reason=reason)
        order.save(update_fields=["status", "rejection_reason", "updated_at"])
        OrderEvent.objects.create(
            order=order, from_status=previous, to_status=OrderStatus.REJECTED, reason=reason
        )
        PaperBroker._release_static_margin(order)

    @staticmethod
    def _release_static_margin(order: Order) -> None:
        log_margin_event(
            order.account,
            Decimal("0.0000"),
            block=False,
            reason=f"order {order.client_order_id} terminal",
        )
        refresh_margin(order.account)

    def _match(self, order: Order, snapshot: MarketSnapshot, config: FillConfig) -> None:
        """Match one order; persists fills, legs, OCO, and margin moves."""
        from core.models.orders import OrderEvent

        if order.order_type == OrderType.MARKET:
            result = fill_sim.fill_market(
                side=order.side,
                quantity=order.pending_quantity,  # type: ignore[attr-defined]
                snapshot=snapshot,
                config=config,
            )
        elif order.order_type == OrderType.LIMIT:
            if order.price is None:
                # Placement requires a limit price; None means row corruption.
                self._reject_malformed(order, "limit order without a price")
                return
            result = fill_sim.fill_limit(
                side=order.side,
                quantity=order.pending_quantity,
                limit=order.price,
                snapshot=snapshot,
                config=config,
            )
        else:  # SL / SL-M
            if order.trigger_price is None:
                self._reject_malformed(order, "stop order without a trigger")
                return
            result = fill_sim.fill_stop(
                side=order.side,
                quantity=order.pending_quantity,
                trigger=order.trigger_price,
                limit=order.price if order.order_type == OrderType.STOP_LOSS else None,
                snapshot=snapshot,
                config=config,
            )
            if result.trigger_hit:
                OrderEvent.objects.create(
                    order=order,
                    from_status=order.status,
                    to_status=order.status,  # type: ignore[attr-defined]
                    reason=f"stop triggered at {order.trigger_price}",  # type: ignore[attr-defined]
                )
        if result.rejected:
            previous = order.status  # type: ignore[attr-defined]
            order.transition_to(OrderStatus.REJECTED, reason=result.reject_reason)  # type: ignore[attr-defined]
            order.save(update_fields=["status", "rejection_reason", "updated_at"])  # type: ignore[attr-defined]
            OrderEvent.objects.create(
                order=order,
                from_status=previous,
                to_status=OrderStatus.REJECTED,
                reason=result.reject_reason,
            )
            self._release_order_margin(order)
            return
        if result.filled_qty > 0 and result.fill_price is not None:
            self._record_fill(order, result.filled_qty, result.fill_price, snapshot)

    def _record_fill(
        self, order: Order, quantity: int, price: Decimal, snapshot: MarketSnapshot
    ) -> None:
        """Persist one fill: Trade + order state + position + ledger + legs/OCO."""
        import uuid as uuid_lib

        from core.models.orders import OrderEvent
        from core.models.trades import Trade

        from .ledger import record_fill_entries
        from .positions import PositionState, apply_fill

        previous_status = order.status  # type: ignore[attr-defined]
        breakdown = calculate(
            side=order.side,
            quantity=quantity,
            price=price,  # type: ignore[attr-defined]
            segment=order.instrument.segment,  # type: ignore[attr-defined]
            product=order.product_type,  # type: ignore[attr-defined]
            rates=_cost_rates(),
        )
        trade = Trade.objects.create(
            trade_id=f"TRD-{uuid_lib.uuid4().hex[:12].upper()}",
            order=order,
            account=order.account,
            instrument=order.instrument,  # type: ignore[attr-defined]
            side=order.side,
            quantity=quantity,
            price=price,  # type: ignore[attr-defined]
            value=price * quantity,
            executed_at=timezone.now(),
            expected_price=self._expected_price(order, snapshot),
            **breakdown.as_dict(),
        )

        # Order quantities + average fill price (volume-weighted).
        order.filled_quantity += quantity  # type: ignore[attr-defined]
        order.pending_quantity = order.quantity - order.filled_quantity  # type: ignore[attr-defined]
        prior_avg = order.average_fill_price or Decimal("0")  # type: ignore[attr-defined]
        prior_qty = order.filled_quantity - quantity  # type: ignore[attr-defined]
        order.average_fill_price = (  # type: ignore[attr-defined]
            (prior_avg * prior_qty + price * quantity) / order.filled_quantity  # type: ignore[attr-defined]
        )
        order.total_charges += breakdown.total  # type: ignore[attr-defined]
        filled_fully = order.pending_quantity == 0  # type: ignore[attr-defined]
        new_status = OrderStatus.FILLED if filled_fully else OrderStatus.PARTIALLY_FILLED
        order.transition_to(new_status, reason=f"fill {quantity}@{price}")
        if filled_fully:
            order.filled_at = timezone.now()  # type: ignore[attr-defined]
            update_fields = [
                "filled_quantity",
                "pending_quantity",
                "average_fill_price",
                "total_charges",
                "status",
                "filled_at",
                "updated_at",
            ]
        else:
            update_fields = [
                "filled_quantity",
                "pending_quantity",
                "average_fill_price",
                "total_charges",
                "status",
                "updated_at",
            ]
        order.save(update_fields=update_fields)  # type: ignore[attr-defined]
        OrderEvent.objects.create(
            order=order,
            from_status=previous_status,
            to_status=new_status,
            reason=f"fill {quantity}@{price}",
            metadata={
                "trade_id": trade.trade_id,
                "charges": str(breakdown.total),
                "slippage": str(trade.slippage),
                "latency_ms": snapshot.latency_ms,
            },
        )

        # Position: apply the fill. Margin is recomputed from truth below —
        # never hand-adjusted here (see margin.refresh_margin).
        from core.models.positions import Position

        position, _ = Position.objects.get_or_create(
            account=order.account,
            instrument=order.instrument,  # type: ignore[attr-defined]
            is_open=True,
            defaults={
                "product_type": order.product_type,  # type: ignore[attr-defined]
                "side": "LONG" if order.side == OrderSide.BUY else "SHORT",  # type: ignore[attr-defined]
                "quantity": 0,
                "average_entry_price": Decimal("0.0000"),
                "opened_at": timezone.now(),
            },
        )
        state = PositionState(
            quantity=position.quantity,
            average_entry_price=position.average_entry_price,
            realized_pnl=position.realized_pnl,
        )
        realized_before = state.realized_pnl
        apply_fill(state, side=order.side, quantity=quantity, price=price)  # type: ignore[attr-defined]
        position.quantity = state.quantity
        position.average_entry_price = state.average_entry_price
        position.realized_pnl = state.realized_pnl
        position.total_charges += breakdown.total
        if state.quantity == 0:
            position.is_open = False
            position.closed_at = timezone.now()
        position.side = (  # type: ignore[attr-defined]
            "LONG" if state.quantity > 0 else "SHORT" if state.quantity < 0 else "FLAT"
        )
        position.save()

        # Ledger: cash move for the fill. Leveraged (MIS/FUT) fills move only
        # realized PnL and costs — margin, blocked separately, is the binding
        # constraint. Delivery/option fills move full value (paid in full).
        order.account.refresh_from_db()  # type: ignore[attr-defined]
        leveraged = order.instrument.segment == "FUT" or (  # type: ignore[attr-defined]
            order.instrument.segment == "EQ"  # type: ignore[attr-defined]
            and order.product_type == "MIS"  # type: ignore[attr-defined]
        )
        record_fill_entries(
            account=order.account,
            trade=trade,  # type: ignore[attr-defined]
            position_realized=state.realized_pnl - realized_before,
            leveraged=leveraged,
        )
        order.account.save(
            update_fields=["current_balance", "realized_pnl", "peak_balance", "updated_at"]
        )  # type: ignore[attr-defined]
        # Margin is derived from open positions + resting orders — one call
        # keeps it exact no matter which path filled, flipped, or cancelled.
        log_margin_event(
            order.account,
            breakdown.total,
            block=False,  # type: ignore[attr-defined]
            reason=f"fill {trade.trade_id}",
        )
        refresh_margin(order.account)  # type: ignore[attr-defined]

        # Bracket legs spawn on FULL entry fill; OCO on any leg fill.
        if order.is_bracket_order and filled_fully and order.parent_order_id is None:  # type: ignore[attr-defined]
            self._spawn_bracket_legs(order)
        if order.parent_order_id is not None and filled_fully:  # type: ignore[attr-defined]
            self._cancel_sibling_legs(order)

    # -- helpers ---------------------------------------------------------
    def _expected_price(self, order: Order, snapshot: MarketSnapshot) -> Decimal:
        if order.order_type == OrderType.MARKET:  # type: ignore[attr-defined]
            return snapshot.ltp
        return order.price or snapshot.ltp  # type: ignore[attr-defined]

    def _reference_price(self, instrument: Instrument) -> Decimal | None:
        from market_data.services import get_latest_price

        quote = get_latest_price(instrument.symbol)  # type: ignore[attr-defined]
        return quote["ltp"] if quote else None

    def _open_quantity(self, account: Account, instrument: Instrument) -> int:
        from core.models.positions import Position

        row = Position.objects.filter(account=account, instrument=instrument, is_open=True).first()
        return max(row.quantity, 0) if row else 0

    def _release_order_margin(self, order: Order) -> None:
        """Release leftover blocks by recomputing from truth."""
        log_margin_event(
            order.account,
            Decimal("0.0000"),
            block=False,
            reason=f"order {order.client_order_id} terminal",
        )  # type: ignore[attr-defined]
        refresh_margin(order.account)  # type: ignore[attr-defined]

    def _cancel_remainder(self, order: Order, *, reason: str) -> None:
        from core.models.orders import OrderEvent

        previous = order.status  # type: ignore[attr-defined]
        order.transition_to(OrderStatus.CANCELLED, reason=reason)  # type: ignore[attr-defined]
        order.cancelled_at = timezone.now()  # type: ignore[attr-defined]
        order.save(update_fields=["status", "cancelled_at", "updated_at"])  # type: ignore[attr-defined]
        OrderEvent.objects.create(
            order=order, from_status=previous, to_status=OrderStatus.CANCELLED, reason=reason
        )
        self._release_order_margin(order)

    def _spawn_bracket_legs(self, entry: Order) -> None:
        """Target (limit) + stop (SL) legs, opposite side, same qty. OCO linked."""
        exit_side = OrderSide.SELL if entry.side == OrderSide.BUY else OrderSide.BUY  # type: ignore[attr-defined]
        for leg_type, leg_price, leg_trigger in (
            (OrderType.LIMIT, entry.target_price, None),  # type: ignore[attr-defined]
            (OrderType.STOP_LOSS, entry.stoploss_price, entry.stoploss_price),  # type: ignore[attr-defined]
        ):
            self.place_order(
                account=entry.account,
                instrument=entry.instrument,  # type: ignore[attr-defined]
                side=exit_side,
                order_type=leg_type,
                quantity=entry.quantity,
                price=leg_price,  # type: ignore[attr-defined]
                trigger_price=leg_trigger,
                product_type=entry.product_type,  # type: ignore[attr-defined]
                validity=entry.validity,  # type: ignore[attr-defined]
                client_order_id=f"{entry.client_order_id}-{leg_type}-leg",  # type: ignore[attr-defined]
                strategy_run=entry.strategy_run,  # type: ignore[attr-defined]
            )
            # Link the leg back to its entry for OCO.
            from core.models.orders import Order

            leg = Order.objects.get(
                client_order_id=f"{entry.client_order_id}-{leg_type}-leg"  # type: ignore[attr-defined]
            )
            leg.parent_order = entry  # type: ignore[attr-defined]
            leg.save(update_fields=["parent_order", "updated_at"])

    def _cancel_sibling_legs(self, filled_leg: Order) -> None:
        """OCO: when one exit leg fills, cancel its siblings."""
        from core.models.orders import Order

        siblings = Order.objects.filter(
            parent_order=filled_leg.parent_order,  # type: ignore[attr-defined]
            status__in=[OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED],
        ).exclude(id=filled_leg.id)  # type: ignore[attr-defined]
        for sibling in siblings:
            try:
                self.cancel_order(
                    sibling, reason=f"OCO: sibling {filled_leg.client_order_id} filled"
                )  # type: ignore[attr-defined]
            except InvalidOrderTransitionError:
                continue  # raced to terminal between filter and cancel; terminal wins
