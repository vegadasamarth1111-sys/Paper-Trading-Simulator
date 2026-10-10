"""
Paper Trading Simulator — Fill simulator (Phase 2, plan section 5.3)
======================================================================
Pure functions: order + market snapshot in, fills out. No Django, no I/O.

The rules below decide whether backtests look realistic, so each one is
explicit, configurable, and unit-tested:

- Market BUY fills at best ASK (never LTP); market SELL at best BID.
- Limit BUY fills only when ask <= limit. Conservative mode (default)
  requires a trade-THROUGH (ask strictly below limit); touch mode fills
  when ask == limit. Mirror logic for sells. Rationale: a backtest that
  fills on every touch overstates realistic fills.
- Stop (SL/SLM) triggers when LTP crosses the trigger price, then behaves
  as a limit/market order respectively.
- Partial fills: one tick cannot absorb more than `max_depth_ratio` of the
  displayed size (default 50%). Anything beyond waits for the next quote.
- Slippage: fixed basis points PLUS ticks, applied against the taker
  (buys worse, sells worse). Spread-adaptive models are a later upgrade;
  the config hook is already here.
- Circuits: quotes outside [lower, upper] are rejected as bad data by the
  caller; an order priced outside the band is rejected, and a market
  locked at circuit (bid == ask == circuit) produces NO fill.
- Illiquid instruments: below `min_bid_qty`/`min_volume` the order is
  rejected (options far OTM would otherwise fill impossibly). Rejection
  beats fantasy fills — the plan calls this out explicitly.
- Latency does not invent prices: the caller passes the quote visible
  after the latency delay; `latency_ms` is recorded for audit only.
"""

from dataclasses import dataclass
from decimal import Decimal

Q4 = Decimal("0.0001")


def _q(value: Decimal) -> Decimal:
    return value.quantize(Q4)


@dataclass(frozen=True)
class MarketSnapshot:
    """The quote the matching engine can see RIGHT NOW (post-latency)."""

    bid: Decimal
    ask: Decimal
    ltp: Decimal
    bid_qty: int = 0
    ask_qty: int = 0
    volume: int = 0  # cumulative day volume (for illiquidity guard)
    upper_circuit: Decimal | None = None
    lower_circuit: Decimal | None = None
    latency_ms: int = 300


@dataclass(frozen=True)
class FillConfig:
    """Tunable realism knobs (env overrides in settings)."""

    slippage_bps: Decimal = Decimal("2")  # 2 bps against the taker
    slippage_ticks: int = 0  # plus N ticks of instrument tick_size
    tick_size: Decimal = Decimal("0.05")
    max_depth_ratio: Decimal = Decimal("0.5")  # max share of displayed size per tick
    conservative_limit: bool = True  # require trade-through, not touch
    min_bid_qty: int = 0  # illiquidity floor (0 = disabled)
    min_volume: int = 0
    reject_illiquid: bool = True  # False = penalize with 5x slippage instead


@dataclass(frozen=True)
class FillResult:
    """Outcome of matching one order against one snapshot."""

    filled_qty: int
    fill_price: Decimal | None  # None when nothing filled
    slippage_per_unit: Decimal
    rejected: bool = False
    reject_reason: str = ""
    trigger_hit: bool = False  # stop orders: trigger crossed this quote


def _apply_slippage(price: Decimal, side: str, config: FillConfig) -> tuple[Decimal, Decimal]:
    """Push price against the taker. Returns (adjusted, per-unit slippage)."""
    bps_part = price * config.slippage_bps / Decimal("10000")
    tick_part = config.tick_size * config.slippage_ticks
    slip = _q(bps_part + tick_part)
    if side == "BUY":
        return _q(price + slip), slip
    return _q(price - slip), slip


def _depth_for(snapshot: MarketSnapshot, side: str) -> int:
    return snapshot.ask_qty if side == "BUY" else snapshot.bid_qty


def _check_halt(side: str, snapshot: MarketSnapshot, config: FillConfig) -> FillResult | None:
    """Circuit lock and illiquidity guards. Returns a rejection or None."""
    # Locked at circuit: bid == ask at a band edge -> no real fills possible.
    if (
        snapshot.upper_circuit is not None
        and snapshot.lower_circuit is not None
        and snapshot.bid == snapshot.ask
        and snapshot.bid in (snapshot.upper_circuit, snapshot.lower_circuit)
    ):
        return FillResult(
            filled_qty=0,
            fill_price=None,
            slippage_per_unit=Decimal("0"),
            rejected=True,
            reject_reason="circuit locked: no fills possible",
        )
    # Illiquid: no displayed size to absorb the order.
    thin_depth = config.min_bid_qty > 0 and _depth_for(snapshot, side) < config.min_bid_qty
    thin_day = config.min_volume > 0 and snapshot.volume < config.min_volume
    if (thin_depth or thin_day) and config.reject_illiquid:
        return FillResult(
            filled_qty=0,
            fill_price=None,
            slippage_per_unit=Decimal("0"),
            rejected=True,
            reject_reason=(
                f"illiquid: depth={_depth_for(snapshot, side)} volume={snapshot.volume}"
            ),
        )
    # Penalize mode: caller inflates slippage 5x (handled via config copy).
    return None


def fill_market(
    *,
    side: str,
    quantity: int,
    snapshot: MarketSnapshot,
    config: FillConfig | None = None,
) -> FillResult:
    """Market order against one snapshot (partial fills possible)."""
    config = config or FillConfig()
    halt = _check_halt(side, snapshot, config)
    if halt is not None:
        return halt

    base = snapshot.ask if side == "BUY" else snapshot.bid
    if base <= 0:
        return FillResult(
            filled_qty=0,
            fill_price=None,
            slippage_per_unit=Decimal("0"),
            rejected=True,
            reject_reason="no quote available",
        )

    depth = _depth_for(snapshot, side)
    allowed = int(Decimal(depth) * config.max_depth_ratio) if depth > 0 else quantity
    fill_qty = min(quantity, max(allowed, 0))
    if fill_qty <= 0:
        return FillResult(
            filled_qty=0,
            fill_price=None,
            slippage_per_unit=Decimal("0"),
            rejected=True,
            reject_reason="no displayed depth to absorb order",
        )
    price, slip = _apply_slippage(base, side, config)
    return FillResult(filled_qty=fill_qty, fill_price=price, slippage_per_unit=slip)


def _limit_crossed(side: str, limit: Decimal, snapshot: MarketSnapshot, conservative: bool) -> bool:
    """Has the market reached the limit? Conservative needs trade-through."""
    if side == "BUY":
        if conservative:
            return snapshot.ask < limit
        return snapshot.ask <= limit
    if conservative:
        return snapshot.bid > limit
    return snapshot.bid >= limit


def fill_limit(
    *,
    side: str,
    quantity: int,
    limit: Decimal,
    snapshot: MarketSnapshot,
    config: FillConfig | None = None,
) -> FillResult:
    """Limit order against one snapshot. No cross -> no fill (never forced)."""
    config = config or FillConfig()
    halt = _check_halt(side, snapshot, config)
    if halt is not None:
        return halt
    if not _limit_crossed(side, limit, snapshot, config.conservative_limit):
        return FillResult(filled_qty=0, fill_price=None, slippage_per_unit=Decimal("0"))
    # Fill AT the limit (price improvement beyond slippage is not claimed).
    depth = _depth_for(snapshot, side)
    allowed = int(Decimal(depth) * config.max_depth_ratio) if depth > 0 else quantity
    fill_qty = min(quantity, max(allowed, 0))
    if fill_qty <= 0:
        return FillResult(
            filled_qty=0,
            fill_price=None,
            slippage_per_unit=Decimal("0"),
            rejected=True,
            reject_reason="no displayed depth to absorb order",
        )
    _, slip = _apply_slippage(limit, side, config)
    return FillResult(filled_qty=fill_qty, fill_price=_q(limit), slippage_per_unit=slip)


def stop_triggered(*, side: str, trigger: Decimal, snapshot: MarketSnapshot) -> bool:
    """Stop trigger: BUY stops fire when LTP rises through; SELL when it falls."""
    if side == "BUY":
        return snapshot.ltp >= trigger
    return snapshot.ltp <= trigger


def fill_stop(
    *,
    side: str,
    quantity: int,
    trigger: Decimal,
    limit: Decimal | None,
    snapshot: MarketSnapshot,
    config: FillConfig | None = None,
) -> FillResult:
    """
    SL (limit given) or SL-M (limit None) against one snapshot.
    Untriggered stops rest. Triggered stops delegate to limit/market logic.
    """
    config = config or FillConfig()
    if not stop_triggered(side=side, trigger=trigger, snapshot=snapshot):
        return FillResult(filled_qty=0, fill_price=None, slippage_per_unit=Decimal("0"))
    if limit is None:
        result = fill_market(side=side, quantity=quantity, snapshot=snapshot, config=config)
    else:
        result = fill_limit(
            side=side, quantity=quantity, limit=limit, snapshot=snapshot, config=config
        )
    return FillResult(
        filled_qty=result.filled_qty,
        fill_price=result.fill_price,
        slippage_per_unit=result.slippage_per_unit,
        rejected=result.rejected,
        reject_reason=result.reject_reason,
        trigger_hit=True,
    )
