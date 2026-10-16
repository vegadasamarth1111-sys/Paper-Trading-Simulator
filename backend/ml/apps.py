"""ML scoring application configuration (Phase 6)."""

from django.apps import AppConfig


class MLConfig(AppConfig):
    """Configuration for the ML scoring application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "ml"
    verbose_name = "ML Scoring"
