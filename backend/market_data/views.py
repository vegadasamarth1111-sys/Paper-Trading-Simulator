"""
Paper Trading Simulator — Market-data read API (Phase 1)
==========================================================
Authenticated read-only endpoints for the dashboard and strategies:

- GET /api/v1/market/latest/?symbol=RELIANCE  -> latest price (Redis, DB fallback)
- GET /api/v1/market/candles/?symbol=..&timeframe=1m -> closed candles first
- GET /api/v1/market/quality/?date=.. -> daily data-quality report

Write paths (poll/backfill) stay in management commands + Celery, never in
request handlers — the tick loop never runs inside Django views.
"""

from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enums import CandleTimeframe

from .models import Candle, DataQualityReport
from .services import get_latest_price


class LatestPriceView(APIView):
    """Latest known price for one symbol."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        symbol = (request.query_params.get("symbol") or "").strip().upper()
        if not symbol:
            return Response(
                {"errors": ["query param 'symbol' is required"]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        quote = get_latest_price(symbol)
        if quote is None:
            return Response(
                {"errors": [f"no price known for {symbol}; run backfill/poll first"]},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({"symbol": symbol, "ltp": str(quote["ltp"]), "ts": quote["ts"].isoformat()})


class CandleListView(APIView):
    """Closed-first candle history for charting and strategies."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        symbol = (request.query_params.get("symbol") or "").strip().upper()
        timeframe = (request.query_params.get("timeframe") or "1m").strip()
        limit = min(int(request.query_params.get("limit", "200")), 1000)
        if not symbol:
            return Response(
                {"errors": ["query param 'symbol' is required"]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if timeframe not in CandleTimeframe.values:
            return Response(
                {"errors": [f"unknown timeframe {timeframe!r}"]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rows = (
            Candle.objects.filter(symbol=symbol, timeframe=timeframe)
            .order_by("-open_ts")[:limit]
            .values(
                "open_ts",
                "open_price",
                "high_price",
                "low_price",
                "close_price",
                "volume",
                "trades",
                "is_complete",
                "is_empty",
            )
        )
        # Closed candles first: strategies must never read an open candle as truth.
        data = sorted(rows, key=lambda r: (not r["is_complete"], r["open_ts"]))
        serialised = [
            {
                **r,
                "open_ts": r["open_ts"].isoformat(),
                "open_price": str(r["open_price"]),
                "high_price": str(r["high_price"]),
                "low_price": str(r["low_price"]),
                "close_price": str(r["close_price"]),
            }
            for r in data
        ]
        return Response({"symbol": symbol, "timeframe": timeframe, "candles": serialised})


class DataQualityView(APIView):
    """Daily data-quality verdict (suspect days excluded from stats)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        raw = (request.query_params.get("date") or "").strip()
        day = parse_date(raw) if raw else None
        if raw and day is None:
            return Response(
                {"errors": ["date must be YYYY-MM-DD"]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qs = DataQualityReport.objects.all().order_by("-report_date")
        if day is not None:
            qs = qs.filter(report_date=day)
        row = qs.first()
        if row is None:
            return Response(
                {"errors": ["no report yet; run data_quality_report after close"]},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {
                "date": row.report_date.isoformat(),
                "exchange": row.exchange,
                "is_suspect": row.is_suspect,
                "bad_tick_ratio": row.bad_tick_ratio,
                "notes": row.notes,
                "missing_symbols": row.missing_symbols,
            }
        )
