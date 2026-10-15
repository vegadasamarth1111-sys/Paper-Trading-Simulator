"""Risk-engine application configuration (Phase 3)."""

from django.apps import AppConfig


class RiskEngineConfig(AppConfig):
    """Configuration for the risk-engine application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "risk"
    verbose_name = "Risk Engine"
