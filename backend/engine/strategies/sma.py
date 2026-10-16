"""
Paper Trading Simulator — Baseline 1: SMA crossover (Phase 4)
===============================================================
Buys when the fast average crosses above the slow average, sells (short)
on the opposite cross. Stops and targets are fixed fractions of the
signal bar's close. Deliberately naive: its job is proving the pipeline
(signal -> size -> risk -> fill -> ledger), not making money.
"""

from decimal import Decimal

from engine.base import BaseStrategy, CandleView, EmittedSignal


def _sma(closes: list[Decimal], period: int) -> Decimal | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:], Decimal("0")) / Decimal(period)


class SMACrossover(BaseStrategy):
    """Fast/slow simple-moving-average cross on closed candles."""

    name = "sma_cross"
    default_params = {
        "fast": 5,
        "slow": 20,
        "stop_pct": "0.01",  # 1% protective stop
        "target_pct": "0.02",  # 2% target (bracket leg)
        "timeframe": "1m",
    }

    def on_candle(self, candle: CandleView, history: list[CandleView]) -> list[EmittedSignal]:
        fast = int(self.params["fast"])
        slow = int(self.params["slow"])
        if fast >= slow:
            raise ValueError(f"fast ({fast}) must be < slow ({slow})")
        closes = [c.close_price for c in history]
        if len(closes) < slow + 1:  # need a previous bar to detect the cross
            return []
        prev_fast = _sma(closes[:-1], fast)
        prev_slow = _sma(closes[:-1], slow)
        cur_fast = _sma(closes, fast)
        cur_slow = _sma(closes, slow)
        if None in (prev_fast, prev_slow, cur_fast, cur_slow):
            return []
        assert prev_fast is not None and prev_slow is not None
        assert cur_fast is not None and cur_slow is not None

        crossed_up = prev_fast <= prev_slow and cur_fast > cur_slow
        crossed_down = prev_fast >= prev_slow and cur_fast < cur_slow
        if not (crossed_up or crossed_down):
            return []
        direction = "BUY" if crossed_up else "SELL"
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
            metadata={"fast": str(cur_fast), "slow": str(cur_slow)},
        )
        signal.validate()
        return [signal]
