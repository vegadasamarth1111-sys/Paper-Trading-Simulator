"""
Paper Trading Simulator — Score provider (Phase 6, plan section 7)
=====================================================================
Reads the latest PUBLISHED ranking for a trade date. The two look-ahead
gates are enforced in a single query — callers cannot forget them:

1. score_date < trade date (yesterday's scores drive today, never today's)
2. features_as_of < trade date (features must predate the trade too)

A strategy holds one provider snapshot per trading day (cached by date),
so the store is hit once per day, never per bar or per tick. Models stay
out of the hot loop by construction.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class RankedSymbol:
    """One ranked row, with full provenance for the Signal record."""

    symbol: str
    score: Decimal
    model_version: str
    score_date: date
    features_as_of: date


@dataclass(frozen=True)
class Ranking:
    """Full published ranking for one trade date + horizon."""

    trade_date: date
    horizon: str
    score_date: date
    model_version: str
    ranked: tuple  # RankedSymbol, best-first


class ScoreProvider:
    """Read-only access to published rankings. No training, no fitting."""

    def __init__(self, horizon: str = "1d") -> None:
        from ml.models import Horizon

        if horizon not in Horizon.values:
            raise ValueError(f"unknown horizon {horizon!r}")
        self.horizon = horizon
        self._cache: dict[date, Ranking | None] = {}

    def for_date(self, trade_date: date) -> Ranking | None:
        """
        Latest ranking publishable for trade_date (cached per date).

        Returns None when nothing was published in time — the strategy
        then emits nothing instead of trading blind. None is data, not
        an error.
        """
        if trade_date in self._cache:
            return self._cache[trade_date]
        ranking = self._load(trade_date)
        self._cache[trade_date] = ranking
        return ranking

    def _load(self, trade_date: date) -> Ranking | None:
        from ml.models import ModelScore

        # Gate 1+2 in one query: only rows published AND featurized
        # strictly before the trade date are even visible.
        candidates = (
            ModelScore.objects.filter(horizon=self.horizon)
            .filter(score_date__lt=trade_date, features_as_of__lt=trade_date)
            .order_by("-score_date")
            .values_list("score_date", flat=True)
            .first()
        )
        if candidates is None:
            return None
        latest: date = candidates
        # Newest publication wins outright (no blending across versions —
        # blending would mix model vintages into one decision).
        rows = (
            ModelScore.objects.filter(horizon=self.horizon, score_date=latest)
            .order_by("-score", "symbol")
            .values("symbol", "score", "model_version", "score_date", "features_as_of")
        )
        ranked = tuple(
            RankedSymbol(
                symbol=row["symbol"],
                score=row["score"],
                model_version=row["model_version"],
                score_date=row["score_date"],
                features_as_of=row["features_as_of"],
            )
            for row in rows
        )
        if not ranked:
            return None
        versions = {row.model_version for row in ranked}
        return Ranking(
            trade_date=trade_date,
            horizon=self.horizon,
            score_date=latest,
            model_version=sorted(versions)[0] if len(versions) == 1 else "mixed",
            ranked=ranked,
        )

    def top(self, trade_date: date, n: int) -> list[RankedSymbol]:
        """Best N symbols for the date (empty when unpublished)."""
        ranking = self.for_date(trade_date)
        if ranking is None:
            return []
        return list(ranking.ranked[: max(n, 0)])

    def bottom(self, trade_date: date, n: int) -> list[RankedSymbol]:
        """Worst N symbols for the date (empty when unpublished)."""
        ranking = self.for_date(trade_date)
        if ranking is None:
            return []
        return list(ranking.ranked[-max(n, 0) :]) if n > 0 else []
