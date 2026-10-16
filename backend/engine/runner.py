"""
Paper Trading Simulator — Backtest & replay runner (Phase 4, plan section 8)
===============================================================================
The SAME engine runs in three modes (plan mandate):

- Backtest: closed candles from the store, simulated clock, as fast as
  possible. Strategies see candles only (no ticks) — pure signal research.
- Replay: recorded raw ticks played in order; closed 1m candles are
  derived tick-by-tick and delivered alongside. Best for debugging live
  behavior, because the strategy sees exactly what live code would see.
- Live paper: real-time data and real clock (Phase 5 wires the loop;
  the runner already speaks to it through the broker + risk).

Determinism contract (the Phase 4 exit gate): the same tick/candle input
drives the same signals, fills, and PnL. No wall-clock reads, no random
choices, no dict-ordering dependence inside the loop. Timestamps sort by
(exchange_ts, id); symbols break ties alphabetically. UUIDs differ
between runs by design — comparisons use financial fields only.

Signal flow per signal: validate -> confidence gate -> persist Signal row
-> size from stop distance -> skip with reason when unsizable ->
place (bracket when target+stop, else market) with signal_id + stop ->
record order link / skip reason -> process resting stops against the
current quote so bracket legs trigger on later bars.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from core.clock import Clock
from core.models.accounts import Account
from core.models.instruments import Instrument
from core.models.strategies import Strategy, StrategyRun

from .base import BaseStrategy, CandleView, TickView

logger = logging.getLogger("core")
UTC = ZoneInfo("UTC")


@dataclass
class RunSpec:
    """Everything a run needs, declared up front for reproducibility."""

    strategy_name: str
    params: dict
    symbols: list[str]
    timeframe: str = "1m"
    mode: str = "backtest"  # backtest | replay
    risk_pct: Decimal = Decimal("0.01")
    data_version: str = ""
    account_id: str = ""


@dataclass
class RunResult:
    """Financial fingerprint of a run (UUID-free, comparable across runs)."""

    run_id: str
    orders_placed: int
    orders_filled: int
    signals: int
    signals_skipped: int
    trades: int
    realized_pnl: Decimal = Decimal("0.0000")
    total_charges: Decimal = Decimal("0.0000")
    final_balance: Decimal = Decimal("0.0000")
    fills: list[tuple] = field(default_factory=list)  # (symbol, side, qty, price)


class EventRunner:
    """
    Drives one strategy over historical bars against the paper broker.

    Owns: the simulated clock, candle/tick iteration, signal routing, and
    the StrategyRun record. Never owns pricing logic (broker), risk limits
    (risk engine), or decisions (strategy) — composition, not duplication.
    """

    def __init__(
        self,
        *,
        spec: RunSpec,
        strategy: BaseStrategy,
        account: Account,
        strategy_row: Strategy,
        broker,
        clock: Clock,
        price_of: Callable[[str], Decimal | None],
        live_feed: dict,
    ) -> None:
        self.spec = spec
        self.strategy = strategy
        self.account = account
        self.strategy_row = strategy_row
        self.broker = broker
        self.clock = clock
        self._price_of = price_of
        # Mutable quote board shared with the broker's price source: every
        # bar/tick updates it BEFORE signals route, so fills always price
        # off the simulated present — never the future, never stale cache.
        self._live_feed = live_feed
        self._quote_ts: dict[str, datetime] = {}
        self._seq = 0
        self._realized_start = Decimal("0.0000")

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------
    def run_backtest(self, candles_by_symbol: dict[str, list[CandleView]]) -> RunResult:
        """Walk closed candles in time order (merged across symbols)."""
        run = self._open_run()
        timeline: list[tuple[object, CandleView]] = []
        for candles in candles_by_symbol.values():
            for candle in candles:
                timeline.append((candle.open_ts, candle))
        timeline.sort(key=lambda item: (str(item[0]), item[1].symbol))
        histories: dict[str, list[CandleView]] = {s: [] for s in candles_by_symbol}
        result = RunResult(
            run_id=str(run.id),
            orders_placed=0,
            orders_filled=0,
            signals=0,
            signals_skipped=0,
            trades=0,
        )
        for _, candle in timeline:
            self.clock.set_time(_as_utc(candle.open_ts) + _interval(candle.timeframe))
            self._live_feed[candle.symbol] = candle.close_price
            self._quote_ts[candle.symbol] = _as_utc(candle.open_ts) + _interval(candle.timeframe)
            histories[candle.symbol].append(candle)
            self._on_candle(run, candle, list(histories[candle.symbol]), result)
            self._match_all(candle)
        return self._close_run(run, result)

    def run_replay(self, ticks_by_symbol: dict[str, list[TickView]]) -> RunResult:
        """Walk recorded ticks in order; derive and deliver closed 1m candles."""
        from market_data.candles import TickPoint, build_1m_candles

        run = self._open_run()
        points: dict[str, list[TickPoint]] = {}
        for symbol, ticks in ticks_by_symbol.items():
            points[symbol] = sorted(
                (
                    TickPoint(
                        exchange_ts=_as_utc(t.exchange_ts), ltp=t.ltp, volume_traded=t.volume_traded
                    )
                    for t in ticks
                ),
                key=lambda p: p.exchange_ts,
            )
        per_symbol_1m = {symbol: build_1m_candles(pts) for symbol, pts in points.items()}
        # Minute-close delivery plan: {symbol: {minute_open: CandleData}}.
        minute_map: dict[str, dict] = {}
        for symbol, candles in per_symbol_1m.items():
            minute_map[symbol] = {c.open_ts: c for c in candles}

        merged: list[tuple[datetime, str, TickView]] = []
        for symbol, ticks in ticks_by_symbol.items():
            for tick in ticks:
                merged.append((_as_utc(tick.exchange_ts), symbol, tick))
        merged.sort(key=lambda item: (str(item[0]), item[1]))
        histories: dict[str, list[CandleView]] = {s: [] for s in ticks_by_symbol}
        delivered: dict[str, set] = {s: set() for s in ticks_by_symbol}
        result = RunResult(
            run_id=str(run.id),
            orders_placed=0,
            orders_filled=0,
            signals=0,
            signals_skipped=0,
            trades=0,
        )
        for ts, symbol, tick in merged:
            self.clock.set_time(ts)
            self._live_feed[symbol] = tick.ltp
            self._quote_ts[symbol] = ts
            for signal in self.strategy.on_tick(tick) or []:
                self._route_signal(run, signal, result)
            # Any minute strictly before this tick is now closed: deliver it.
            for sym, minutes in minute_map.items():
                for minute_open in sorted(minutes):
                    if minute_open < ts and minute_open not in delivered[sym]:
                        data = minutes[minute_open]
                        view = CandleView(
                            symbol=sym,
                            timeframe="1m",
                            open_ts=data.open_ts,
                            open_price=data.open_price,
                            high_price=data.high_price,
                            low_price=data.low_price,
                            close_price=data.close_price,
                            volume=data.volume,
                        )
                        # Skip empty minutes (no trades): silence, not signal.
                        if not data.is_empty:
                            histories[sym].append(view)
                            self._on_candle(run, view, list(histories[sym]), result)
                        delivered[sym].add(minute_open)
            self._match_all_tick(symbol, tick)
        return self._close_run(run, result)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _open_run(self) -> StrategyRun:
        self.account.refresh_from_db()
        # Attribution baseline: realized PnL delta over THIS run. Exact when
        # one run owns the account at a time (the v1 contract); concurrent
        # runs on one account share the ledger and split attribution in
        # Phase 5 reports instead.
        self._realized_start = self.account.realized_pnl
        run = StrategyRun.objects.create(
            strategy=self.strategy_row,
            mode=self.spec.mode,
            strategy_version=self.strategy.version,
            parameters_snapshot=dict(self.spec.params),
            data_version=self.spec.data_version,
        )
        self.strategy.on_start(list(self.spec.symbols))
        logger.info("Run %s started: %s %s", run.id, self.spec.strategy_name, self.spec.mode)
        return run

    def _on_candle(
        self,
        run: StrategyRun,
        candle: CandleView,
        history: list[CandleView],
        result: RunResult,
    ) -> None:
        for signal in self.strategy.on_candle(candle, history) or []:
            self._route_signal(run, signal, result)

    def _route_signal(self, run: StrategyRun, signal, result: RunResult) -> None:
        from engine.sizing import size_from_signal

        from .signal_store import persist_signal

        try:
            signal.validate()
        except ValueError as exc:
            logger.error("Strategy emitted invalid signal: %s", exc)
            result.signals_skipped += 1
            return
        instrument = Instrument.objects.filter(
            symbol=signal.symbol, exchange="NSE", is_active=True, effective_to__isnull=True
        ).first()
        if instrument is None:
            # No instrument row exists, so no Signal row can reference it
            # (FK). The skip is counted and logged — the run stays green
            # and other symbols keep trading.
            logger.warning("Signal skipped: unknown symbol %s (master not synced)", signal.symbol)
            result.signals += 1
            result.signals_skipped += 1
            return
        if signal.confidence < self.strategy.min_confidence:
            persist_signal(
                run,
                signal,
                instrument=instrument,
                skipped=f"confidence {signal.confidence} below floor",
            )
            result.signals += 1
            result.signals_skipped += 1
            return
        capital = self.strategy_row.allocated_capital or (
            self.account.current_balance + self.account.unrealized_pnl
        )
        # Align sizing with the risk position-size cap up front: orders the
        # engine would reject are skipped with a reason instead of placed
        # to fail. The engine still re-checks every order downstream.
        from risk.engine import position_size_cap_pct

        cap_pct = position_size_cap_pct(self.account, run)
        cap_notional = capital * cap_pct / Decimal("100") if cap_pct else capital
        sizing = size_from_signal(
            capital=capital,
            risk_pct=self.spec.risk_pct,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            lot_size=instrument.lot_size or 1,
            max_notional=min(capital, cap_notional),
        )
        if sizing.skipped:
            persist_signal(run, signal, instrument=instrument, skipped=sizing.skip_reason)
            result.signals += 1
            result.signals_skipped += 1
            return
        row = persist_signal(run, signal, instrument=instrument)
        result.signals += 1
        use_bracket = signal.target is not None and signal.stop_loss is not None
        self._seq += 1
        order = self.broker.place_order(
            account=self.account,
            instrument=instrument,
            side=signal.direction,
            order_type="MKT",
            quantity=sizing.quantity,
            product_type="MIS",
            validity="DAY",
            client_order_id=f"run-{str(run.id)[:8]}-{self._seq:04d}",
            target_price=signal.target if use_bracket else None,
            stoploss_price=signal.stop_loss if use_bracket else None,
            strategy_run=run,
            signal_id=f"sig-{row.id}",
            stop_loss_price=signal.stop_loss,
            market_quote_ts=self._quote_ts.get(signal.symbol),
        )
        row.order = order
        row.was_executed = order.status == "FLD"
        if not row.was_executed and order.status == "REJ":
            row.skip_reason = f"rejected: {order.rejection_reason}"
            result.signals_skipped += 1
        row.save(update_fields=["order", "was_executed", "skip_reason"])
        result.orders_placed += 1
        if order.status == "FLD":
            result.orders_filled += 1

    def _match_all(self, candle: CandleView) -> None:
        """Let resting stops/targets react to this candle's close."""
        from broker.fills import MarketSnapshot
        from market_data.candles import interval_end

        quote = self._price_of(candle.symbol)
        if quote is None:
            return
        close_ts = interval_end(candle.open_ts, candle.timeframe)
        snapshot = MarketSnapshot(bid=quote, ask=quote, ltp=quote, ts=close_ts)
        instrument = Instrument.objects.filter(
            symbol=candle.symbol, exchange="NSE", is_active=True, effective_to__isnull=True
        ).first()
        if instrument is not None:
            self.broker.process_snapshot(instrument, snapshot)

    def _match_all_tick(self, symbol: str, tick: TickView) -> None:
        from broker.fills import MarketSnapshot

        instrument = Instrument.objects.filter(
            symbol=symbol, exchange="NSE", is_active=True, effective_to__isnull=True
        ).first()
        if instrument is None:
            return
        snapshot = MarketSnapshot(bid=tick.ltp, ask=tick.ltp, ltp=tick.ltp, ts=tick.exchange_ts)
        self.broker.process_snapshot(instrument, snapshot)

    def _close_run(self, run: StrategyRun, result: RunResult) -> RunResult:
        from core.models.trades import Trade

        self.strategy.on_stop()
        trades = Trade.objects.filter(order__strategy_run=run).order_by("executed_at")
        result.trades = trades.count()
        for trade in trades:
            signed = trade.quantity if trade.side == "BUY" else -trade.quantity
            result.fills.append(
                (
                    trade.instrument.symbol,
                    trade.side,
                    trade.quantity,
                    str(trade.price),
                    str(signed * trade.quantity),
                )
            )
            result.total_charges += trade.total_charges
        self.account.refresh_from_db()
        result.final_balance = self.account.current_balance
        result.realized_pnl = self.account.realized_pnl - self._realized_start
        run.realized_pnl = result.realized_pnl
        run.trades_count = result.trades
        # Model provenance for ML-driven runs (baselines leave it blank).
        run.model_version = getattr(self.strategy, "run_model_version", "") or run.model_version
        run.is_running = False
        run.ended_at = _as_utc(self.clock.now())
        run.save()
        logger.info(
            "Run %s closed: orders=%d fills=%d realized=%s",
            run.id,
            result.orders_placed,
            result.trades,
            result.realized_pnl,
        )
        return result


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _interval(timeframe: str) -> timedelta:
    minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 24 * 60}
    return timedelta(minutes=minutes.get(timeframe, 1))
