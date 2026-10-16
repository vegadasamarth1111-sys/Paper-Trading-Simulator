"""
Paper Trading Simulator — Score ingestion (Phase 6)
======================================================
Loads publisher artifacts (your pipeline's output) into ModelScore rows.

Accepted formats (by extension):
- .csv with header `symbol,score` (one row per symbol, strings/numbers)
- .json as a list of {"symbol": ..., "score": ...}

Publication metadata comes from flags, not file content (content is
data, flags are the publisher's signed claim):
  --model-version (required, e.g. xgb-rank@v3-2026-01-09-g1234abc)
  --score-date    (required, publication evening YYYY-MM-DD)
  --horizon       (1d or 1w)
  --features-as-of (required, newest feature timestamp YYYY-MM-DD;
                    must be <= score_date, else the file is rejected —
                    future-dated features are a look-ahead factory)

Idempotent: re-ingesting the same file updates rows in place (keyed by
the unique publication triple + version), never duplicates.
"""

import csv
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path


def parse_score_file(path: str | Path) -> list[tuple[str, Decimal]]:
    """Read (symbol, score) pairs. Malformed rows raise, never skip silently."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"score file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _parse_csv(path)
    if suffix == ".json":
        return _parse_json(path)
    raise ValueError(f"unsupported score format {suffix!r} (use .csv or .json)")


def _parse_csv(path: Path) -> list[tuple[str, Decimal]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or {"symbol", "score"} - set(reader.fieldnames):
            raise ValueError(f"{path}: CSV needs a `symbol,score` header")
        rows = list(reader)
    if not rows:
        raise ValueError(f"{path}: no score rows")
    return [(_clean_symbol(r["symbol"], path), _clean_score(r["score"], path)) for r in rows]


def _parse_json(path: Path) -> list[tuple[str, Decimal]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"{path}: expected a non-empty list of {{symbol, score}}")
    out = []
    for i, entry in enumerate(payload):
        if not isinstance(entry, dict) or "symbol" not in entry or "score" not in entry:
            raise ValueError(f"{path}: row {i} needs symbol and score")
        out.append((_clean_symbol(entry["symbol"], path), _clean_score(entry["score"], path)))
    return out


def _clean_symbol(value: object, path: Path) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol:
        raise ValueError(f"{path}: blank symbol")
    return symbol


def _clean_score(value: object, path: Path) -> Decimal:
    if isinstance(value, float):
        # Scores arrive as text; a float here means the publisher
        # serialized imprecisely — refuse rather than guess.
        raise ValueError(f"{path}: score {value!r} must be a JSON string or int, not float")
    try:
        score = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"{path}: bad score {value!r}: {exc}") from exc
    if not score.is_finite():
        raise ValueError(f"{path}: non-finite score {value!r}")
    return score


def ingest_scores(
    *,
    path: str | Path,
    model_version: str,
    score_date: date,
    horizon: str,
    features_as_of: date,
) -> dict:
    """Validate metadata + file, then upsert rows. Returns a summary."""
    from ml.models import Horizon, ModelScore

    if not model_version.strip():
        raise ValueError("model_version is required (every score needs provenance)")
    if horizon not in Horizon.values:
        raise ValueError(f"unknown horizon {horizon!r}")
    if features_as_of > score_date:
        raise ValueError(
            f"features_as_of ({features_as_of}) is after score_date ({score_date}): "
            "scores cannot be published before their features exist"
        )
    pairs = parse_score_file(path)
    if len({symbol for symbol, _ in pairs}) != len(pairs):
        raise ValueError(f"{path}: duplicate symbols in one publication")
    created = updated = 0
    for symbol, score in pairs:
        _, was_created = ModelScore.objects.update_or_create(
            score_date=score_date,
            horizon=horizon,
            symbol=symbol,
            model_version=model_version.strip(),
            defaults={
                "score": score,
                "features_as_of": features_as_of,
                "source_file": str(path),
            },
        )
        created += was_created
        updated += not was_created
    return {
        "symbols": len(pairs),
        "created": created,
        "updated": updated,
        "model_version": model_version.strip(),
    }
