"""Instrument API views."""

import bleach
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models.instruments import Instrument
from instruments.services import InstrumentService


class InstrumentSerializer(serializers.ModelSerializer):
    """Read-only serializer for instrument data."""

    class Meta:
        model = Instrument
        fields = [
            "id",
            "symbol",
            "exchange",
            "segment",
            "name",
            "lot_size",
            "tick_size",
            "expiry_date",
            "strike_price",
            "option_type",
            "underlying_symbol",
            "upper_circuit",
            "lower_circuit",
            "is_active",
        ]
        read_only_fields = fields


class InstrumentListView(APIView):
    """
    List active instruments with optional filtering.
    Supports query parameters: exchange, segment, search.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Sanitize query parameters
        exchange = bleach.clean(request.query_params.get("exchange", "")).strip() or None
        segment = bleach.clean(request.query_params.get("segment", "")).strip() or None
        search = bleach.clean(request.query_params.get("search", "")).strip() or None

        instruments = InstrumentService.get_active_instruments(
            exchange=exchange,
            segment=segment,
            search=search,
        )[:200]  # Cap results

        serializer = InstrumentSerializer(instruments, many=True)
        return Response({"success": True, "data": serializer.data})
