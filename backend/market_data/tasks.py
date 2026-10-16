"""
Paper Trading Simulator — Market-data Celery tasks (Phase 1)
==============================================================
Scheduled jobs (Celery Beat): master sync before open, polling fallback,
EOD candle rebuild + data-quality report after close.

All tasks are idempotent: re-running them updates rows instead of
duplicating, so a Beat retry never corrupts the store.
"""

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from celery import shared_task

logger = logging.getLogger("core")
UTC = ZoneInfo("UTC")
IST = ZoneInfo("Asia/Kolkata")


@shared_task(name="market_data.sync_master")
def sync_master_task(provider: str = "yahoo") -> dict:
    """Daily instrument-master sync. Returns the loader summary."""
    from instruments.loader import InstrumentLoader
    from market_data.providers.angel import AngelOneProvider
    from market_data.providers.yahoo import YahooFinanceProvider

    impl = YahooFinanceProvider() if provider == "yahoo" else AngelOneProvider()
    summary = InstrumentLoader(provider=impl).sync_instruments()  # type: ignore[arg-type]
    logger.info("Master sync (%s): %s", provider, summary)
    return dict(summary)


@shared_task(name="market_data.backfill_history")
def backfill_history_task(symbol: str, interval: str = "1d", days: int = 30) -> dict:
    """Backfill OHLC history for one symbol (idempotent)."""
    from market_data.services import get_provider, store_history

    provider = get_provider()
    bars = provider.fetch_history(symbol, interval=interval, period_days=days)  # type: ignore[attr-defined]
    return store_history(symbol, bars, source=getattr(provider, "name", "yahoo"))


@shared_task(name="market_data.poll_once")
def poll_once_task(symbols: list[str] | None = None) -> dict:
    """Single polling cycle (Beat calls this every minute in market hours)."""
    from decouple import config

    from market_data.ingestion import PollingIngestor
    from market_data.services import get_provider
    from market_data.universe import get_seed_symbols

    provider = get_provider()
    watchlist = symbols or get_seed_symbols()[:50]
    ingestor = PollingIngestor(
        provider,  # type: ignore[arg-type]
        watchlist,
        poll_interval_seconds=config("POLL_INTERVAL_SECONDS", default=60, cast=int),
        stale_seconds=config("STALE_TICK_SECONDS", default=900, cast=int),
    )
    return ingestor.poll_once()


@shared_task(name="market_data.data_quality_report")
def data_quality_report_task(report_date: str | None = None) -> dict:
    """Build the daily data-quality report (plan section 4.8)."""
    from market_data.quality import build_daily_report

    day = date.fromisoformat(report_date) if report_date else datetime.now(IST).date()
    report = build_daily_report(day)
    logger.info("Data-quality report %s: suspect=%s", report, report.is_suspect)
    if report.is_suspect:
        try:
            from alerts.service import notify_data_quality

            notify_data_quality(report)
        except Exception:  # noqa: BLE001 - alerts never break the job
            logger.exception("Data-quality alert failed")
    return {"report_id": report.id, "is_suspect": report.is_suspect}
