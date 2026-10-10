"""
Paper Trading Simulator — Cost calculator (Phase 2, plan section 5.4)
=======================================================================
Pure functions: trade facts in, itemized charges out. No Django queries
inside the math — the caller loads the CostTable row once and passes the
rates in, which keeps the calculator unit-testable and deterministic.

Zerodha-style schedule (defaults match broker.models.CostTable):
- Equity delivery: brokerage FREE. STT 0.1% buy AND sell.
- Equity intraday: brokerage min(0.03% turnover, Rs 20) per side.
  STT 0.025% on SELL only.
- Futures: brokerage capped Rs 20/side. STT 0.02% on SELL only.
- Options: brokerage capped Rs 20/side. STT 0.1% of premium on SELL only.
  Exercised (ITM cash-settled): STT 0.125% of intrinsic, no brokerage.
- Exchange + SEBI on both sides (options: on premium).
- GST 18% on (brokerage + exchange + SEBI).
- Stamp duty on BUY only (delivery 0.015%, intraday/F&O 0.002%).
- DP Rs 15.34 per scrip per day on delivery (CNC) SELLS — charged once
  per order here (per-day grouping is a settlement concern).

All money is Decimal. Each leg is quantized to 4dp (model precision);
display rounding to 2dp happens at presentation, never here.
"""

from dataclasses import dataclass
from decimal import Decimal

Q4 = Decimal("0.0001")


def _q(value: Decimal) -> Decimal:
    return value.quantize(Q4)


@dataclass(frozen=True)
class CostRates:
    """Rate snapshot. Mirrors CostTable fields; defaults are Zerodha-style."""

    brokerage_flat: Decimal = Decimal("20.0000")
    brokerage_pct: Decimal = Decimal("0.000300")
    brokerage_delivery_free: bool = True
    stt_delivery: Decimal = Decimal("0.001000")
    stt_intraday: Decimal = Decimal("0.000250")
    stt_futures: Decimal = Decimal("0.000200")
    stt_options: Decimal = Decimal("0.001000")
    stt_exercise: Decimal = Decimal("0.001250")
    exchange_equity: Decimal = Decimal("0.00002970")
    exchange_futures: Decimal = Decimal("0.00001730")
    exchange_options: Decimal = Decimal("0.00035030")
    sebi_rate: Decimal = Decimal("0.00000100")
    gst_rate: Decimal = Decimal("0.1800")
    stamp_delivery: Decimal = Decimal("0.000150")
    stamp_intraday: Decimal = Decimal("0.000020")
    dp_charges: Decimal = Decimal("15.3400")

    @classmethod
    def from_cost_table(cls, table: object) -> CostRates:
        """Build from a CostTable row (duck-typed for testability)."""
        fields = (
            "brokerage_flat",
            "brokerage_pct",
            "stt_delivery",
            "stt_intraday",
            "stt_futures",
            "stt_options",
            "stt_exercise",
            "exchange_equity",
            "exchange_futures",
            "exchange_options",
            "sebi_rate",
            "gst_rate",
            "stamp_delivery",
            "stamp_intraday",
            "dp_charges",
        )
        kwargs = {f: getattr(table, f) for f in fields}
        kwargs["brokerage_delivery_free"] = bool(table.brokerage_delivery_free)  # type: ignore[attr-defined]
        return cls(**kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True)
class CostBreakdown:
    """Itemized charges for one fill (one side)."""

    brokerage: Decimal
    stt: Decimal
    exchange_charges: Decimal
    sebi_charges: Decimal
    gst: Decimal
    stamp_duty: Decimal
    dp_charges: Decimal
    total: Decimal

    def as_dict(self) -> dict:
        """Kwargs for Trade charge fields."""
        return {
            "brokerage": self.brokerage,
            "stt": self.stt,
            "exchange_charges": self.exchange_charges,
            "sebi_charges": self.sebi_charges,
            "gst": self.gst,
            "stamp_duty": self.stamp_duty,
            "dp_charges": self.dp_charges,
            "total_charges": self.total,
        }


def calculate(
    *,
    side: str,
    quantity: int,
    price: Decimal,
    segment: str,
    product: str,
    rates: CostRates | None = None,
    is_exercise: bool = False,
) -> CostBreakdown:
    """
    Itemized charges for one fill leg.

    Args:
        side: "BUY" or "SELL".
        quantity: filled units (lots already expanded to units by the caller).
        price: fill price per unit.
        segment: "EQ" | "FUT" | "OPT".
        product: "CNC" | "MIS" | "NRML".
        rates: schedule override (defaults = Zerodha-style).
        is_exercise: ITM option cash settlement (STT on intrinsic, no brokerage).
    """
    rates = rates or CostRates()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY/SELL, got {side!r}")
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if price <= 0:
        raise ValueError("price must be positive")
    turnover = _q(price * quantity)
    is_delivery = segment == "EQ" and product == "CNC"

    # --- Brokerage ---
    if is_exercise or is_delivery and rates.brokerage_delivery_free:
        brokerage = Decimal("0.0000")
    else:
        brokerage = _q(min(turnover * rates.brokerage_pct, rates.brokerage_flat))

    # --- STT ---
    if is_exercise:
        stt = _q(turnover * rates.stt_exercise)
    elif segment == "EQ" and is_delivery:
        stt = _q(turnover * rates.stt_delivery)
    elif side == "BUY":
        stt = Decimal("0.0000")  # STT on sell side only for intraday/F&O
    elif segment == "EQ":
        stt = _q(turnover * rates.stt_intraday)
    elif segment == "FUT":
        stt = _q(turnover * rates.stt_futures)
    else:
        stt = _q(turnover * rates.stt_options)

    # --- Exchange + SEBI (both sides; options on premium = turnover here) ---
    if segment == "EQ":
        exchange_rate = rates.exchange_equity
    elif segment == "FUT":
        exchange_rate = rates.exchange_futures
    else:
        exchange_rate = rates.exchange_options
    exchange_charges = _q(turnover * exchange_rate)
    sebi_charges = _q(turnover * rates.sebi_rate)

    # --- GST on (brokerage + exchange + SEBI) ---
    gst = _q((brokerage + exchange_charges + sebi_charges) * rates.gst_rate)

    # --- Stamp duty (buy side only; exercises are not market buys) ---
    if side == "BUY" and not is_exercise:
        stamp_rate = rates.stamp_delivery if is_delivery else rates.stamp_intraday
        stamp_duty = _q(turnover * stamp_rate)
    else:
        stamp_duty = Decimal("0.0000")

    # --- DP (delivery sells only, once per order leg) ---
    dp = _q(rates.dp_charges) if side == "SELL" and is_delivery else Decimal("0.0000")

    total = _q(brokerage + stt + exchange_charges + sebi_charges + gst + stamp_duty + dp)
    return CostBreakdown(
        brokerage=brokerage,
        stt=stt,
        exchange_charges=exchange_charges,
        sebi_charges=sebi_charges,
        gst=gst,
        stamp_duty=stamp_duty,
        dp_charges=dp,
        total=total,
    )


def estimate_for_margin(
    *,
    side: str,
    quantity: int,
    price: Decimal,
    segment: str,
    product: str,
    rates: CostRates | None = None,
) -> Decimal:
    """Conservative cost estimate for pre-trade margin checks (one side)."""
    return calculate(
        side=side,
        quantity=quantity,
        price=price,
        segment=segment,
        product=product,
        rates=rates,
    ).total


def active_rates() -> CostRates:
    """
    Active CostTable schedule, or Zerodha-style defaults when never seeded.

    Never raises: engine code must keep trading (with documented defaults)
    rather than crash on an unseeded table. Seed with `load_cost_table`.
    """
    try:
        from .models import CostTable

        table = CostTable.active()
    except Exception:  # noqa: BLE001 - unmigrated DB in some test contexts
        table = None
    if table is None:
        return CostRates()
    return CostRates.from_cost_table(table)
