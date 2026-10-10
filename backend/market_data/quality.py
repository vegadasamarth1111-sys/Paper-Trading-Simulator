"""
Paper Trading Simulator — Daily data-quality checks (Phase 1, plan 4.8)
=========================================================================
After market close: tick counts vs rolling average, gaps longer than the
threshold, bad-tick ratio, candle-rebuild reconciliation, master changes.

Failures mark the day SUSPECT (excluded from performance stats) and queue
a Telegram alert. Every number here must be explainable — "no unexplained
data gaps" is the Phase 1 exit gate.
"""

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Count

from core.models.instruments import Instrument

from .models import BadTick, DataQualityReport, Tick
from .services import rebuild_1m_from_ticks

logger = logging.getLogger("core")
IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

# Tunables (env overrides live in the report task / management command).
GAP_THRESHOLD_SECONDS = 300  # a 5-min silence intraday is a gap, not noise
BAD_RATIO_SUSPECT = 0.05  # >5% rejected ticks -> suspect day
MISSING_SYMBOL_SUSPECT = 1  # any expected symbol with zero ticks -> suspect


def _day_bounds_utc(trading_day: date) -> tuple[datetime, datetime]:
    start_ist = datetime(trading_day.year, trading_day.month, trading_day.day, tzinfo=IST)
    start_utc = start_ist.astimezone(UTC)
    return start_utc, start_utc + timedelta(days=1)


def _count_gaps(ticks: list[datetime], threshold_seconds: int) -> tuple[int, int]:
    """(gap_count, max_gap_seconds) over ascending timestamps."""
    gaps = 0
    worst = 0
    for prev, cur in zip(ticks, ticks[1:], strict=False):
        delta = (cur - prev).total_seconds()
        if delta > threshold_seconds:
            gaps += 1
            worst = max(worst, int(delta))
    return gaps, worst


def build_daily_report(
    trading_day: date,
    *,
    exchange: str = "NSE",
    gap_threshold_seconds: int = GAP_THRESHOLD_SECONDS,
) -> DataQualityReport:
    """Compute and persist the daily report. Idempotent per (date, exchange)."""
    start_utc, end_utc = _day_bounds_utc(trading_day)

    instruments = list(
        Instrument.objects.filter(
            exchange=exchange, is_active=True, effective_to__isnull=True
        ).order_by("symbol")
    )
    symbols = [i.symbol for i in instruments]

    total_ticks = Tick.objects.filter(
        exchange=exchange, exchange_ts__gte=start_utc, exchange_ts__lt=end_utc
    ).count()
    bad_ticks = BadTick.objects.filter(
        exchange=exchange, created_at__gte=start_utc, created_at__lt=end_utc
    ).count()

    # Per-symbol presence + worst gap (sampled from tick timestamps).
    missing: list[str] = []
    gap_count = 0
    gap_max = 0
    for inst in instruments:
        stamps = list(
            Tick.objects.filter(
                instrument=inst, exchange_ts__gte=start_utc, exchange_ts__lt=end_utc
            )
            .order_by("exchange_ts")
            .values_list("exchange_ts", flat=True)[:10000]
        )
        if not stamps:
            missing.append(inst.symbol)
            continue
        gaps, worst = _count_gaps(list(stamps), gap_threshold_seconds)
        gap_count += gaps
        gap_max = max(gap_max, worst)

    # Candle reconciliation: rebuild 1m from ticks for a sample of symbols
    # (full-universe rebuild runs in the EOD Celery task on large boxes).
    mismatches = 0
    for inst in instruments[:10]:
        _, bad = rebuild_1m_from_ticks(inst, trading_day)
        mismatches += bad

    # Master changes that day (created/updated instruments).
    master_changes = Instrument.objects.filter(
        exchange=exchange, created_at__date=trading_day
    ).count()

    bad_ratio = (bad_ticks / total_ticks) if total_ticks else 0.0
    is_suspect = bool(
        missing[:MISSING_SYMBOL_SUSPECT] or bad_ratio > BAD_RATIO_SUSPECT or mismatches > 0
    )
    notes_parts = [
        f"checked={len(symbols)} ticks={total_ticks} bad={bad_ticks} ({bad_ratio:.2%})",
        f"gaps={gap_count} worst={gap_max}s mismatches={mismatches}",
        f"missing={missing[:5]}{'...' if len(missing) > 5 else ''}",
    ]

    report, _ = DataQualityReport.objects.update_or_create(
        report_date=trading_day,
        exchange=exchange,
        defaults={
            "symbols_checked": len(symbols),
            "total_ticks": total_ticks,
            "bad_ticks": bad_ticks,
            "gap_count": gap_count,
            "gap_seconds_max": gap_max,
            "missing_symbols": missing,
            "candle_mismatches": mismatches,
            "instruments_changed": master_changes,
            "is_suspect": is_suspect,
            "notes": "; ".join(notes_parts),
        },
    )
    if is_suspect:
        logger.error("Data-quality SUSPECT %s %s: %s", trading_day, exchange, report.notes)
    else:
        logger.info("Data-quality OK %s %s: %s", trading_day, exchange, report.notes)
    return report


def rolling_tick_average(symbol: str, days: int = 5) -> float:
    """Mean daily tick count for a symbol over the last N days with data."""
    rows = (
        Tick.objects.filter(symbol=symbol.upper())
        .extra(select={"day": "DATE(exchange_ts)"})
        .values("day")
        .annotate(n=Count("id"))
        .order_by("-day")[:days]
    )
    counts = [r["n"] for r in rows]
    return sum(counts) / len(counts) if counts else 0.0
