"""Baseline strategy registry (Phase 4). Name -> class, no magic imports."""

from engine.strategies.donchian import DonchianBreakout
from engine.strategies.sma import SMACrossover

REGISTRY: dict[str, type] = {
    SMACrossover.name: SMACrossover,
    DonchianBreakout.name: DonchianBreakout,
}


def get_strategy(name: str, params: dict | None = None):
    """Instantiate a registered baseline by name. Unknown names raise."""
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown strategy {name!r}; registered: {sorted(REGISTRY)}") from None
    return cls(params)
