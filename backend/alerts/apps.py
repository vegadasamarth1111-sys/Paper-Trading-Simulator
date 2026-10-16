"""Alerts application configuration (Phase 5, Telegram)."""

from django.apps import AppConfig


class AlertsConfig(AppConfig):
    """Configuration for the alerts application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "alerts"
    verbose_name = "Alerts"
