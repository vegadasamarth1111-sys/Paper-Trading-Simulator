"""
Paper Trading Simulator — ML Celery tasks (Phase 6)
=====================================================
The nightly drop-directory scan: new publisher artifacts in ML_SCORE_DIR
are ingested automatically (filename carries the metadata). Training
itself stays in your pipeline — this service only ever READS scores.
"""

import logging

from celery import shared_task

logger = logging.getLogger("core")


@shared_task(name="ml.refresh_scores")
def refresh_scores_task() -> dict:
    """
    Ingest new score files from the drop directory.

    Expected filename convention (documented, enforced):
      scores_<horizon>_<score_date>_<features_as_of>_<model_version>.<csv|json>
    e.g. scores_1d_2026-01-09_2026-01-08_xgb-rank@v3.csv
    Files failing validation are LEFT IN PLACE and reported — a bad
    artifact must be visible, never silently skipped or deleted.
    """
    from pathlib import Path

    from decouple import config

    from ml.ingest import ingest_scores

    drop = Path(config("ML_SCORE_DIR", default="ml_scores"))
    ingested: list[dict] = []
    failed: list[dict] = []
    if not drop.is_dir():
        logger.info("Score drop directory %s absent; nothing to ingest", drop)
        return {"ingested": [], "failed": [], "note": "no drop directory"}
    for path in sorted(drop.iterdir()):
        if path.suffix.lower() not in (".csv", ".json") or not path.is_file():
            continue
        try:
            meta = _parse_filename(path.name)
        except ValueError as exc:
            failed.append({"file": path.name, "error": str(exc)})
            continue
        try:
            summary = ingest_scores(path=path, **meta)
            ingested.append({"file": path.name, **summary})
        except ValueError as exc:
            failed.append({"file": path.name, "error": str(exc)})
    if failed:
        logger.warning("Score ingest failures: %s", failed)
    return {"ingested": ingested, "failed": failed}


def _parse_filename(name: str) -> dict:
    """scores_<horizon>_<score_date>_<features_as_of>_<version>.<ext>"""
    from datetime import date

    stem, dot, _ = name.rpartition(".")
    if not dot:
        raise ValueError(f"{name}: missing extension")
    parts = stem.split("_", 4)
    if len(parts) != 5 or parts[0] != "scores":
        raise ValueError(
            f"{name}: expected "
            "scores_<horizon>_<score_date>_<features_as_of>_<model_version>.<csv|json>"
        )
    _, horizon, score_date, features_as_of, model_version = parts
    try:
        score_d = date.fromisoformat(score_date)
        features_d = date.fromisoformat(features_as_of)
    except ValueError as exc:
        raise ValueError(f"{name}: bad dates: {exc}") from exc
    return {
        "model_version": model_version,
        "score_date": score_d,
        "horizon": horizon,
        "features_as_of": features_d,
    }
