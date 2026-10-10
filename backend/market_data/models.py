"""
Paper Trading Simulator — Market Data Models (Phase 1: Data Layer)
====================================================================
Append-only raw tick store, aggregated candles, rejected ticks, and daily
data-quality reports.

Design notes (production plan, section 4):
- ticks_raw is append-only and partitioned by day in production MySQL.
  Django does not do the partitioning; the model carries the indexes that
  make the partitions prunable (instrument, exchange_ts) and a management
  command archives rows older than 30 days to Parquet.
- candles carry a unique key (instrument, timeframe, open_ts).
- Raw data is NEVER overwritten. Splits/bonuses live in CorporateAction
  with adjustment factors applied at read time.
- All timestamps are stored in UTC. IST conversion happens only in
  market-hour logic (core.clock) and display.
- Money/price fields are Decimal, never float.
"""

from decimal import Decimal

from django.db import models

from core.enums import CandleTimeframe, Exchange, TickRejectReason


class DataSource(models.TextChoices):
    """Where a tick/candle came from. Provenance matters for replay."""

    YAHOO = "yahoo", "Yahoo Finance (free, keyless)"
    ANGEL = "angel", "Angel One SmartAPI"
    NSE = "nse", "NSE official archives"
    REPLAY = "replay", "Replayed from raw store"
    SYNTHETIC = "synthetic", "Synthetic (tests / backfill placeholders)"


class Tick(models.Model):
    """
    One normalized market-data tick (internal schema, plan section 4.3).

    Fields map 1:1 to the plan's internal tick schema:
    symbol_id, exchange_ts, received_ts, ltp, last_qty, volume_traded, oi,
    best_bid, best_bid_qty, best_ask, best_ask_qty, sequence_no.
    """

    id = models.BigAutoField(primary_key=True)
    instrument = models.ForeignKey(
        "core.Instrument",
        on_delete=models.PROTECT,
        related_name="ticks",
        help_text="Instrument this tick belongs to",
    )
    # Denormalized for partition pruning without a JOIN.
    symbol = models.CharField(max_length=50, db_index=True)
    exchange = models.CharField(max_length=3, choices=Exchange.choices, db_index=True)

    exchange_ts = models.DateTimeField(
        db_index=True,
        help_text="Exchange timestamp (UTC). Measures true market time.",
    )
    received_ts = models.DateTimeField(
        db_index=True,
        help_text="When we received the tick (UTC). Gap vs exchange_ts = latency.",
    )

    ltp = models.DecimalField(max_digits=12, decimal_places=4)
    last_qty = models.PositiveIntegerField(default=0)
    volume_traded = models.BigIntegerField(
        default=0,
        help_text="Cumulative day volume. Must be non-decreasing within the day.",
    )
    oi = models.BigIntegerField(null=True, blank=True)

    best_bid = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    best_bid_qty = models.PositiveIntegerField(null=True, blank=True)
    best_ask = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    best_ask_qty = models.PositiveIntegerField(null=True, blank=True)
    sequence_no = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Provider sequence number for duplicate/out-of-order detection",
    )

    source = models.CharField(max_length=10, choices=DataSource.choices, default=DataSource.YAHOO)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticks_raw"
        ordering = ["exchange_ts"]
        indexes = [
            models.Index(fields=["instrument", "exchange_ts"]),
            models.Index(fields=["symbol", "exchange_ts"]),
            models.Index(fields=["exchange_ts"]),
            models.Index(fields=["source", "exchange_ts"]),
        ]

    def __str__(self) -> str:
        return f"{self.symbol} @ {self.ltp} ({self.exchange_ts.isoformat()})"

    @property
    def latency_seconds(self) -> float:
        """Seconds between exchange time and receipt. High values mean stale data."""
        return (self.received_ts - self.exchange_ts).total_seconds()


class Candle(models.Model):
    """
    Aggregated OHLCV candle built from ticks (plan section 4.5).

    - 1m candles are built from ticks, then aggregated to 5m/15m/1h/1d.
    - Intraday boundaries align to 09:15 IST (market open).
    - A candle is complete only when the next interval's first tick arrives
      or the interval time passes; strategies must only see complete candles.
    - Empty intervals are stored explicitly (is_empty=True, volume=0) rather
      than skipped, so gaps are visible instead of silent.
    """

    id = models.BigAutoField(primary_key=True)
    instrument = models.ForeignKey(
        "core.Instrument",
        on_delete=models.PROTECT,
        related_name="candles",
    )
    symbol = models.CharField(max_length=50, db_index=True)
    exchange = models.CharField(max_length=3, choices=Exchange.choices, db_index=True)
    timeframe = models.CharField(max_length=3, choices=CandleTimeframe.choices, db_index=True)

    open_ts = models.DateTimeField(
        db_index=True,
        help_text="Interval open timestamp (UTC). Aligned to 09:15 IST for intraday.",
    )
    open_price = models.DecimalField(max_digits=12, decimal_places=4)
    high_price = models.DecimalField(max_digits=12, decimal_places=4)
    low_price = models.DecimalField(max_digits=12, decimal_places=4)
    close_price = models.DecimalField(max_digits=12, decimal_places=4)
    volume = models.BigIntegerField(
        default=0,
        help_text="Traded volume during the interval (0 for empty intervals).",
    )
    trades = models.PositiveIntegerField(
        default=0,
        help_text="Number of ticks aggregated into this candle.",
    )
    is_complete = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True only when the interval has closed. Strategies use closed candles.",
    )
    is_empty = models.BooleanField(
        default=False,
        help_text="True when no trades occurred in the interval (explicit gap marker).",
    )
    source = models.CharField(max_length=10, choices=DataSource.choices, default=DataSource.YAHOO)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "candles"
        ordering = ["open_ts"]
        indexes = [
            models.Index(fields=["instrument", "timeframe", "open_ts"]),
            models.Index(fields=["symbol", "timeframe", "open_ts"]),
            models.Index(fields=["timeframe", "open_ts"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["instrument", "timeframe", "open_ts"],
                name="uq_candle_instrument_tf_open",
            ),
        ]

    def __str__(self) -> str:
        flag = " (empty)" if self.is_empty else ""
        state = "closed" if self.is_complete else "open"
        return f"{self.symbol} {self.timeframe} {self.open_ts.isoformat()} [{state}]{flag}"

    def range_pct(self) -> Decimal:
        """High-low range as a fraction of open. Zero-safe (empty candles)."""
        if not self.open_price:
            return Decimal("0")
        return (self.high_price - self.low_price) / self.open_price


class BadTick(models.Model):
    """
    Rejected ticks with a reason code (plan section 4.4).

    Rejected ticks are never silently dropped: they land here with the rule
    that rejected them, feeding the daily data-quality report.
    """

    id = models.BigAutoField(primary_key=True)
    instrument = models.ForeignKey(
        "core.Instrument",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bad_ticks",
    )
    symbol = models.CharField(max_length=50, db_index=True)
    exchange = models.CharField(max_length=3, choices=Exchange.choices, db_index=True)
    exchange_ts = models.DateTimeField(null=True, blank=True)
    received_ts = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=15, choices=TickRejectReason.choices, db_index=True)
    details = models.TextField(blank=True, default="")
    raw_payload = models.JSONField(default=dict)
    source = models.CharField(max_length=10, choices=DataSource.choices, default=DataSource.YAHOO)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bad_ticks"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["symbol", "reason"]),
            models.Index(fields=["reason", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.symbol} rejected: {self.reason}"


class DataQualityReport(models.Model):
    """
    Daily per-exchange data-quality summary (plan section 4.8).

    Produced after market close by a Celery beat task. If checks fail, the
    day is marked suspect so its results are excluded from performance stats,
    and a Telegram alert is queued.
    """

    id = models.BigAutoField(primary_key=True)
    report_date = models.DateField(db_index=True)
    exchange = models.CharField(max_length=3, choices=Exchange.choices)

    symbols_checked = models.PositiveIntegerField(default=0)
    total_ticks = models.PositiveIntegerField(default=0)
    bad_ticks = models.PositiveIntegerField(default=0)
    gap_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of gaps longer than the threshold.",
    )
    gap_seconds_max = models.PositiveIntegerField(default=0)
    missing_symbols = models.JSONField(
        default=list,
        help_text="Symbols with zero ticks that were expected to trade.",
    )
    candle_mismatches = models.PositiveIntegerField(
        default=0,
        help_text="1m candles whose EOD rebuild differed from the stored row.",
    )
    instruments_changed = models.PositiveIntegerField(
        default=0,
        help_text="Instrument-master rows created/updated/deactivated that day.",
    )
    is_suspect = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True when the day must be excluded from performance stats.",
    )
    notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "data_quality_reports"
        ordering = ["-report_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["report_date", "exchange"],
                name="uq_dqreport_date_exchange",
            ),
        ]

    def __str__(self) -> str:
        verdict = "SUSPECT" if self.is_suspect else "OK"
        return f"{self.report_date} {self.exchange} [{verdict}]"

    @property
    def bad_tick_ratio(self) -> float:
        """Fraction of ticks rejected. Zero-safe."""
        if not self.total_ticks:
            return 0.0
        return self.bad_ticks / self.total_ticks
