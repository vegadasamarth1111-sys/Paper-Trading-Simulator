"""
Paper Trading Simulator — Instrument Master Loader
====================================================
Downloads and syncs the instrument list from the broker API provider.
Run daily before market open to capture new listings, expiries, and lot size changes.

This is a skeleton for Phase 0. The actual provider integration
(Zerodha/Angel/Upstox/Fyers) is built in Phase 1.
"""

import logging
from abc import ABC, abstractmethod
from datetime import date, datetime
from decimal import Decimal
from typing import TypedDict

from django.db import transaction

from core.enums import Segment
from core.models.instruments import Instrument, TradingHoliday

logger = logging.getLogger("core")


class InstrumentSyncSummary(TypedDict):
    """Counters returned by InstrumentLoader.sync_instruments()."""

    total_fetched: int
    created: int
    updated: int
    deactivated: int
    unchanged: int
    errors: list[str]


class HolidaySyncSummary(TypedDict):
    """Counters returned by InstrumentLoader.sync_holidays()."""

    total: int
    created: int
    existing: int
    errors: list[str]


class InstrumentProviderBase(ABC):
    """
    Abstract base class for instrument data providers.
    Each broker API implements this interface.

    Verify each provider's current API terms, rate limits and pricing
    before committing (as stated in the production plan).
    """

    @abstractmethod
    def fetch_instruments(self) -> list[dict]:
        """
        Fetch the current instrument list from the provider.

        Returns a list of dicts with standardized keys:
        {
            "symbol": "RELIANCE",
            "exchange": "NSE",
            "segment": "EQ",
            "name": "Reliance Industries Limited",
            "exchange_token": "256265",
            "isin": "INE002A01018",
            "lot_size": 1,
            "tick_size": "0.05",
            "expiry_date": None,         # date or None
            "strike_price": None,        # Decimal or None
            "option_type": "",           # "CE", "PE", or ""
            "underlying_symbol": "",     # for derivatives
            "upper_circuit": "2500.00",  # or None
            "lower_circuit": "2000.00",  # or None
        }
        """
        ...

    @abstractmethod
    def fetch_holidays(self, year: int) -> list[dict]:
        """
        Fetch trading holidays for the given year.

        Returns a list of dicts:
        {
            "date": "2025-01-26",
            "exchange": "NSE",
            "description": "Republic Day",
            "is_half_day": False,
            "close_time": None,
        }
        """
        ...


class InstrumentLoader:
    """
    Syncs instruments from a provider into the database.
    Handles slowly changing dimensions: when an instrument's lot size
    or tick size changes, the old row is end-dated and a new row is created.
    """

    def __init__(self, provider: InstrumentProviderBase):
        self.provider = provider

    @transaction.atomic
    def sync_instruments(self) -> InstrumentSyncSummary:
        """
        Synchronize instruments from the provider.

        Returns a summary:
        {
            "total_fetched": 5000,
            "created": 150,
            "updated": 30,
            "deactivated": 5,
            "unchanged": 4815,
            "errors": [],
        }
        """
        logger.info("Starting instrument sync...")
        today = date.today()

        raw_instruments = self.provider.fetch_instruments()
        logger.info("Fetched %d instruments from provider.", len(raw_instruments))

        summary: InstrumentSyncSummary = {
            "total_fetched": len(raw_instruments),
            "created": 0,
            "updated": 0,
            "deactivated": 0,
            "unchanged": 0,
            "errors": [],
        }

        seen_keys = set()

        for raw in raw_instruments:
            try:
                # Each row is processed inside its own savepoint. If anything
                # goes wrong we roll that row back to exactly how we found it,
                # rather than leaving a half-applied change behind. (Without
                # this, a failure after `existing.save()` would leave an
                # instrument end-dated with no replacement — silently removing
                # it from the instrument master.)
                with transaction.atomic():
                    symbol = raw["symbol"]
                    exchange = raw["exchange"]
                    key = (symbol, exchange)
                    seen_keys.add(key)

                    # Check for existing active instrument
                    existing = Instrument.objects.filter(
                        symbol=symbol,
                        exchange=exchange,
                        is_active=True,
                        effective_to__isnull=True,
                    ).first()

                    if existing:
                        # Check if anything changed that requires a new version
                        changed = self._has_changed(existing, raw)
                        if changed:
                            # End-date the old version
                            existing.effective_to = today
                            existing.is_active = False
                            existing.save()

                            # Create new version
                            self._create_instrument(raw, today)
                            summary["updated"] += 1
                            logger.info(
                                "Instrument updated (new version): %s %s - %s",
                                symbol,
                                exchange,
                                changed,
                            )
                        else:
                            summary["unchanged"] += 1
                    else:
                        self._create_instrument(raw, today)
                        summary["created"] += 1

            except Exception as exc:
                error_msg = f"Error processing instrument {raw.get('symbol', '?')}: {exc}"
                summary["errors"].append(error_msg)
                logger.error(error_msg)

        # Deactivate instruments not in the latest fetch
        # (expired contracts, delisted stocks)
        active_instruments = Instrument.objects.filter(
            is_active=True,
            effective_to__isnull=True,
        )
        for inst in active_instruments:
            if (inst.symbol, inst.exchange) not in seen_keys:
                inst.is_active = False
                inst.effective_to = today
                inst.save()
                summary["deactivated"] += 1
                logger.info("Instrument deactivated: %s %s", inst.symbol, inst.exchange)

        logger.info(
            "Instrument sync complete: created=%d updated=%d deactivated=%d unchanged=%d errors=%d",
            summary["created"],
            summary["updated"],
            summary["deactivated"],
            summary["unchanged"],
            len(summary["errors"]),
        )

        return summary

    @transaction.atomic
    def sync_holidays(self, year: int) -> HolidaySyncSummary:
        """Sync trading holidays for the given year."""
        logger.info("Syncing holidays for year %d...", year)

        raw_holidays = self.provider.fetch_holidays(year)
        summary: HolidaySyncSummary = {
            "total": len(raw_holidays),
            "created": 0,
            "existing": 0,
            "errors": [],
        }

        for raw in raw_holidays:
            try:
                holiday_date = (
                    datetime.strptime(raw["date"], "%Y-%m-%d").date()
                    if isinstance(raw["date"], str)
                    else raw["date"]
                )

                _, created = TradingHoliday.objects.get_or_create(
                    date=holiday_date,
                    exchange=raw["exchange"],
                    defaults={
                        "description": raw.get("description", ""),
                        "is_half_day": raw.get("is_half_day", False),
                        "close_time": raw.get("close_time"),
                    },
                )

                if created:
                    summary["created"] += 1
                else:
                    summary["existing"] += 1

            except Exception as exc:
                error_msg = f"Error processing holiday {raw.get('date', '?')}: {exc}"
                summary["errors"].append(error_msg)
                logger.error(error_msg)

        logger.info(
            "Holiday sync complete: created=%d, existing=%d, errors=%d",
            summary["created"],
            summary["existing"],
            len(summary["errors"]),
        )
        return summary

    @staticmethod
    def _has_changed(existing: Instrument, raw: dict) -> str:
        """Check if instrument data has changed. Returns description of change or empty string."""
        changes = []

        if existing.lot_size != int(raw.get("lot_size", existing.lot_size)):
            changes.append(f"lot_size: {existing.lot_size}→{raw['lot_size']}")

        raw_tick = Decimal(str(raw.get("tick_size", existing.tick_size)))
        if existing.tick_size != raw_tick:
            changes.append(f"tick_size: {existing.tick_size}→{raw_tick}")

        if existing.name != raw.get("name", existing.name):
            changes.append("name changed")

        return ", ".join(changes)

    @staticmethod
    def _create_instrument(raw: dict, effective_date: date) -> Instrument:
        """Create a new Instrument record from raw provider data."""
        return Instrument.objects.create(
            symbol=raw["symbol"],
            exchange=raw["exchange"],
            segment=raw.get("segment", Segment.EQUITY),
            name=raw.get("name", raw["symbol"]),
            exchange_token=raw.get("exchange_token", ""),
            isin=raw.get("isin", ""),
            lot_size=int(raw.get("lot_size", 1)),
            tick_size=Decimal(str(raw.get("tick_size", "0.05"))),
            expiry_date=raw.get("expiry_date"),
            strike_price=(Decimal(str(raw["strike_price"])) if raw.get("strike_price") else None),
            option_type=raw.get("option_type", ""),
            underlying_symbol=raw.get("underlying_symbol", ""),
            upper_circuit=(
                Decimal(str(raw["upper_circuit"])) if raw.get("upper_circuit") else None
            ),
            lower_circuit=(
                Decimal(str(raw["lower_circuit"])) if raw.get("lower_circuit") else None
            ),
            effective_from=effective_date,
            is_active=True,
        )
