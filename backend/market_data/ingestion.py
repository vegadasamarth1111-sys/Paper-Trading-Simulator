"""
Paper Trading Simulator — Polling ingestor (Phase 1, free tier)
=================================================================
Free sources expose no tick websocket without broker login, so Phase 1
ingests by polling LTP every POLL_INTERVAL_SECONDS (default 60s) during
market hours and treating each poll as a tick. The pipeline order is
deliberate (plan section 4.2):

1. Fetch raw quotes (provider).
2. Write the RAW response first (append-only Tick row) — replay fidelity.
3. Normalize -> validate -> BadTick on reject.
4. Update Redis latest price + in-memory previous-tick cache.
5. Heartbeat: symbols silent longer than the threshold raise an alert
   and trigger a provider retry with exponential backoff.

When Angel One keys appear, only the provider changes; this loop is
untouched. A future websocket ingestor will share steps 2-5.
"""

import logging
import time
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from core.clock import Clock, ClockMode
from core.models.instruments import Instrument

from .schema import NormalizedTick
from .validation import ValidationThresholds, validate_tick

logger = logging.getLogger("core")
UTC = ZoneInfo("UTC")

# Yahoo/Angel tolerate larger polling watchlists; broker websockets fragment
# subscriptions when the dedicated feed arrives (see AngelOneProvider).
# The seed universe (53 symbols) must always fit — a smaller cap would
# crash the default poll_prices run (regression-tested in test_load.py).
MAX_SUBSCRIPTIONS = 100


class PollingIngestor:
    """Polling ingestion loop. One instance per process (see poll_prices)."""

    def __init__(
        self,
        provider: object,
        symbols: list[str],
        *,
        poll_interval_seconds: int = 60,
        stale_seconds: int = 900,
        clock: Clock | None = None,
        calendar: object | None = None,
    ) -> None:
        if len(symbols) > MAX_SUBSCRIPTIONS:
            raise ValueError(
                f"Watchlist holds {len(symbols)} symbols but the free tier allows "
                f"{MAX_SUBSCRIPTIONS}. Trim the universe or raise the limit deliberately."
            )
        self.provider = provider
        self.symbols = list(dict.fromkeys(symbols))  # de-dup, keep order
        self.poll_interval_seconds = poll_interval_seconds
        self.stale_seconds = stale_seconds
        self.clock = clock or Clock(mode=ClockMode.LIVE)
        self.calendar = calendar
        # Previous accepted tick per symbol for validator context.
        self._prev: dict[str, dict] = {}
        # Last successful poll per symbol for heartbeat monitoring.
        self._last_seen: dict[str, datetime] = {}
        self._backoff_seconds = 0

    # ------------------------------------------------------------------
    # Subscription manager (plan 4.2: subscribe only to the watchlist)
    # ------------------------------------------------------------------
    def subscribe(self, symbol: str) -> None:
        """Add a symbol to the watchlist (respects the free-tier cap)."""
        if symbol in self.symbols:
            return
        if len(self.symbols) >= MAX_SUBSCRIPTIONS:
            raise ValueError(f"Subscription limit reached ({MAX_SUBSCRIPTIONS})")
        self.symbols.append(symbol)

    def unsubscribe(self, symbol: str) -> None:
        """Remove a symbol; its cached state is dropped with it."""
        if symbol in self.symbols:
            self.symbols.remove(symbol)
        self._prev.pop(symbol, None)
        self._last_seen.pop(symbol, None)

    # ------------------------------------------------------------------
    # One poll cycle (used by the loop AND by tests/Celery — same code path)
    # ------------------------------------------------------------------
    def poll_once(self) -> dict:
        """
        Fetch quotes once and persist ticks. Returns a summary dict:
        {"polled": n, "accepted": n, "rejected": n, "flagged": n, "errors": []}.
        Never raises on provider errors: they become backoff + error entries.
        """
        from django.core.cache import cache

        from core.models.instruments import Instrument

        from .models import BadTick, Tick

        summary: dict = {"polled": 0, "accepted": 0, "rejected": 0, "flagged": 0, "errors": []}
        now_utc = self.clock.now()
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=UTC)

        try:
            quotes = self.provider.fetch_ltp(self.symbols)  # type: ignore[attr-defined]
        except Exception as exc:
            self._backoff_seconds = (
                min((self._backoff_seconds or 5) * 2, 300) if self._backoff_seconds else 5
            )
            summary["errors"].append(f"provider fetch failed: {exc}")
            logger.error("Polling fetch failed (backoff %ss): %s", self._backoff_seconds, exc)
            return summary
        self._backoff_seconds = 0

        calendar = self.calendar or self.clock.calendar
        thresholds = ValidationThresholds(staleness_seconds=self.stale_seconds)

        for symbol in self.symbols:
            quote = quotes.get(symbol)
            if not quote:
                summary["errors"].append(f"no quote for {symbol}")
                continue
            summary["polled"] += 1
            try:
                tick = NormalizedTick(
                    symbol=symbol,
                    exchange="NSE",
                    exchange_ts=quote["ts"],
                    received_ts=now_utc,
                    ltp=Decimal(str(quote["ltp"])),
                    last_qty=int(quote.get("qty", 0)),
                    volume_traded=int(quote.get("volume", 0)),
                    source=getattr(self.provider, "name", "yahoo"),
                )
            except Exception as exc:
                summary["rejected"] += 1
                BadTick.objects.create(
                    symbol=symbol,
                    exchange="NSE",
                    reason="NEG_PRICE",
                    details=f"schema reject: {exc}",
                    raw_payload={"quote": str(quote)},
                    source=getattr(self.provider, "name", "yahoo"),
                )
                continue

            instrument = Instrument.objects.filter(
                symbol=symbol, exchange="NSE", is_active=True, effective_to__isnull=True
            ).first()
            prev = self._prev.get(symbol)
            if prev is None and instrument is not None:
                last = (
                    Tick.objects.filter(instrument=instrument)
                    .order_by("-exchange_ts")
                    .values("ltp", "volume_traded", "sequence_no", "exchange_ts")
                    .first()
                )
                if last:
                    prev = {
                        "ltp": last["ltp"],
                        "volume": last["volume_traded"],
                        "sequence_no": last["sequence_no"],
                        "exchange_ts": last["exchange_ts"],
                    }

            result = validate_tick(
                tick,
                upper_circuit=getattr(instrument, "upper_circuit", None),
                lower_circuit=getattr(instrument, "lower_circuit", None),
                prev_ltp=prev["ltp"] if prev else None,
                prev_volume=prev["volume"] if prev else None,
                prev_sequence_no=prev["sequence_no"] if prev else None,
                prev_exchange_ts=prev["exchange_ts"] if prev else None,
                calendar=calendar,
                now_utc=now_utc,
                thresholds=thresholds,
            )
            if not result.accepted:
                summary["rejected"] += 1
                BadTick.objects.create(
                    instrument=instrument,
                    symbol=symbol,
                    exchange="NSE",
                    exchange_ts=tick.exchange_ts,
                    received_ts=tick.received_ts,
                    reason=result.reason or "NEG_PRICE",
                    details=result.details,
                    raw_payload=tick.model_dump(mode="json"),
                    source=tick.source,
                )
                logger.warning("Tick rejected %s: %s", symbol, result.details)
                continue

            row = (
                Tick.objects.create(instrument=instrument, **tick.to_tick_kwargs())
                if instrument
                else None
            )
            if row is None:
                # No instrument row yet (master not synced): keep the tick
                # visible as a bad tick instead of dropping it silently.
                summary["rejected"] += 1
                BadTick.objects.create(
                    symbol=symbol,
                    exchange="NSE",
                    exchange_ts=tick.exchange_ts,
                    received_ts=tick.received_ts,
                    reason="OUTSIDE_MKT",
                    details="no active instrument row; run sync_master first",
                    raw_payload=tick.model_dump(mode="json"),
                    source=tick.source,
                )
                continue

            summary["accepted"] += 1
            if result.flag_only:
                summary["flagged"] += 1
                logger.warning("Tick flagged %s: %s", symbol, result.details)
            prev_volume = prev["volume"] if prev else None
            assert instrument is not None  # narrowed: None path continued above
            self._prev[symbol] = {
                "ltp": tick.ltp,
                "volume": tick.volume_traded,
                "sequence_no": tick.sequence_no,
                "exchange_ts": tick.exchange_ts,
            }
            self._last_seen[symbol] = now_utc
            self._upsert_1m_candle(instrument, tick, prev_volume)
            try:
                cache.set(
                    f"md:latest:{symbol}",
                    {"ltp": str(tick.ltp), "ts": tick.exchange_ts.isoformat()},
                    timeout=self.stale_seconds,
                )
            except Exception as exc:  # noqa: BLE001 - cache must never break ingestion
                logger.warning("Latest-price cache write failed for %s: %s", symbol, exc)

        return summary

    # ------------------------------------------------------------------
    # Incremental 1m candles (plan 4.5: ticks -> 1m -> higher timeframes)
    # ------------------------------------------------------------------
    @staticmethod
    def _upsert_1m_candle(
        instrument: Instrument, tick: NormalizedTick, prev_volume: int | None
    ) -> None:
        """
        Fold one accepted tick into its 1-minute candle row.

        The EOD rebuild replays ticks_raw into 1m candles and diffs them
        against these rows — if polling never maintained them, every day
        would falsely report mismatches. Earlier minutes are marked complete
        as soon as a tick from a later minute arrives (closed-candle rule).
        """
        from .candles import floor_open_ts
        from .models import Candle

        minute_open = floor_open_ts(tick.exchange_ts, "1m")
        volume_delta = max(0, tick.volume_traded - prev_volume) if prev_volume is not None else 0
        candle, created = Candle.objects.get_or_create(
            instrument=instrument,  # type: ignore[arg-type]
            timeframe="1m",
            open_ts=minute_open,
            defaults={
                "symbol": tick.symbol,
                "exchange": tick.exchange,
                "open_price": tick.ltp,
                "high_price": tick.ltp,
                "low_price": tick.ltp,
                "close_price": tick.ltp,
                "volume": volume_delta,
                "trades": 1,
                "is_complete": False,
                "source": tick.source,
            },
        )
        if not created:
            candle.high_price = max(candle.high_price, tick.ltp)
            candle.low_price = min(candle.low_price, tick.ltp)
            candle.close_price = tick.ltp
            candle.volume += volume_delta
            candle.trades += 1
            candle.save(
                update_fields=[
                    "high_price",
                    "low_price",
                    "close_price",
                    "volume",
                    "trades",
                    "updated_at",
                ]
            )
        # Any earlier open minute is now closed: its interval has passed.
        Candle.objects.filter(
            instrument=instrument,
            timeframe="1m",
            open_ts__lt=minute_open,
            is_complete=False,
        ).update(is_complete=True)

    # ------------------------------------------------------------------
    # Heartbeat + loop
    # ------------------------------------------------------------------
    def silent_symbols(self, now_utc: datetime | None = None) -> list[str]:
        """Symbols with no accepted tick for longer than the stale threshold."""
        now = now_utc or self.clock.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        silent = []
        for symbol in self.symbols:
            last = self._last_seen.get(symbol)
            if last is None:
                continue  # never seen yet; not silence, just startup
            if (now - last).total_seconds() > self.stale_seconds:
                silent.append(symbol)
        return silent

    def run_forever(self) -> None:
        """Blocking poll loop for the poll_prices management command."""
        logger.info(
            "Polling ingestor started: %d symbols every %ss",
            len(self.symbols),
            self.poll_interval_seconds,
        )
        while True:
            summary = self.poll_once()
            silent = self.silent_symbols()
            if silent:
                logger.error(
                    "Heartbeat: no ticks for %s beyond %ss — provider retry scheduled",
                    silent,
                    self.stale_seconds,
                )
            logger.info(
                "Poll cycle: accepted=%d rejected=%d flagged=%d errors=%d",
                summary["accepted"],
                summary["rejected"],
                summary["flagged"],
                len(summary["errors"]),
            )
            sleep_for = self.poll_interval_seconds + self._backoff_seconds
            time.sleep(sleep_for)
