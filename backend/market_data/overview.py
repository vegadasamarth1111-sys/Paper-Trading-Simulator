"""
Paper Trading Simulator — Market overview, indices, option chain (Phase 5+)
=============================================================================
Dashboard-grade read endpoints, all authenticated and read-only:

- GET /api/v1/market/overview/?symbols=A,B (default: seed universe) ->
  per-symbol LTP plus day change derived from the last two daily closes.
  Unknown symbols are skipped (listed under "missing"), never invented.
- GET /api/v1/market/indices/ -> index instruments (IDX segment) + changes.
- GET /api/v1/market/expiries/?underlying=NIFTY -> expiry dates.
- GET /api/v1/market/option-chain/?underlying=NIFTY&expiry=YYYY-MM-DD ->
  strikes with CE/PE LTPs (null when never quoted — honest gaps).
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enums import Segment
from core.models.instruments import Instrument

from .models import Candle
from .services import get_latest_price


def _day_change(symbol: str) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """(change, change_pct, previous_close) for the dashboard tape.

    Live quote newer than the last stored close: change vs that close.
    Otherwise: the last stored session's own move (closes[0] - closes[1]).
    Anything else is an honest null, never a fabricated zero.
    """
    closes = list(
        Candle.objects.filter(symbol=symbol.upper(), timeframe="1d")
        .order_by("-open_ts")
        .values("close_price", "open_ts")[:2]
    )
    if not closes:
        return None, None, None
    quote = get_latest_price(symbol.upper())
    quote_ts = quote["ts"] if quote is not None else None
    quote_ltp = quote["ltp"] if quote is not None else None
    if quote_ts is not None and quote_ltp is not None and quote_ts > closes[0]["open_ts"]:
        last, prev = quote_ltp, closes[0]["close_price"]
    elif len(closes) == 2:
        last, prev = closes[0]["close_price"], closes[1]["close_price"]
    else:
        return None, None, None
    if prev == 0:
        return None, None, None
    change = last - prev
    pct = change / prev * 100
    return change, pct, prev


def _quote_row(symbol: str, name: str = "") -> dict:
    quote = get_latest_price(symbol.upper())
    change, pct, prev = _day_change(symbol)
    return {
        "symbol": symbol.upper(),
        "name": name,
        "ltp": str(quote["ltp"]) if quote else None,
        "ts": quote["ts"].isoformat() if quote else None,
        "change": str(change.quantize(Decimal("0.01"))) if change is not None else None,
        "change_pct": str(pct.quantize(Decimal("0.01"))) if pct is not None else None,
    }


class OverviewView(APIView):
    """Watchlist-grade snapshot for many symbols in one round trip."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from market_data.universe import get_seed_universe

        param = request.query_params.get("symbols", "")
        wanted = [s.strip().upper() for s in param.split(",") if s.strip()]
        universe = {e.symbol: e.name for e in get_seed_universe()}
        if not wanted:
            wanted = list(universe)
        rows, missing = [], []
        for symbol in wanted[:100]:
            quote = get_latest_price(symbol)
            if quote is None and symbol not in universe:
                # No quote AND not in the known universe: nothing to show.
                inst = Instrument.objects.filter(
                    symbol=symbol,
                    exchange="NSE",
                    is_active=True,
                    effective_to__isnull=True,
                ).first()
                if inst is None:
                    missing.append(symbol)
                    continue
                name = inst.name
            else:
                name = universe.get(symbol, "")
            rows.append(_quote_row(symbol, name))
        return Response({"symbols": rows, "missing": missing})


class IndicesView(APIView):
    """Index tape (NIFTY, BANKNIFTY, INDIAVIX + any IDX rows in the master)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = (
            Instrument.objects.filter(
                exchange="NSE",
                segment=Segment.INDEX,
                is_active=True,
                effective_to__isnull=True,
            )
            .order_by("symbol")
            .values("symbol", "name")
        )
        return Response({"indices": [_quote_row(row["symbol"], row["name"]) for row in rows]})


class ExpiriesView(APIView):
    """Available expiries for a derivatives underlying."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from instruments.services import InstrumentService

        underlying = (request.query_params.get("underlying") or "").strip().upper()
        if not underlying:
            return Response(
                {"errors": ["query param 'underlying' is required"]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        dates = InstrumentService.get_expiry_dates(underlying)
        return Response({"underlying": underlying, "expiries": [d.isoformat() for d in dates]})


class OptionChainView(APIView):
    """Strike table with live CE/PE quotes for one underlying + expiry."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.utils.dateparse import parse_date

        from instruments.services import InstrumentService

        underlying = (request.query_params.get("underlying") or "").strip().upper()
        expiry = parse_date(request.query_params.get("expiry") or "")
        if not underlying or expiry is None:
            return Response(
                {"errors": ["query params 'underlying' and 'expiry=YYYY-MM-DD' are required"]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        chain = InstrumentService.get_option_chain(underlying, expiry)
        strikes: dict = {}
        for contract in chain.select_related():
            key = str(contract.strike_price)
            leg = strikes.setdefault(
                key, {"strike": key, "call": None, "put": None, "expiry": expiry.isoformat()}
            )
            quote = get_latest_price(contract.symbol)
            side = "call" if contract.option_type == "CE" else "put"
            leg[side] = {
                "symbol": contract.symbol,
                "ltp": str(quote["ltp"]) if quote else None,
                "lot_size": contract.lot_size,
            }
        ordered = sorted(strikes.values(), key=lambda leg: float(leg["strike"]))
        return Response(
            {"underlying": underlying, "expiry": expiry.isoformat(), "strikes": ordered}
        )
