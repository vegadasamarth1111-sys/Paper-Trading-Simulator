"""Market data application configuration."""

from django.apps import AppConfig


class MarketDataConfig(AppConfig):
    """Configuration for the market-data (Phase 1) application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "market_data"
    verbose_name = "Market Data"
