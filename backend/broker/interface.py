"""
Paper Trading Simulator — Broker interface (Phase 2, plan section 2)
=======================================================================
Strategy code only ever calls these methods. PaperBroker implements them
today; a future LiveBroker implements the same interface, so any strategy
validated here moves to live trading by replacing a single module.
"""

from abc import ABC, abstractmethod
from decimal import Decimal

from broker.fills import MarketSnapshot
from core.models.accounts import Account
from core.models.instruments import Instrument
from core.models.orders import Order
from core.models.strategies import StrategyRun


class BrokerInterface(ABC):
    """The only trading surface strategies may touch."""

    @abstractmethod
    def place_order(
        self,
        *,
        account: Account,
        instrument: Instrument,
        side: str,
        order_type: str,
        quantity: int,
        price: Decimal | None = None,
        trigger_price: Decimal | None = None,
        product_type: str = "MIS",
        validity: str = "DAY",
        client_order_id: str,
        target_price: Decimal | None = None,
        stoploss_price: Decimal | None = None,
        strategy_run: StrategyRun | None = None,
        signal_id: str | None = None,
        stop_loss_price: Decimal | None = None,
        skip_risk: bool = False,
    ) -> Order:
        """Validate, margin-check, and open an order. Returns the Order."""
        ...

    @abstractmethod
    def modify_order(
        self,
        order: Order,
        *,
        price: Decimal | None = None,
        trigger_price: Decimal | None = None,
        quantity: int | None = None,
    ) -> Order:
        """Change price/trigger/quantity on an OPEN order."""
        ...

    @abstractmethod
    def cancel_order(self, order: Order, *, reason: str = "") -> Order:
        """Cancel an open order. Returns the updated Order."""
        ...

    @abstractmethod
    def process_snapshot(self, instrument: Instrument, snapshot: MarketSnapshot) -> dict:
        """
        Match all open orders on an instrument against one market snapshot.
        Returns {"fills": [...], "rejected": [...], "triggered": [...] }.
        """
        ...
