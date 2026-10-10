"""
Paper Trading Simulator — Instrument Services
================================================
Business logic layer for querying instruments.
All queries go through here — views never touch models directly.
"""

import logging
from datetime import date
from decimal import Decimal

from django.db.models import Q, QuerySet
from django.utils import timezone

from core.enums import Exchange, Segment
from core.models.instruments import Instrument

logger = logging.getLogger("core")


class InstrumentService:
    """Service layer for instrument queries and operations."""

    @staticmethod
    def get_active_instruments(
        exchange: str | None = None,
        segment: str | None = None,
        search: str | None = None,
    ) -> QuerySet:
        """
        Get currently active instruments with optional filtering.
        Only returns instruments with no effective_to date (current version).
        """
        qs = Instrument.objects.filter(
            is_active=True,
            effective_to__isnull=True,
        )

        if exchange:
            qs = qs.filter(exchange=exchange)
        if segment:
            qs = qs.filter(segment=segment)
        if search:
            qs = qs.filter(Q(symbol__icontains=search) | Q(name__icontains=search))

        return qs

    @staticmethod
    def get_option_chain(
        underlying_symbol: str,
        expiry_date: date,
        exchange: str = Exchange.NSE,
    ) -> QuerySet:
        """Get the option chain for a given underlying and expiry."""
        return Instrument.objects.filter(
            underlying_symbol=underlying_symbol,
            expiry_date=expiry_date,
            exchange=exchange,
            segment=Segment.OPTIONS,
            is_active=True,
            effective_to__isnull=True,
        ).order_by("strike_price", "option_type")

    @staticmethod
    def get_instrument_by_token(exchange_token: str) -> Instrument | None:
        """Look up an instrument by its exchange token (for data subscription)."""
        return Instrument.objects.filter(
            exchange_token=exchange_token,
            is_active=True,
            effective_to__isnull=True,
        ).first()

    @staticmethod
    def get_expiry_dates(
        underlying_symbol: str,
        exchange: str = Exchange.NSE,
    ) -> list[date]:
        """Get available expiry dates for a derivative underlying."""
        # `expiry_date` is a nullable column, so values_list() types as
        # date | None. The __gte filter already excludes NULL rows in SQL;
        # the guard keeps the Python type honest without changing behaviour.
        expiries = (
            Instrument.objects.filter(
                underlying_symbol=underlying_symbol,
                exchange=exchange,
                is_active=True,
                effective_to__isnull=True,
                expiry_date__gte=timezone.now().date(),
            )
            .values_list("expiry_date", flat=True)
            .distinct()
            .order_by("expiry_date")
        )
        return [expiry for expiry in expiries if expiry is not None]

    @staticmethod
    def get_adjustment_factor(
        instrument: Instrument,
        from_date: date,
        to_date: date,
    ) -> Decimal:
        """
        Calculate the cumulative adjustment factor for an instrument
        between two dates (for adjusted historical data).
        """
        from core.models.instruments import CorporateAction

        actions = CorporateAction.objects.filter(
            instrument=instrument,
            ex_date__gte=from_date,
            ex_date__lte=to_date,
        )

        cumulative_factor = Decimal("1.000000")
        for action in actions:
            cumulative_factor *= action.adjustment_factor

        return cumulative_factor
