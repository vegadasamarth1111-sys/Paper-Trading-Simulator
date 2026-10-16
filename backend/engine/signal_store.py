"""
Paper Trading Simulator — Signal persistence (Phase 4)
=========================================================
Every emitted signal lands in the signals table BEFORE sizing and risk,
so skipped signals are explainable (skip_reason) and executed ones link
to their order. The duplicate guard keys off sig-{row id}: stable within
a run, unique across runs.
"""

from core.models.strategies import Signal, StrategyRun


def persist_signal(run: StrategyRun, signal, instrument=None, skipped: str = "") -> Signal:
    """Write one EmittedSignal. Returns the row (its id feeds signal_id)."""
    from core.models.instruments import Instrument

    if instrument is None:
        instrument = Instrument.objects.filter(
            symbol=signal.symbol, exchange="NSE", is_active=True, effective_to__isnull=True
        ).first()
    if instrument is None:  # pragma: no cover - caller pre-checks the master
        raise ValueError(f"unknown symbol {signal.symbol!r}")
    return Signal.objects.create(
        strategy_run=run,
        instrument=instrument,
        direction=signal.direction,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        target=signal.target,
        confidence=signal.confidence,
        model_version=signal.model_version,
        was_executed=False,
        skip_reason=skipped,
        metadata=dict(signal.metadata),
    )
