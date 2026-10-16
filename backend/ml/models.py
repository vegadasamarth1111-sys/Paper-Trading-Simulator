"""
Paper Trading Simulator — Ranking score store (Phase 6, plan section 7)
=========================================================================
Precomputed ranking scores land here — training happens in your existing
ML pipeline (XGBoost/LightGBM/BiLSTM), NOT in this service. Models never
run inside the tick loop: scores are published on a schedule (EOD for
1-day and 1-week horizons) and strategies read the latest PUBLISHED row.

Anti-look-ahead is structural: a score row carries BOTH the publication
date (score_date) and the newest feature timestamp (features_as_of).
The provider only serves rows with score_date < trade date AND
features_as_of < trade date. A score computed with Friday's close can
only drive Monday's signals, never Friday's.
"""

from django.db import models


class Horizon(models.TextChoices):
    """Forecast horizons the rankers publish for."""

    DAY_1 = "1d", "1 Day"
    WEEK_1 = "1w", "1 Week"


class ModelScore(models.Model):
    """
    One symbol's ranking score for one publication date and horizon.

    Immutable by convention: re-publishing the same (date, horizon,
    symbol, version) updates in place (idempotent ingest); history is
    the sequence of publication dates, never edits to old rows' scores.
    """

    id = models.BigAutoField(primary_key=True)
    score_date = models.DateField(
        db_index=True,
        help_text="Publication date (the evening the scores were produced)",
    )
    horizon = models.CharField(max_length=2, choices=Horizon.choices, db_index=True)
    symbol = models.CharField(max_length=50, db_index=True)
    score = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        help_text="Higher = better expected rank (model-specific scale)",
    )
    model_version = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Exact model version, e.g. xgb-rank@v3-2026-01-09-g1234abc",
    )
    features_as_of = models.DateField(
        help_text="Newest feature timestamp used (must predate score_date)",
    )
    source_file = models.CharField(
        max_length=300, blank=True, default="", help_text="Artifact the row was ingested from"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "model_scores"
        ordering = ["-score_date", "-score"]
        indexes = [
            models.Index(fields=["horizon", "score_date"]),
            models.Index(fields=["model_version", "score_date"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["score_date", "horizon", "symbol", "model_version"],
                name="uq_score_pub_horizon_symbol_version",
            ),
        ]

    def __str__(self):
        return f"{self.score_date} {self.horizon} {self.symbol}={self.score} [{self.model_version}]"
