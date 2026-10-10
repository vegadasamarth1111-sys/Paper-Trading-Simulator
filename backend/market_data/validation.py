"""
Paper Trading Simulator — Tick validation (Phase 1, plan section 4.4)
=======================================================================
Every tick is validated BEFORE it can move prices, candles, or fills.
Rules reject or flag, never silently accept:

- price positive (schema guarantees gt=0; this is defense in depth)
- price within the day's circuit limits            -> reject
- timestamp not in the future                     -> reject
- timestamp not older than the staleness limit    -> reject
- timestamp inside market hours (09:15-15:30 IST,
  pre-open 09:00-09:15 accepted as its own session) -> reject otherwise
- volume non-decreasing within the day            -> reject
- duplicate / out-of-order via sequence_no        -> reject
- bid <= ask when both exist                      -> reject
- extreme move vs previous tick                   -> FLAG only (accepted)

Rejected ticks go to bad_ticks with a reason code; flagged ticks are
accepted but the warning is returned so the caller can log/meter it.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from core.enums import TickRejectReason

from .schema import NormalizedTick

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class ValidationThresholds:
    """Tunable knobs. Lives in code with safe defaults; env overrides in services."""

    future_tolerance_seconds: int = 60
    staleness_seconds: int = 900  # 15 min default for 1-min polling
    extreme_move_pct: Decimal = Decimal("0.10")  # flag moves > 10% tick-to-tick


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating one tick."""

    accepted: bool
    reason: str | None = None
    details: str = ""
    flag_only: bool = False

    @classmethod
    def ok(cls) -> ValidationResult:
        return cls(accepted=True)

    @classmethod
    def flagged(cls, reason: str, details: str) -> ValidationResult:
        return cls(accepted=True, reason=reason, details=details, flag_only=True)

    @classmethod
    def rejected(cls, reason: str, details: str) -> ValidationResult:
        return cls(accepted=False, reason=reason, details=details)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def validate_tick(
    tick: NormalizedTick,
    *,
    upper_circuit: Decimal | None,
    lower_circuit: Decimal | None,
    prev_ltp: Decimal | None,
    prev_volume: int | None,
    prev_sequence_no: int | None,
    prev_exchange_ts: datetime | None,
    calendar: Any,
    now_utc: datetime,
    thresholds: ValidationThresholds | None = None,
) -> ValidationResult:
    """
    Validate one normalized tick against plan section 4.4.

    Args:
        tick: the normalized tick under test.
        upper_circuit / lower_circuit: instrument limits (None = unknown, skip).
        prev_ltp / prev_volume / prev_sequence_no / prev_exchange_ts: the most
            recent ACCEPTED tick for the same instrument (None on first tick).
        calendar: core.clock.TradingCalendar (duck-typed for testability).
        now_utc: current time from Clock (never datetime.now() here).
        thresholds: tunable limits.
    """
    thresholds = thresholds or ValidationThresholds()
    now_utc = _as_utc(now_utc)
    exchange_ts = _as_utc(tick.exchange_ts)

    # 1. Price positive (schema already enforces gt=0; keep as a hard guard
    #    because validators must not trust upstream schemas).
    if tick.ltp <= 0:
        return ValidationResult.rejected(
            TickRejectReason.NEGATIVE_PRICE, f"ltp={tick.ltp} is not positive"
        )

    # 2. Circuit limits.
    if upper_circuit is not None and tick.ltp > upper_circuit:
        return ValidationResult.rejected(
            TickRejectReason.CIRCUIT_BREACH,
            f"ltp={tick.ltp} above upper circuit {upper_circuit}",
        )
    if lower_circuit is not None and tick.ltp < lower_circuit:
        return ValidationResult.rejected(
            TickRejectReason.CIRCUIT_BREACH,
            f"ltp={tick.ltp} below lower circuit {lower_circuit}",
        )

    # 3. Timestamp in the future (with a small tolerance for clock skew).
    if (exchange_ts - now_utc).total_seconds() > thresholds.future_tolerance_seconds:
        return ValidationResult.rejected(
            TickRejectReason.FUTURE_TIMESTAMP,
            f"exchange_ts={exchange_ts.isoformat()} is ahead of now={now_utc.isoformat()}",
        )

    # 4. Stale timestamp.
    if (now_utc - exchange_ts).total_seconds() > thresholds.staleness_seconds:
        return ValidationResult.rejected(
            TickRejectReason.STALE_TIMESTAMP,
            f"exchange_ts={exchange_ts.isoformat()} is older than {thresholds.staleness_seconds}s",
        )

    # 5. Market hours. Pre-open (09:00-09:15 IST) is accepted as its own
    #    session; anything outside [pre-open, regular] is rejected.
    if not calendar.is_market_open(exchange_ts) and not calendar.is_pre_open(exchange_ts):
        return ValidationResult.rejected(
            TickRejectReason.OUTSIDE_MARKET_HOURS,
            f"exchange_ts={exchange_ts.isoformat()} is outside 09:00-15:30 IST",
        )

    # 6. Volume must not decrease within the day.
    if prev_volume is not None and tick.volume_traded < prev_volume:
        return ValidationResult.rejected(
            TickRejectReason.VOLUME_DECREASE,
            f"volume {tick.volume_traded} < previous {prev_volume}",
        )

    # 7. Duplicate / out-of-order detection.
    if (
        prev_sequence_no is not None
        and tick.sequence_no is not None
        and tick.sequence_no == prev_sequence_no
    ):
        return ValidationResult.rejected(
            TickRejectReason.DUPLICATE_TICK,
            f"sequence_no={tick.sequence_no} already seen",
        )
    if prev_exchange_ts is not None:
        prev_ts = _as_utc(prev_exchange_ts)
        if exchange_ts < prev_ts:
            return ValidationResult.rejected(
                TickRejectReason.OUT_OF_ORDER,
                f"exchange_ts={exchange_ts.isoformat()} before previous {prev_ts.isoformat()}",
            )
        if exchange_ts == prev_ts and tick.sequence_no is None:
            # Same timestamp with no sequence number to disambiguate:
            # treat as a duplicate rather than risking a double-count.
            return ValidationResult.rejected(
                TickRejectReason.DUPLICATE_TICK,
                f"exchange_ts={exchange_ts.isoformat()} already seen (no sequence_no)",
            )

    # 8. Bid must not exceed ask.
    if tick.best_bid is not None and tick.best_ask is not None and tick.best_bid > tick.best_ask:
        return ValidationResult.rejected(
            TickRejectReason.INVALID_SPREAD,
            f"bid={tick.best_bid} > ask={tick.best_ask}",
        )

    # 9. Extreme move: FLAG, do not reject (a real circuit move must survive).
    if prev_ltp is not None and prev_ltp > 0:
        move = abs(tick.ltp - prev_ltp) / prev_ltp
        if move > thresholds.extreme_move_pct:
            return ValidationResult.flagged(
                TickRejectReason.EXTREME_MOVE,
                f"ltp moved {move:.2%} from {prev_ltp} to {tick.ltp}",
            )

    return ValidationResult.ok()
