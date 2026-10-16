"""
Paper Trading Simulator — Watchlist Models
============================================
User-curated symbol lists for the dashboard watchlist tab.
Symbols are validated against the instrument master on write.
"""

import uuid

from django.conf import settings
from django.db import models


class Watchlist(models.Model):
    """A named list of symbols owned by one user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="watchlists",
    )
    name = models.CharField(max_length=100, default="My Watchlist")
    symbols = models.JSONField(
        default=list,
        help_text='Ordered NSE symbols, e.g. ["RELIANCE", "NIFTY"]',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "watchlists"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"],
                name="uq_watchlist_user_name",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({len(self.symbols)} symbols)"

    def clean_symbols(self, valid: set) -> list[str]:
        """Uppercase, de-dupe, drop empties; raise on unknown symbols."""
        seen: list[str] = []
        for raw in self.symbols if isinstance(self.symbols, list) else []:
            symbol = str(raw).strip().upper()
            if symbol and symbol not in seen:
                seen.append(symbol)
        unknown = [s for s in seen if s not in valid]
        if unknown:
            raise ValueError(f"unknown symbols (not in instrument master): {unknown}")
        return seen[:100]  # free-tier sanity cap
