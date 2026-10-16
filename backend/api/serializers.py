"""
Paper Trading Simulator — API serializers (Phase 5)
======================================================
Decimals serialize as strings (DRF COERCE_DECIMAL_TO_STRING default),
so paise-precision survives JSON without float mangling. Inputs accept
strings or numbers and are converted to Decimal by the field.
"""

from decimal import Decimal

from rest_framework import serializers

from core.enums import OrderSide, OrderType, OrderValidity, ProductType
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


class AccountSerializer(serializers.ModelSerializer):
    """Balance, margin, equity, drawdown — the money header."""

    available_margin = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    total_equity = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    drawdown = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True)

    class Meta:
        model = Account
        fields = [
            "id",
            "name",
            "initial_capital",
            "current_balance",
            "margin_used",
            "available_margin",
            "realized_pnl",
            "unrealized_pnl",
            "total_equity",
            "peak_balance",
            "drawdown",
            "is_active",
            "kill_switch_active",
        ]
        read_only_fields = fields


class InstrumentRefSerializer(serializers.ModelSerializer):
    """Compact instrument identity for nested order/trade rows."""

    class Meta:
        model = Instrument
        fields = ["symbol", "exchange", "segment", "lot_size", "tick_size"]


class OrderPlaceSerializer(serializers.Serializer):
    """Placement input. Resolved to an instrument + account by the view."""

    symbol = serializers.CharField(max_length=50)
    side = serializers.ChoiceField(choices=OrderSide.choices)
    order_type = serializers.ChoiceField(choices=OrderType.choices)
    quantity = serializers.IntegerField(min_value=1)
    price = serializers.DecimalField(
        max_digits=18, decimal_places=4, required=False, min_value=Decimal("0.0001")
    )
    trigger_price = serializers.DecimalField(
        max_digits=18, decimal_places=4, required=False, min_value=Decimal("0.0001")
    )
    product_type = serializers.ChoiceField(choices=ProductType.choices, default=ProductType.MIS)
    validity = serializers.ChoiceField(choices=OrderValidity.choices, default=OrderValidity.DAY)
    target_price = serializers.DecimalField(max_digits=18, decimal_places=4, required=False)
    stoploss_price = serializers.DecimalField(max_digits=18, decimal_places=4, required=False)
    stop_loss_price = serializers.DecimalField(max_digits=18, decimal_places=4, required=False)
    signal_id = serializers.CharField(max_length=100, required=False, allow_blank=True)

    def validate(self, data):
        order_type = data["order_type"]
        if order_type in (OrderType.LIMIT, OrderType.STOP_LOSS) and "price" not in data:
            raise serializers.ValidationError(f"{order_type} requires a limit price.")
        if (
            order_type in (OrderType.STOP_LOSS, OrderType.STOP_LOSS_MARKET)
            and "trigger_price" not in data
        ):
            raise serializers.ValidationError(f"{order_type} requires a trigger price.")
        return data


class OrderModifySerializer(serializers.Serializer):
    """Only price, trigger, and quantity may change on an OPEN order."""

    price = serializers.DecimalField(
        max_digits=18, decimal_places=4, required=False, min_value=Decimal("0.0001")
    )
    trigger_price = serializers.DecimalField(
        max_digits=18, decimal_places=4, required=False, min_value=Decimal("0.0001")
    )
    quantity = serializers.IntegerField(min_value=1, required=False)

    def validate(self, data):
        if not data:
            raise serializers.ValidationError("Nothing to modify.")
        return data


class OrderSerializer(serializers.ModelSerializer):
    """Full order state for history and detail views."""

    instrument = InstrumentRefSerializer(read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "client_order_id",
            "instrument",
            "side",
            "order_type",
            "product_type",
            "validity",
            "quantity",
            "filled_quantity",
            "pending_quantity",
            "price",
            "trigger_price",
            "average_fill_price",
            "status",
            "rejection_reason",
            "total_charges",
            "is_bracket_order",
            "target_price",
            "stoploss_price",
            "placed_at",
            "filled_at",
            "cancelled_at",
        ]
        read_only_fields = fields


class TradeSerializer(serializers.ModelSerializer):
    """Immutable fills, itemized charges included."""

    instrument = InstrumentRefSerializer(read_only=True)

    class Meta:
        model = Trade
        fields = [
            "id",
            "trade_id",
            "instrument",
            "side",
            "quantity",
            "price",
            "value",
            "brokerage",
            "stt",
            "exchange_charges",
            "sebi_charges",
            "gst",
            "stamp_duty",
            "dp_charges",
            "total_charges",
            "slippage",
            "is_adjustment",
            "executed_at",
        ]
        read_only_fields = fields


class PositionSerializer(serializers.ModelSerializer):
    """Open positions with live MTM (refreshed on read)."""

    instrument = InstrumentRefSerializer(read_only=True)

    class Meta:
        model = Position
        fields = [
            "id",
            "instrument",
            "side",
            "product_type",
            "quantity",
            "average_entry_price",
            "last_price",
            "realized_pnl",
            "unrealized_pnl",
            "total_charges",
            "margin_used",
            "is_open",
            "opened_at",
        ]
        read_only_fields = fields


class StrategySerializer(serializers.ModelSerializer):
    """Strategy definitions with live run state."""

    is_running = serializers.SerializerMethodField()

    class Meta:
        model = Strategy
        fields = [
            "id",
            "name",
            "class_name",
            "description",
            "allocated_capital",
            "parameters",
            "is_active",
            "is_running",
        ]
        read_only_fields = ["id", "is_running"]

    def get_is_running(self, obj) -> bool:
        return obj.runs.filter(is_running=True).exists()


class StrategyRunSerializer(serializers.ModelSerializer):
    """Versioned run records (reproducibility proof)."""

    strategy_name = serializers.CharField(source="strategy.name", read_only=True)

    class Meta:
        model = StrategyRun
        fields = [
            "id",
            "strategy_name",
            "mode",
            "strategy_version",
            "parameters_snapshot",
            "data_version",
            "model_version",
            "is_running",
            "started_at",
            "ended_at",
            "exit_reason",
            "realized_pnl",
            "trades_count",
        ]
        read_only_fields = fields


class SignalSerializer(serializers.ModelSerializer):
    """Signals with execution outcome (executed vs skip reason)."""

    instrument = InstrumentRefSerializer(read_only=True)

    class Meta:
        model = Signal
        fields = [
            "id",
            "instrument",
            "direction",
            "entry_price",
            "stop_loss",
            "target",
            "confidence",
            "model_version",
            "was_executed",
            "skip_reason",
            "generated_at",
        ]
        read_only_fields = fields


class RiskConfigSerializer(serializers.ModelSerializer):
    """Readable limits; writes validated (non-negative, sane percentages)."""

    rule_display = serializers.CharField(source="get_rule_type_display", read_only=True)

    class Meta:
        model = RiskConfig
        fields = [
            "id",
            "rule_type",
            "rule_display",
            "is_enabled",
            "limit_value",
            "limit_percentage",
            "description",
            "updated_at",
        ]
        read_only_fields = ["id", "rule_type", "rule_display", "updated_at"]


class RiskEventSerializer(serializers.ModelSerializer):
    """Recent rejections first (audit trail for the risk panel)."""

    rule_display = serializers.CharField(source="get_rule_type_display", read_only=True)

    class Meta:
        model = RiskEvent
        fields = [
            "id",
            "rule_type",
            "rule_display",
            "action",
            "rule_limit",
            "actual_value",
            "message",
            "timestamp",
        ]
        read_only_fields = fields
