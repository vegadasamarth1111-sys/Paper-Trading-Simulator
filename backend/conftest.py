"""
Shared pytest fixtures for the Paper Trading Simulator test suite.

This file lives at `backend/conftest.py` so that pytest inserts `backend/`
onto sys.path, making `config`, `core`, `security` and `instruments`
importable exactly the same way `manage.py` imports them.
"""

from datetime import date
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from core.enums import Exchange, Segment


@pytest.fixture
def api_client() -> APIClient:
    """Unauthenticated DRF API client."""
    return APIClient()


@pytest.fixture
def user(django_user_model):
    """A standard, active end user."""
    return django_user_model.objects.create_user(
        username="samarth",
        email="samarth@example.com",
        password="correct-horse-battery-staple",  # nosec B106 -- test fixture, never a real credential
    )


@pytest.fixture
def other_user(django_user_model):
    """A second user, used to prove users cannot reach each other's data."""
    return django_user_model.objects.create_user(
        username="intruder",
        email="intruder@example.com",
        password="another-strong-passphrase-1",  # nosec B106 -- test fixture only
    )


@pytest.fixture
def account(user):
    """Primary virtual trading account owned by `user`."""
    from core.models import Account

    return Account.objects.create(user=user, name="Primary")


@pytest.fixture
def other_account(other_user):
    """Trading account owned by `other_user`."""
    from core.models import Account

    return Account.objects.create(user=other_user, name="Primary")


@pytest.fixture
def auth_client(api_client, user):
    """API client authenticated as `user` via forced authentication."""
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def equity_instrument():
    """A plain NSE equity instrument."""
    from core.models import Instrument

    return Instrument.objects.create(
        symbol="RELIANCE",
        exchange=Exchange.NSE,
        segment=Segment.EQUITY,
        name="Reliance Industries Limited",
        exchange_token="256265",  # nosec B106 -- NSE exchange token, not a credential
        isin="INE002A01018",
        lot_size=1,
        tick_size=Decimal("0.05"),
        effective_from=date(2024, 1, 1),
    )


@pytest.fixture
def nifty_option_instrument():
    """An index option contract with a lot size and expiry."""
    from core.models import Instrument

    return Instrument.objects.create(
        symbol="NIFTY26OCT25000CE",
        exchange=Exchange.NSE,
        segment=Segment.OPTIONS,
        name="NIFTY 25 Oct 25000 CE",
        underlying_symbol="NIFTY",
        lot_size=75,
        tick_size=Decimal("0.05"),
        strike_price=Decimal("25000.00"),
        option_type="CE",
        expiry_date=date(2026, 10, 29),
        effective_from=date(2026, 1, 1),
    )


@pytest.fixture
def order(account, equity_instrument):
    """A freshly placed BUY order in the NEW state."""
    from core.models import Order

    return Order.objects.create(
        client_order_id="test-order-0001",
        account=account,
        instrument=equity_instrument,
        side="BUY",
        order_type="MKT",
        product_type="MIS",
        quantity=10,
    )


@pytest.fixture
def frozen_clock():
    """
    A REPLAY clock frozen inside market hours (Monday 10:00 IST).

    The broker judges the trading window against its clock, so placements
    need a frozen clock to be time-deterministic. Production uses wall time.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from core.clock import Clock, ClockMode

    clock = Clock(mode=ClockMode.REPLAY)
    clock.set_time(datetime(2026, 1, 5, 4, 30, tzinfo=ZoneInfo("UTC")))
    return clock
