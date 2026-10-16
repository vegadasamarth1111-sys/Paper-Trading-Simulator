"""
Tests for ml ingest + provider (Phase 6).

The headline property: scores published or featurized on/after the trade
date are INVISIBLE to that date. Both gates are tested independently.
"""

from datetime import date
from decimal import Decimal

import pytest

from ml.ingest import ingest_scores, parse_score_file
from ml.models import ModelScore
from ml.provider import ScoreProvider

pytestmark = pytest.mark.django_db

PUB = date(2026, 1, 8)  # Thursday evening publication
FRI = date(2026, 1, 9)  # trade date


def _csv(tmp_path, rows):
    path = tmp_path / "scores.csv"
    path.write_text("symbol,score\n" + "\n".join(f"{s},{v}" for s, v in rows))
    return path


def _publish(symbols=("RELIANCE", "TCS", "INFY"), version="xgb-rank@v1"):
    for i, symbol in enumerate(symbols):
        ModelScore.objects.create(
            score_date=PUB,
            horizon="1d",
            symbol=symbol,
            score=Decimal(f"0.{9 - i}"),
            model_version=version,
            features_as_of=date(2026, 1, 7),
        )


class TestIngest:
    def test_csv_and_json(self, tmp_path):
        csv_path = _csv(tmp_path, [("RELIANCE", "0.9"), ("TCS", "0.8")])
        summary = ingest_scores(
            path=csv_path,
            model_version="v1",
            score_date=PUB,
            horizon="1d",
            features_as_of=date(2026, 1, 7),
        )
        assert summary == {"symbols": 2, "created": 2, "updated": 0, "model_version": "v1"}

        json_path = tmp_path / "scores.json"
        json_path.write_text('[{"symbol": "INFY", "score": "0.7"}]')
        summary = ingest_scores(
            path=json_path,
            model_version="v1",
            score_date=PUB,
            horizon="1d",
            features_as_of=date(2026, 1, 7),
        )
        assert summary["created"] == 1

    def test_idempotent_reingest(self, tmp_path):
        csv_path = _csv(tmp_path, [("RELIANCE", "0.9")])
        kwargs = {
            "path": csv_path,
            "model_version": "v1",
            "score_date": PUB,
            "horizon": "1d",
            "features_as_of": date(2026, 1, 7),
        }
        assert ingest_scores(**kwargs)["created"] == 1
        second = ingest_scores(**kwargs)
        assert (second["created"], second["updated"]) == (0, 1)

    def test_future_features_rejected(self, tmp_path):
        csv_path = _csv(tmp_path, [("RELIANCE", "0.9")])
        with pytest.raises(ValueError, match="features_as_of"):
            ingest_scores(
                path=csv_path,
                model_version="v1",
                score_date=PUB,
                horizon="1d",
                features_as_of=date(2026, 1, 9),
            )

    def test_float_scores_rejected(self, tmp_path):
        json_path = tmp_path / "scores.json"
        json_path.write_text('[{"symbol": "RELIANCE", "score": 0.9}]')
        with pytest.raises(ValueError, match="not float"):
            ingest_scores(
                path=json_path,
                model_version="v1",
                score_date=PUB,
                horizon="1d",
                features_as_of=date(2026, 1, 7),
            )

    def test_duplicates_and_garbage_rejected(self, tmp_path):
        dup = _csv(tmp_path, [("RELIANCE", "0.9"), ("RELIANCE", "0.8")])
        with pytest.raises(ValueError, match="duplicate"):
            ingest_scores(
                path=dup,
                model_version="v1",
                score_date=PUB,
                horizon="1d",
                features_as_of=date(2026, 1, 7),
            )
        bad = tmp_path / "scores.txt"
        bad.write_text("nope")
        with pytest.raises(ValueError, match="unsupported"):
            parse_score_file(bad)
        with pytest.raises(ValueError, match="not found"):
            parse_score_file(tmp_path / "missing.csv")

    def test_empty_version_rejected(self, tmp_path):
        csv_path = _csv(tmp_path, [("RELIANCE", "0.9")])
        with pytest.raises(ValueError, match="model_version"):
            ingest_scores(
                path=csv_path,
                model_version="  ",
                score_date=PUB,
                horizon="1d",
                features_as_of=date(2026, 1, 7),
            )


class TestProvider:
    def test_top_and_order(self):
        _publish()
        provider = ScoreProvider(horizon="1d")
        top = provider.top(FRI, 2)
        assert [r.symbol for r in top] == ["RELIANCE", "TCS"]
        assert top[0].model_version == "xgb-rank@v1"
        bottom = provider.bottom(FRI, 1)
        assert [r.symbol for r in bottom] == ["INFY"]

    def test_same_day_publication_invisible(self):
        """Scores published ON the trade date cannot drive it (gate 1)."""
        _publish()
        assert ScoreProvider().for_date(PUB) is None

    def test_future_features_invisible(self):
        """Scores featurized on/after the trade date cannot drive it (gate 2)."""
        ModelScore.objects.create(
            score_date=date(2026, 1, 7),
            horizon="1d",
            symbol="RELIANCE",
            score=Decimal("0.99"),
            model_version="sneaky-v1",
            features_as_of=FRI,  # features from the trade day itself
        )
        assert ScoreProvider().for_date(FRI) is None

    def test_unpublished_is_silence_not_error(self):
        assert ScoreProvider().for_date(FRI) is None
        assert ScoreProvider().top(FRI, 3) == []

    def test_newest_publication_wins(self):
        _publish(version="v1")
        _publish(version="v2")  # same PUB date? no — separate date below
        ModelScore.objects.filter(model_version="v2").update(score_date=date(2026, 1, 9))
        ranking = ScoreProvider().for_date(date(2026, 1, 12))
        assert ranking is not None
        assert ranking.model_version == "v2"

    def test_unknown_horizon_raises(self):
        with pytest.raises(ValueError, match="horizon"):
            ScoreProvider(horizon="1h")

    def test_cached_per_date(self):
        _publish()
        provider = ScoreProvider()
        first = provider.for_date(FRI)
        ModelScore.objects.all().delete()
        assert provider.for_date(FRI) is first  # cache, not a re-query


class TestTasksAndCommand:
    def test_refresh_task_ingests_and_reports_bad(self, tmp_path, monkeypatch):
        from ml.tasks import _parse_filename, refresh_scores_task

        drop = tmp_path / "drop"
        drop.mkdir()
        (drop / "scores_1d_2026-01-08_2026-01-07_xgb-rank@v3.csv").write_text(
            "symbol,score\nRELIANCE,0.91\nTCS,0.82\n"
        )
        (drop / "bogus.csv").write_text("symbol,score\nRELIANCE,0.5\n")
        (drop / "notes.txt").write_text("ignored")
        monkeypatch.setenv("ML_SCORE_DIR", str(drop))
        result = refresh_scores_task()
        assert len(result["ingested"]) == 1
        assert result["ingested"][0]["symbols"] == 2
        assert any(f["file"] == "bogus.csv" for f in result["failed"])
        assert ModelScore.objects.filter(model_version="xgb-rank@v3").count() == 2
        with pytest.raises(ValueError, match="expected scores_"):
            _parse_filename("bogus.csv")

    def test_refresh_task_no_directory(self, tmp_path, monkeypatch):
        from ml.tasks import refresh_scores_task

        monkeypatch.setenv("ML_SCORE_DIR", str(tmp_path / "absent"))
        assert refresh_scores_task()["ingested"] == []

    def test_load_scores_command(self, tmp_path):
        from io import StringIO

        from django.core.management import call_command

        csv_path = _csv(tmp_path, [("RELIANCE", "0.9")])
        out = StringIO()
        call_command(
            "load_scores",
            "--file",
            str(csv_path),
            "--model-version",
            "cmd-v1",
            "--score-date",
            "2026-01-08",
            "--horizon",
            "1d",
            "--features-as-of",
            "2026-01-07",
            stdout=out,
        )
        assert "1 symbols" in out.getvalue()
        assert ModelScore.objects.filter(model_version="cmd-v1").count() == 1
