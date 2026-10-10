"""
Tests for instruments.loader — the instrument master sync (Phase 0 deliverable).

The loader is what keeps lot sizes, tick sizes and expiries correct. Plan
section 5.6 is explicit that lot size must never be hard-coded, so the
slowly-changing-dimension behaviour (end-date old row, insert new row) gets
particular attention here.
"""

from datetime import date
from decimal import Decimal

import pytest

from core.enums import Segment
from core.models import Instrument, TradingHoliday
from instruments.loader import InstrumentLoader, InstrumentProviderBase

pytestmark = pytest.mark.django_db


def _fixed_today(day):
    """
    Patch `date.today()` as seen by the loader.

    The loader stamps every instrument version with `date.today()`, and
    versions are unique on (symbol, exchange, effective_from). Simulating a
    later day is the only way to exercise the slowly-changing-dimension path
    deterministically inside a single test.
    """
    from unittest.mock import patch

    class _StubDate:
        @staticmethod
        def today():
            return day

    return patch("instruments.loader.date", _StubDate)


class FakeProvider(InstrumentProviderBase):
    """Deterministic stand-in for a real broker API."""

    def __init__(self, instruments=None, holidays=None):
        self._instruments = instruments or []
        self._holidays = holidays or []

    def fetch_instruments(self):
        return self._instruments

    def fetch_holidays(self, year):
        return self._holidays


def raw_equity(**overrides):
    payload = {
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "segment": "EQ",
        "name": "Reliance Industries Limited",
        "exchange_token": "256265",
        "isin": "INE002A01018",
        "lot_size": 1,
        "tick_size": "0.05",
        "expiry_date": None,
        "strike_price": None,
        "option_type": "",
        "underlying_symbol": "",
        "upper_circuit": "2500.00",
        "lower_circuit": "2000.00",
    }
    payload.update(overrides)
    return payload


class TestProviderContract:
    def test_provider_is_abstract(self):
        """A provider must implement both fetch methods before it can be used."""
        with pytest.raises(TypeError):
            InstrumentProviderBase()  # type: ignore[abstract]

    def test_partial_implementation_is_still_abstract(self):
        class Partial(InstrumentProviderBase):
            def fetch_instruments(self):
                return []

        with pytest.raises(TypeError):
            Partial()  # type: ignore[abstract]


class TestSyncInstruments:
    def test_creates_instruments_that_do_not_exist(self):
        loader = InstrumentLoader(FakeProvider(instruments=[raw_equity()]))
        summary = loader.sync_instruments()

        assert summary["created"] == 1
        assert summary["total_fetched"] == 1
        assert summary["errors"] == []
        instrument = Instrument.objects.get(symbol="RELIANCE")
        assert instrument.is_active is True
        assert instrument.effective_to is None
        assert instrument.lot_size == 1
        assert instrument.tick_size == Decimal("0.05")

    def test_rerun_with_no_changes_is_idempotent(self):
        InstrumentLoader(FakeProvider(instruments=[raw_equity()])).sync_instruments()
        summary = InstrumentLoader(FakeProvider(instruments=[raw_equity()])).sync_instruments()

        assert summary["unchanged"] == 1
        assert summary["created"] == 0
        assert Instrument.objects.count() == 1

    def test_lot_size_change_creates_a_new_version(self):
        """Plan 5.6: lot size comes from the master; a change is a new row."""
        InstrumentLoader(FakeProvider(instruments=[raw_equity()])).sync_instruments()
        original = Instrument.objects.get(symbol="RELIANCE")

        # A later trading day, so the new version gets its own effective_from
        # and does not collide with the original row's.
        with _fixed_today(date(2026, 1, 2)):
            summary = InstrumentLoader(
                FakeProvider(instruments=[raw_equity(lot_size=250)])
            ).sync_instruments()

        assert summary["updated"] == 1
        original.refresh_from_db()
        assert original.is_active is False
        assert original.effective_to == date(2026, 1, 2)

        current = Instrument.objects.get(symbol="RELIANCE", is_active=True)
        assert current.lot_size == 250
        assert current.id != original.id
        assert current.effective_from == date(2026, 1, 2)

    def test_tick_size_change_creates_a_new_version(self):
        InstrumentLoader(FakeProvider(instruments=[raw_equity()])).sync_instruments()
        with _fixed_today(date(2026, 1, 2)):
            InstrumentLoader(
                FakeProvider(instruments=[raw_equity(tick_size="0.10")])
            ).sync_instruments()

        current = Instrument.objects.get(symbol="RELIANCE", is_active=True)
        assert current.tick_size == Decimal("0.10")
        assert Instrument.objects.filter(symbol="RELIANCE").count() == 2

    def test_name_change_creates_a_new_version(self):
        InstrumentLoader(FakeProvider(instruments=[raw_equity()])).sync_instruments()
        with _fixed_today(date(2026, 1, 2)):
            summary = InstrumentLoader(
                FakeProvider(instruments=[raw_equity(name="Reliance Industries Ltd")])
            ).sync_instruments()

        assert summary["updated"] == 1
        assert Instrument.objects.get(symbol="RELIANCE", is_active=True).name == (
            "Reliance Industries Ltd"
        )

    def test_same_day_change_is_rolled_back_not_half_applied(self):
        """
        Instrument versions are unique on (symbol, exchange, effective_from).
        If a contract's lot size changes on the very day it was first loaded,
        the replacement row cannot be inserted. That failure must roll the row
        back completely: the original must stay active and tradeable, and the
        problem must be reported — never left as an end-dated row with no
        successor, which would silently drop the instrument from the master.
        """
        with _fixed_today(date(2026, 1, 1)):
            InstrumentLoader(FakeProvider(instruments=[raw_equity()])).sync_instruments()

            summary = InstrumentLoader(
                FakeProvider(instruments=[raw_equity(lot_size=250)])
            ).sync_instruments()

        assert len(summary["errors"]) == 1
        assert summary["updated"] == 0

        surviving = Instrument.objects.get(symbol="RELIANCE")
        assert surviving.is_active is True
        assert surviving.effective_to is None
        assert surviving.lot_size == 1, "original row must be untouched"
        assert Instrument.objects.count() == 1

    def test_instruments_missing_from_fetch_are_deactivated(self):
        """Delisted stocks and expired contracts must drop out of the master."""
        InstrumentLoader(
            FakeProvider(instruments=[raw_equity(), raw_equity(symbol="TCS")])
        ).sync_instruments()

        summary = InstrumentLoader(FakeProvider(instruments=[raw_equity()])).sync_instruments()

        assert summary["deactivated"] == 1
        tcs = Instrument.objects.get(symbol="TCS")
        assert tcs.is_active is False
        assert tcs.effective_to is not None

    def test_a_bad_row_does_not_abort_the_whole_sync(self):
        loader = InstrumentLoader(FakeProvider(instruments=[{}, raw_equity(symbol="TCS")]))
        summary = loader.sync_instruments()

        assert len(summary["errors"]) == 1
        assert summary["created"] == 1
        assert Instrument.objects.filter(symbol="TCS").exists()

    def test_options_are_loaded_with_lot_size_and_expiry(self):
        option = raw_equity(
            symbol="NIFTY26OCT25000CE",
            segment="OPT",
            underlying_symbol="NIFTY",
            lot_size=75,
            expiry_date=date(2026, 10, 29),
            strike_price="25000.00",
            option_type="CE",
        )
        InstrumentLoader(FakeProvider(instruments=[option])).sync_instruments()

        loaded = Instrument.objects.get(symbol="NIFTY26OCT25000CE")
        assert loaded.segment == Segment.OPTIONS
        assert loaded.lot_size == 75
        assert loaded.expiry_date == date(2026, 10, 29)
        assert loaded.underlying_symbol == "NIFTY"

    def test_empty_fetch_deactivates_everything(self):
        InstrumentLoader(FakeProvider(instruments=[raw_equity()])).sync_instruments()
        summary = InstrumentLoader(FakeProvider(instruments=[])).sync_instruments()
        assert summary["deactivated"] == 1
        assert Instrument.objects.filter(is_active=True).count() == 0


class TestSyncHolidays:
    def test_creates_new_holidays(self):
        loader = InstrumentLoader(
            FakeProvider(
                holidays=[{"date": "2026-01-26", "exchange": "NSE", "description": "Republic Day"}]
            )
        )
        summary = loader.sync_holidays(2026)

        assert summary["created"] == 1
        assert TradingHoliday.objects.count() == 1

    def test_existing_holidays_are_not_duplicated(self):
        holidays = [{"date": "2026-01-26", "exchange": "NSE", "description": "Republic Day"}]
        InstrumentLoader(FakeProvider(holidays=holidays)).sync_holidays(2026)
        summary = InstrumentLoader(FakeProvider(holidays=holidays)).sync_holidays(2026)

        assert summary["existing"] == 1
        assert summary["created"] == 0
        assert TradingHoliday.objects.count() == 1

    def test_accepts_date_objects_as_well_as_strings(self):
        InstrumentLoader(
            FakeProvider(
                holidays=[{"date": date(2026, 3, 10), "exchange": "NSE", "description": "Holi"}]
            )
        ).sync_holidays(2026)
        assert TradingHoliday.objects.get(date=date(2026, 3, 10))

    def test_half_day_flag_is_preserved(self):
        InstrumentLoader(
            FakeProvider(
                holidays=[
                    {
                        "date": "2026-11-08",
                        "exchange": "NSE",
                        "description": "Muhurat Trading",
                        "is_half_day": True,
                    }
                ]
            )
        ).sync_holidays(2026)
        assert TradingHoliday.objects.get(date=date(2026, 11, 8)).is_half_day is True

    def test_bad_holiday_row_is_reported_not_raised(self):
        summary = InstrumentLoader(
            FakeProvider(holidays=[{"exchange": "NSE"}, {"date": "2026-01-26", "exchange": "NSE"}])
        ).sync_holidays(2026)

        assert len(summary["errors"]) == 1
        assert summary["created"] == 1
