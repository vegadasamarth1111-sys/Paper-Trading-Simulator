"""Market-data URL routes (read-only, authenticated)."""

from django.urls import path

from .views import CandleListView, DataQualityView, LatestPriceView

urlpatterns = [
    path("latest/", LatestPriceView.as_view(), name="market-latest"),
    path("candles/", CandleListView.as_view(), name="market-candles"),
    path("quality/", DataQualityView.as_view(), name="market-quality"),
]
