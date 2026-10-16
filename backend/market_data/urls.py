"""Market-data URL routes (read-only, authenticated)."""

from django.urls import path

from .overview import ExpiriesView, IndicesView, OptionChainView, OverviewView
from .views import CandleListView, DataQualityView, LatestPriceView

urlpatterns = [
    path("latest/", LatestPriceView.as_view(), name="market-latest"),
    path("candles/", CandleListView.as_view(), name="market-candles"),
    path("quality/", DataQualityView.as_view(), name="market-quality"),
    path("overview/", OverviewView.as_view(), name="market-overview"),
    path("indices/", IndicesView.as_view(), name="market-indices"),
    path("expiries/", ExpiriesView.as_view(), name="market-expiries"),
    path("option-chain/", OptionChainView.as_view(), name="market-option-chain"),
]
