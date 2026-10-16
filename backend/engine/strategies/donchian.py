"""
Paper Trading Simulator — Baseline 2: Donchian breakout (Phase 4)
====================================================================
Buys a close above the N-bar highest high, sells a close below the N-bar
lowest low. Same fixed-fraction stop/target plumbing as the SMA baseline.
Chosen because it behaves differently (breakout vs trend-cross), so the
two baselines exercise different paths through sizing and brackets.
"""

from decimal import Decimal

from engine.base import BaseStrategy, CandleView, EmittedSignal


class DonchianBreakout(BaseStrategy):
    """N-bar Donchian channel breakout on closed candles."""

    name = "donchian"
    default_params = {
        "lookback": 20,
        "stop_pct": "0.01",
        "target_pct": "0.02",
        "timeframe": "1m",
    }

    def on_candle(self, candle: CandleView, history: list[CandleView]) -> list[EmittedSignal]:
        lookback = int(self.params["lookback"])
        if len(history) < lookback + 1:  # channel bars + the signal bar
            return []
        channel = history[-(lookback + 1) : -1]
        highest = max(c.high_price for c in channel)
        lowest = min(c.low_price for c in channel)
        direction = None
        if candle.close_price > highest:
            direction = "BUY"
        elif candle.close_price < lowest:
            direction = "SELL"
        if direction is None:
            return []
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
            symbol=candle.symbol,
            direction=direction,
            entry_price=entry,
            stop_loss=stop,
            target=target,
            confidence=Decimal("0.5000"),
            metadata={"highest": str(highest), "lowest": str(lowest)},
        )
        signal.validate()
        return [signal]
