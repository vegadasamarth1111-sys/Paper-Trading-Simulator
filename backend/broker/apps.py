"""Paper-broker application configuration (Phase 2)."""

from django.apps import AppConfig


class BrokerConfig(AppConfig):
    """Configuration for the paper-broker engine application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "broker"
    verbose_name = "Paper Broker"
