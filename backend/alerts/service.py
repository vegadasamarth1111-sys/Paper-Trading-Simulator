"""
Paper Trading Simulator — Alert events (Phase 5)
==================================================
One formatter per plan-mandated alert (feed down, stale data, risk hit,
reconciliation mismatch, service crash, kill switch, EOD summary).
Callers pass domain objects; this module formats and sends. Nothing
here touches money or trading state.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .telegram import configured, send_message

logger = logging.getLogger("core")
UTC = ZoneInfo("UTC")


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def notify_kill_switch(account, reason: str, summary: dict) -> bool:
    """Someone halted everything — shout immediately."""
    if not configured():
        return False
    text = (
        f"🛑 *KILL SWITCH*\n"
        f"Account stopped: `{str(account.id)[:8]}`\n"
        f"Reason: {reason}\n"
        f"Cancelled: {len(summary.get('cancelled', []))} | "
        f"Squared: {len(summary.get('squared', []))}\n"
        f"{_stamp()}"
    )
    return send_message(text)


def notify_reconciliation(account, mismatches: list) -> bool:
    """Money does not reconcile — critical, needs eyes now."""
    if not configured() or not mismatches:
        return False
    lines = "\n".join(f"• {m.area} {m.key}: {m.stored} vs {m.rebuilt}" for m in mismatches[:10])
    return send_message(
        f"🚨 *RECONCILIATION MISMATCH*\nAccount `{str(account.id)[:8]}`\n{lines}\n{_stamp()}"
    )


def notify_data_quality(report) -> bool:
    """A trading day was marked suspect (excluded from stats)."""
    if not configured():
        return False
    verdict = "SUSPECT — excluded from stats" if report.is_suspect else "OK"
    return send_message(
        f"📉 *Data quality {report.report_date} {report.exchange}: {verdict}*\n"
        f"Ticks: {report.total_ticks} | Bad: {report.bad_ticks} | "
        f"Gaps: {report.gap_count} | Mismatches: {report.candle_mismatches}\n"
        f"{report.notes[:500]}"
    )


def notify_risk_block(account, rule: str, message: str) -> bool:
    """A risk rule fired on a live order (rate-limited upstream by volume)."""
    if not configured():
        return False
    return send_message(
        f"⚠️ *Risk block {rule}*\nAccount `{str(account.id)[:8]}`\n{message[:500]}\n{_stamp()}"
    )


def notify_eod_summary(account, snapshot) -> bool:
    """End-of-day numbers for the nightly glance."""
    if not configured():
        return False
    return send_message(
        f"📊 *EOD {snapshot.snapshot_date}*\n"
        f"Equity: ₹{snapshot.total_equity:,.2f} | Day PnL: ₹{snapshot.day_pnl:,.2f}\n"
        f"Open: {snapshot.open_positions_count} | Trades: {snapshot.trades_count} | "
        f"Charges: ₹{snapshot.total_charges_today:,.2f}"
    )
