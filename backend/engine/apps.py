"""Strategy-engine application configuration (Phase 4)."""

from django.apps import AppConfig


class EngineConfig(AppConfig):
    """Configuration for the strategy engine application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "engine"
    verbose_name = "Strategy Engine"
