"""Trading API URL routes (all authenticated, all account-scoped)."""

from django.urls import path

from .views import (
    AccountMoneyView,
    AccountView,
    DataHealthView,
    KillReleaseView,
    KillSwitchView,
    OrderDetailView,
    OrderListCreateView,
    PnLView,
    PositionListView,
    PricesStreamView,
    ReportExportView,
    RiskConfigView,
    RiskEventListView,
    SignalListView,
    StrategyControlView,
    StrategyListView,
    TradeListView,
    WatchlistDetailView,
    WatchlistListCreateView,
)

urlpatterns = [
    path("account/", AccountView.as_view(), name="api-account"),
    path("account/<str:action>/", AccountMoneyView.as_view(), name="api-account-money"),
    path("orders/", OrderListCreateView.as_view(), name="api-orders"),
    path("orders/<uuid:order_id>/", OrderDetailView.as_view(), name="api-order-detail"),
    path("trades/", TradeListView.as_view(), name="api-trades"),
    path("positions/", PositionListView.as_view(), name="api-positions"),
    path("pnl/", PnLView.as_view(), name="api-pnl"),
    path("strategies/", StrategyListView.as_view(), name="api-strategies"),
    path(
        "strategies/<uuid:strategy_id>/<str:action>/",
        StrategyControlView.as_view(),
        name="api-strategy-control",
    ),
    path("signals/", SignalListView.as_view(), name="api-signals"),
    path("risk/", RiskConfigView.as_view(), name="api-risk"),
    path("risk/<str:rule_type>/", RiskConfigView.as_view(), name="api-risk-rule"),
    path("risk-events/", RiskEventListView.as_view(), name="api-risk-events"),
    path("watchlists/", WatchlistListCreateView.as_view(), name="api-watchlists"),
    path(
        "watchlists/<uuid:watchlist_id>/",
        WatchlistDetailView.as_view(),
        name="api-watchlist-detail",
    ),
    path("kill-switch/", KillSwitchView.as_view(), name="api-kill"),
    path("kill-switch/release/", KillReleaseView.as_view(), name="api-kill-release"),
    path("reports/daily.csv", ReportExportView.as_view(), name="api-report-csv"),
    path("data-health/", DataHealthView.as_view(), name="api-data-health"),
    path("stream/prices/", PricesStreamView.as_view(), name="api-stream"),
]
