"""
Paper Trading Simulator — Baseline 3: ranking strategy (Phase 6)
==================================================================
Trades the model's published ranking instead of price patterns: each
trading day, go long the top-N ranked symbols (equal attention, sized by
the runner's risk formula), optionally short the bottom-N. Stops and
targets ride along exactly like the price baselines, so fills, brackets,
and risk behave identically — only the IDEA comes from the model.

Look-ahead discipline lives in two places: the provider only serves
rows published AND featurized before the trade date, and this strategy
pulls one ranking per trading day (cached by date), never per bar.
Every emitted signal carries the publication's model_version, which the
runner persists onto the Signal row (the Phase 6 exit gate).
"""

from datetime import UTC
from decimal import Decimal

from engine.base import BaseStrategy, CandleView, EmittedSignal
from ml.provider import ScoreProvider


class RankingStrategy(BaseStrategy):
    """Top-N long (optional bottom-N short) from published model rankings."""

    name = "ranking"
    default_params = {
        "top_n": 3,
        "bottom_n": 0,  # shorts need MIS intraday; default off for safety
        "horizon": "1d",
        "stop_pct": "0.01",
        "target_pct": "0.02",
        "timeframe": "1m",
    }

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        horizon = str(self.params["horizon"])
        self.provider = ScoreProvider(horizon=horizon)
        self.run_model_version = ""  # latest publication consumed (for the run record)
        self._emitted: set = set()  # (trade_date, symbol) already signaled

    def on_candle(self, candle: CandleView, history: list[CandleView]) -> list[EmittedSignal]:

        _ = history
        trade_date = candle.open_ts.astimezone(UTC).date()
        ranking = self.provider.for_date(trade_date)
        if ranking is None:
            return []  # unpublished: silence, never blind trades
        self.run_model_version = ranking.model_version
        key = (trade_date, candle.symbol)
        if key in self._emitted:
            return []  # one signal per symbol per trading day, not per bar
        top_n = int(self.params["top_n"])
        bottom_n = int(self.params["bottom_n"])
        top_symbols = {r.symbol for r in ranking.ranked[: max(top_n, 0)]}
        bottom_symbols = {r.symbol for r in ranking.ranked[-bottom_n:]} if bottom_n > 0 else set()
        if candle.symbol in top_symbols:
            direction = "BUY"
        elif candle.symbol in bottom_symbols:
            direction = "SELL"
        else:
            return []
        self._emitted.add(key)
        # Entry prices come from the SUBJECT symbol's own bar — never from
        # another symbol's trigger bar (that would be cross-contamination).
        return [self._signal(candle, candle.symbol, direction, ranking)]

    def _signal(self, candle: CandleView, symbol: str, direction: str, ranking) -> EmittedSignal:
        entry = candle.close_price
        stop_pct = Decimal(str(self.params["stop_pct"]))
        target_pct = Decimal(str(self.params["target_pct"]))
        if direction == "BUY":
            stop = (entry * (Decimal("1") - stop_pct)).quantize(Decimal("0.0001"))
            target = (entry * (Decimal("1") + target_pct)).quantize(Decimal("0.0001"))
        else:
            stop = (entry * (Decimal("1") + stop_pct)).quantize(Decimal("0.0001"))
            target = (entry * (Decimal("1") - target_pct)).quantize(Decimal("0.0001"))
        signal = EmittedSignal(
            symbol=symbol,
            direction=direction,
            entry_price=entry,
            stop_loss=stop,
            target=target,
            confidence=Decimal("0.5000"),
            model_version=ranking.model_version,
            metadata={"score_date": ranking.score_date.isoformat(), "horizon": ranking.horizon},
        )
        signal.validate()
        return signal
