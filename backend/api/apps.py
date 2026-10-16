"""Trading API application configuration (Phase 5)."""

from django.apps import AppConfig


class ApiConfig(AppConfig):
    """Configuration for the trading API application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "api"
    verbose_name = "Trading API"
