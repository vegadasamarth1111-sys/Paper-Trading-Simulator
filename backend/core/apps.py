"""Core application configuration."""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Configuration for the core domain models application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core Domain"
