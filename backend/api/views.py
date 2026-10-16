"""
Paper Trading Simulator — Trading API views (Phase 5)
=======================================================
Every view enforces ownership: the account always resolves from
request.user, and object lookups filter by that account first — a 404
for another user's row is indistinguishable from a missing row.
Order placement flows through PaperBroker (risk + margin + fills), so
the HTTP layer adds no parallel trading logic.
"""

import logging
from datetime import datetime
from decimal import Decimal

from django.http import StreamingHttpResponse
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from broker.paper import PaperBroker
from core.exceptions import InvalidOrderTransitionError
from core.models import (
    Account,
    Instrument,
    Order,
    Position,
    RiskConfig,
    RiskEvent,
    Signal,
    Strategy,
    StrategyRun,
    Trade,
)
from market_data.services import get_latest_price
from security.permissions import (
    CanPlaceOrders,
    IsAccountOwner,
    IsAccountOwnerForList,
)
from security.rate_limiting import KillSwitchThrottle, OrderRateThrottle

from .serializers import (
    AccountSerializer,
    OrderModifySerializer,
    OrderPlaceSerializer,
    OrderSerializer,
    PositionSerializer,
    RiskConfigSerializer,
    RiskEventSerializer,
    SignalSerializer,
    StrategyRunSerializer,
    StrategySerializer,
    TradeSerializer,
)

logger = logging.getLogger("core")


def _account_for(user) -> Account:
    """The user's trading account, created on first API use (Rs 10L paper)."""
    account, _ = Account.objects.get_or_create(user=user, defaults={"name": "Primary"})
    return account


def _owned_queryset(user, model, **filters):
    """Account-scoped rows: cross-account access collapses to empty (404)."""
    account = _account_for(user)
    return model.objects.filter(account=account, **filters)


class AccountView(APIView):
    """Balance, margin, equity, drawdown header."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(AccountSerializer(_account_for(request.user)).data)


class OrderListCreateView(APIView):
    """Place orders and list history (status/symbol filters)."""

    permission_classes = [IsAuthenticated, IsAccountOwnerForList, CanPlaceOrders]

    def get_throttles(self):
        if self.request.method == "POST":
            return [OrderRateThrottle()]
        return []

    def get(self, request):
        qs = (
            _owned_queryset(request.user, Order).select_related("instrument").order_by("-placed_at")
        )
        status_filter = request.query_params.get("status")
        symbol = request.query_params.get("symbol")
        if status_filter:
            qs = qs.filter(status=status_filter)
        if symbol:
            qs = qs.filter(instrument__symbol=symbol.upper())
        return Response(OrderSerializer(qs[:200], many=True).data)

    def post(self, request):
        serializer = OrderPlaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        account = _account_for(request.user)
        instrument = Instrument.objects.filter(
            symbol=data["symbol"].upper(),
            exchange="NSE",
            is_active=True,
            effective_to__isnull=True,
        ).first()
        if instrument is None:
            return Response(
                {"errors": [f"unknown or inactive symbol {data['symbol']}"]},
                status=status.HTTP_404_NOT_FOUND,
            )
        order = PaperBroker().place_order(
            account=account,
            instrument=instrument,
            side=data["side"],
            order_type=data["order_type"],
            quantity=data["quantity"],
            price=data.get("price"),
            trigger_price=data.get("trigger_price"),
            product_type=data.get("product_type", "MIS"),
            validity=data.get("validity", "DAY"),
            client_order_id=(f"api-{request.user.id}-{int(datetime.now().timestamp() * 1000)}"),
            target_price=data.get("target_price"),
            stoploss_price=data.get("stoploss_price"),
            stop_loss_price=data.get("stop_loss_price"),
            signal_id=data.get("signal_id") or None,
        )
        body = OrderSerializer(order).data
        code = (
            status.HTTP_201_CREATED
            if order.status != "REJ"
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        return Response(body, status=code)


class OrderDetailView(APIView):
    """Read, modify (OPEN only), or cancel one own order."""

    permission_classes = [IsAuthenticated, IsAccountOwner]

    def _get(self, request, order_id):
        try:
            order = (
                _owned_queryset(request.user, Order).select_related("instrument").get(id=order_id)
            )
        except Order.DoesNotExist, ValueError, TypeError:
            return None
        self.check_object_permissions(request, order)
        return order

    def get(self, request, order_id):
        order = self._get(request, order_id)
        if order is None:
            return Response({"errors": ["order not found"]}, status=status.HTTP_404_NOT_FOUND)
        return Response(OrderSerializer(order).data)

    def patch(self, request, order_id):
        order = self._get(request, order_id)
        if order is None:
            return Response({"errors": ["order not found"]}, status=status.HTTP_404_NOT_FOUND)
        serializer = OrderModifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            PaperBroker().modify_order(order, **serializer.validated_data)
        except InvalidOrderTransitionError as exc:
            return Response({"errors": [exc.message]}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        order.refresh_from_db()
        return Response(OrderSerializer(order).data)

    def delete(self, request, order_id):
        order = self._get(request, order_id)
        if order is None:
            return Response({"errors": ["order not found"]}, status=status.HTTP_404_NOT_FOUND)
        try:
            PaperBroker().cancel_order(order, reason="cancelled via API")
        except InvalidOrderTransitionError as exc:
            return Response({"errors": [exc.message]}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        order.refresh_from_db()
        return Response(OrderSerializer(order).data)


class TradeListView(APIView):
    """Immutable fill history (instrument/date filters)."""

    permission_classes = [IsAuthenticated, IsAccountOwnerForList]

    def get(self, request):
        qs = (
            _owned_queryset(request.user, Trade)
            .select_related("instrument")
            .order_by("-executed_at")
        )
        symbol = request.query_params.get("symbol")
        day = request.query_params.get("date")
        if symbol:
            qs = qs.filter(instrument__symbol=symbol.upper())
        if day:
            parsed = parse_date(day)
            if parsed is None:
                return Response(
                    {"errors": ["date must be YYYY-MM-DD"]}, status=status.HTTP_400_BAD_REQUEST
                )
            qs = qs.filter(executed_at__date=parsed)
        return Response(TradeSerializer(qs[:200], many=True).data)


class PositionListView(APIView):
    """Open positions with MTM refreshed on read (closed on ?open=0)."""

    permission_classes = [IsAuthenticated, IsAccountOwnerForList]

    def get(self, request):
        open_only = request.query_params.get("open", "1") != "0"
        filters: dict = {"is_open": True} if open_only else {}
        account = _account_for(request.user)
        rows = list(
            Position.objects.filter(account=account, **filters)
            .select_related("instrument")
            .order_by("-opened_at")[:200]
        )
        for position in rows:
            if position.is_open and position.quantity != 0:
                quote = get_latest_price(position.instrument.symbol)
                if quote is not None:
                    position.update_unrealized_pnl(quote["ltp"])
                    position.save(update_fields=["last_price", "unrealized_pnl", "updated_at"])
        account.refresh_from_db()
        account.unrealized_pnl = sum(
            (p.unrealized_pnl for p in rows if p.is_open), Decimal("0.0000")
        )
        account.save(update_fields=["unrealized_pnl", "updated_at"])
        return Response(PositionSerializer(rows, many=True).data)


class PnLView(APIView):
    """Summary + 90-day equity curve from daily snapshots."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from core.models.positions import PortfolioSnapshot

        account = _account_for(request.user)
        snapshots = list(
            PortfolioSnapshot.objects.filter(account=account).order_by("-snapshot_date")[:90]
        )
        curve = [
            {
                "date": s.snapshot_date.isoformat(),
                "equity": str(s.total_equity),
                "day_pnl": str(s.day_pnl),
            }
            for s in reversed(snapshots)
        ]
        latest = snapshots[0] if snapshots else None
        return Response(
            {
                "realized_pnl": str(account.realized_pnl),
                "unrealized_pnl": str(account.unrealized_pnl),
                "total_equity": str(account.total_equity),
                "day_pnl": str(latest.day_pnl) if latest else "0.0000",
                "drawdown_pct": str(account.drawdown),
                "equity_curve": curve,
            }
        )


class StrategyListView(APIView):
    """Strategies with live run state."""

    permission_classes = [IsAuthenticated, IsAccountOwnerForList]

    def get(self, request):
        account = _account_for(request.user)
        rows = Strategy.objects.filter(account=account).order_by("name")
        return Response(StrategySerializer(rows, many=True).data)


class StrategyControlView(APIView):
    """Start/stop a strategy (run records opened/closed, never edited)."""

    permission_classes = [IsAuthenticated, IsAccountOwner]

    def _get(self, request, strategy_id):
        try:
            strategy = Strategy.objects.get(id=strategy_id, account=_account_for(request.user))
        except Strategy.DoesNotExist, ValueError, TypeError:
            return None
        self.check_object_permissions(request, strategy)
        return strategy

    def post(self, request, strategy_id, action):
        from django.utils import timezone

        strategy = self._get(request, strategy_id)
        if strategy is None:
            return Response({"errors": ["strategy not found"]}, status=status.HTTP_404_NOT_FOUND)
        if action == "start":
            strategy.is_active = True
            strategy.save(update_fields=["is_active", "updated_at"])
            run = StrategyRun.objects.create(
                strategy=strategy,
                mode="paper",
                strategy_version=f"{strategy.class_name}@live",
                parameters_snapshot=dict(strategy.parameters or {}),
                data_version="live",
            )
            return Response(StrategyRunSerializer(run).data, status=status.HTTP_201_CREATED)
        if action == "stop":
            strategy.is_active = False
            strategy.save(update_fields=["is_active", "updated_at"])
            StrategyRun.objects.filter(strategy=strategy, is_running=True).update(
                is_running=False, ended_at=timezone.now(), exit_reason="stopped via API"
            )
            return Response({"stopped": True})
        return Response(
            {"errors": ["action must be start|stop"]}, status=status.HTTP_400_BAD_REQUEST
        )


class SignalListView(APIView):
    """Signals with execution outcome (run/symbol filters)."""

    permission_classes = [IsAuthenticated, IsAccountOwnerForList]

    def get(self, request):
        account = _account_for(request.user)
        qs = (
            Signal.objects.filter(strategy_run__strategy__account=account)
            .select_related("instrument", "strategy_run__strategy")
            .order_by("-generated_at")
        )
        run_id = request.query_params.get("run")
        symbol = request.query_params.get("symbol")
        if run_id:
            qs = qs.filter(strategy_run_id=run_id)
        if symbol:
            qs = qs.filter(instrument__symbol=symbol.upper())
        return Response(SignalSerializer(qs[:200], many=True).data)


class RiskConfigView(APIView):
    """Read limits; patch value/percentage/enabled (validated + audited)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, rule_type=None):
        account = _account_for(request.user)
        if rule_type is not None:
            try:
                row = RiskConfig.objects.get(
                    account=account, strategy__isnull=True, rule_type=rule_type
                )
            except RiskConfig.DoesNotExist:
                return Response(
                    {"errors": [f"unknown rule {rule_type}"]},
                    status=status.HTTP_404_NOT_FOUND,
                )
            self.check_object_permissions(request, row)
            return Response(RiskConfigSerializer(row).data)
        rows = RiskConfig.objects.filter(account=account, strategy__isnull=True).order_by(
            "rule_type"
        )
        return Response(RiskConfigSerializer(rows, many=True).data)

    def patch(self, request, rule_type):
        from core.models.audit import AuditLog

        account = _account_for(request.user)
        try:
            row = RiskConfig.objects.get(
                account=account, strategy__isnull=True, rule_type=rule_type
            )
        except RiskConfig.DoesNotExist:
            return Response(
                {"errors": [f"unknown rule {rule_type}"]}, status=status.HTTP_404_NOT_FOUND
            )
        self.check_object_permissions(request, row)
        value = request.data.get("limit_value")
        pct = request.data.get("limit_percentage")
        enabled = request.data.get("is_enabled")
        if value is not None:
            value = Decimal(str(value))
            if value < 0:
                return Response(
                    {"errors": ["limit_value must be >= 0"]}, status=status.HTTP_400_BAD_REQUEST
                )
            row.limit_value = value
        if pct is not None:
            pct = Decimal(str(pct))
            if not (Decimal("0") <= pct <= Decimal("100")):
                return Response(
                    {"errors": ["limit_percentage must be 0-100"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            row.limit_percentage = pct
        if enabled is not None:
            row.is_enabled = bool(enabled)
        before = f"{row.rule_type} -> {row.limit_value}/{row.limit_percentage}/{row.is_enabled}"
        row.description = request.data.get("description", row.description)
        row.save()
        AuditLog.objects.create(
            action="CONFIG_CHANGE",
            user=request.user,
            account=account,
            entity_type="risk_config",
            entity_id=str(row.id),
            description=f"Risk limit updated: {before}",
            metadata={"rule": rule_type},
        )
        return Response(RiskConfigSerializer(row).data)


class RiskEventListView(APIView):
    """Recent risk decisions, rejections first."""

    permission_classes = [IsAuthenticated, IsAccountOwnerForList]

    def get(self, request):
        account = _account_for(request.user)
        qs = RiskEvent.objects.filter(account=account).order_by("-timestamp")
        action = request.query_params.get("action")
        if action:
            qs = qs.filter(action=action)
        return Response(RiskEventSerializer(qs[:200], many=True).data)


class KillSwitchView(APIView):
    """Halt everything now. Generous throttle: always reachable in panic."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [KillSwitchThrottle]

    def post(self, request):
        from risk.kill import kill_switch

        account = _account_for(request.user)
        reason = str(request.data.get("reason") or "triggered via API")
        summary = kill_switch(account=account, reason=reason)
        try:
            from alerts.service import notify_kill_switch

            notify_kill_switch(account, reason, summary)
        except Exception:  # noqa: BLE001 - alerts never break the halt path
            logger.exception("Kill-switch alert failed")
        return Response({"halted": True, "summary": summary})


class KillReleaseView(APIView):
    """Deliberate resume after a halt."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [KillSwitchThrottle]

    def post(self, request):
        from risk.kill import release_kill_switch

        account = _account_for(request.user)
        reason = str(request.data.get("reason") or "released via API")
        release_kill_switch(account=account, reason=reason)
        return Response({"released": True})


class ReportExportView(APIView):
    """Daily trade CSV (streamed, no memory blowup on big days)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        day = parse_date(request.query_params.get("date") or "")
        if day is None:
            return Response(
                {"errors": ["?date=YYYY-MM-DD is required"]}, status=status.HTTP_400_BAD_REQUEST
            )
        account = _account_for(request.user)
        trades = (
            Trade.objects.filter(account=account, executed_at__date=day)
            .select_related("instrument", "order")
            .order_by("executed_at")
        )

        _HEADER = (
            "trade_id,executed_at,symbol,side,quantity,price,value,"
            "brokerage,stt,exchange,sebi,gst,stamp,dp,total_charges,slippage\n"
        )

        def _rows():
            yield _HEADER
            for trade in trades.iterator():
                cells = [
                    trade.trade_id,
                    trade.executed_at.isoformat(),
                    trade.instrument.symbol,
                    trade.side,
                    str(trade.quantity),
                    str(trade.price),
                    str(trade.value),
                    str(trade.brokerage),
                    str(trade.stt),
                    str(trade.exchange_charges),
                    str(trade.sebi_charges),
                    str(trade.gst),
                    str(trade.stamp_duty),
                    str(trade.dp_charges),
                    str(trade.total_charges),
                    str(trade.slippage),
                ]
                yield ",".join(cells) + "\n"

        response = StreamingHttpResponse(_rows(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="trades-{day.isoformat()}.csv"'
        return response


class DataHealthView(APIView):
    """Feed status, per-symbol latency, and the latest quality verdict."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from market_data.models import DataQualityReport
        from market_data.universe import get_seed_universe

        from .streaming import symbol_statuses

        symbols_param = request.query_params.get("symbols", "")
        symbols = [s.strip().upper() for s in symbols_param.split(",") if s.strip()]
        if not symbols:
            symbols = [e.symbol for e in get_seed_universe()]
        report = DataQualityReport.objects.order_by("-report_date").first()
        return Response(
            {
                "provider": _provider_name(),
                "symbols": symbol_statuses(symbols),
                "quality": (
                    {
                        "date": report.report_date.isoformat(),
                        "is_suspect": report.is_suspect,
                        "bad_tick_ratio": report.bad_tick_ratio,
                        "notes": report.notes,
                    }
                    if report
                    else None
                ),
            }
        )


def _provider_name() -> str:
    from decouple import config

    return config("MARKET_DATA_PROVIDER", default="yahoo")


class PricesStreamView(APIView):
    """
    Server-sent events with latest prices (browser auto-reconnects).

    Finite chunks (12 events, ~60s) instead of an infinite stream so no
    worker is held forever; `?single=1` emits a single event (tests/health).
    """

    permission_classes = [IsAuthenticated]
    throttle_classes: list = []

    def get(self, request):
        from .streaming import price_events

        symbols_param = request.query_params.get("symbols", "")
        symbols = [s.strip().upper() for s in symbols_param.split(",") if s.strip()]
        if not symbols:
            return Response(
                {"errors": ["?symbols=A,B is required"]}, status=status.HTTP_400_BAD_REQUEST
            )
        # NOTE: the flag is `single`, not `once` — the request-sanitization
        # middleware flags `on\w+=` (event-handler heuristic) and `once=1`
        # trips it. Security control wins; the param name yields.
        single = request.query_params.get("single") == "1"

        def _stream():
            yield from price_events(symbols, once=single)

        response = StreamingHttpResponse(_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
