"""
Tests for the live-paper trial harness (Phase 8).

The harness must be honest in both directions: a clean trial reads
PASS (once time-served), and each acceptance criterion has a failing
counterpart. Overall PASS is structurally unreachable before min_days.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command

from broker.trial import compare_with_replay, evaluate_acceptance, run_trial_day
from core.models import TrialDay

UTC = ZoneInfo("UTC")
DAY = date(2026, 1, 5)  # a Monday

pytestmark = pytest.mark.django_db


def _instrument(symbol="RELIANCE"):
    from core.models import Instrument

    return Instrument.objects.create(
        symbol=symbol,
        exchange="NSE",
        segment="EQ",
        name=symbol,
        exchange_token="1",
        lot_size=1,
        tick_size=Decimal("0.05"),
        effective_from=date(2024, 1, 1),
    )


def _tick(instrument, price="2500", vol=1000, day=DAY, hour=4, minute=30):
    from market_data.models import Tick

    ts = datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
    return Tick.objects.create(
        instrument=instrument,
        symbol=instrument.symbol,
        exchange="NSE",
        exchange_ts=ts,
        received_ts=ts,
        ltp=Decimal(price),
        volume_traded=vol,
        source="yahoo",
    )


def _clean_day(day=DAY):
    """A trial day with ticks and nothing else (no trading, no drift)."""
    return run_trial_day(day)


class TestTrialDaily:
    def test_records_evidence_row(self, account):
        _tick(_instrument())
        row = _clean_day()
        assert row.ticks_total == 1
        assert row.bad_ticks == 0
        assert row.reconcile_ok is True
        assert row.unexplained_rejects == 0

    def test_idempotent_rerun(self, account):
        _clean_day()
        first = TrialDay.objects.get(trial_date=DAY)
        _clean_day()
        assert TrialDay.objects.filter(trial_date=DAY).count() == 1
        assert TrialDay.objects.get(trial_date=DAY).id == first.id

    def test_counts_bad_ticks(self, account):
        from market_data.models import BadTick

        _clean_day()
        BadTick.objects.create(
            symbol="RELIANCE",
            exchange="NSE",
            reason="STALE_TS",
            details="old",
            raw_payload={},
            source="yahoo",
        )
        # BadTick created_at is real-now, outside the trial day: move it in.
        BadTick.objects.update(created_at=datetime(2026, 1, 5, 5, 0, tzinfo=UTC))
        row = _clean_day()
        assert row.bad_ticks == 1


class TestAcceptance:
    def _pass_state(self, days=3):
        for i in range(days):
            TrialDay.objects.create(
                trial_date=DAY + timedelta(days=i),
                ticks_total=100,
                reconcile_ok=True,
                divergence_pct=Decimal("2.5000"),
                fills=5,
                total_charges=Decimal("100.0000"),
                restarts=1 if i == 0 else 0,
            )

    def test_empty_trial_is_in_progress(self):
        report = evaluate_acceptance(min_days=30)
        assert report.overall == "IN PROGRESS"
        assert report.days == 0
        assert all(c.status == "PENDING" for c in report.criteria if c.name != "trial_length")
        assert [c for c in report.criteria if c.name == "trial_length"][0].status == "PENDING"

    def test_full_pass_state(self):
        self._pass_state(days=30)
        report = evaluate_acceptance(min_days=30)
        assert report.overall == "PASS", [(c.name, c.status, c.detail) for c in report.criteria]

    def test_short_trial_cannot_pass(self):
        self._pass_state(days=29)
        report = evaluate_acceptance(min_days=30)
        assert report.overall == "IN PROGRESS"

    def test_mismatch_fails(self):
        self._pass_state(days=30)
        row = TrialDay.objects.first()
        row.reconcile_ok = False
        row.reconcile_mismatches = 2
        row.save()
        report = evaluate_acceptance(min_days=30)
        assert report.overall == "FAIL"
        assert [c for c in report.criteria if c.name == "zero_reconciliation_mismatches"][
            0
        ].status == "FAIL"

    def test_crash_fails(self):
        self._pass_state(days=30)
        row = TrialDay.objects.first()
        row.crash_count = 1
        row.save()
        report = evaluate_acceptance(min_days=30)
        assert report.overall == "FAIL"

    def test_unexplained_fails(self):
        self._pass_state(days=30)
        row = TrialDay.objects.first()
        row.unexplained_rejects = 1
        row.save()
        report = evaluate_acceptance(min_days=30)
        assert report.overall == "FAIL"

    def test_divergence_over_tolerance_fails(self):
        self._pass_state(days=30)
        row = TrialDay.objects.first()
        row.divergence_pct = Decimal("99.0000")
        row.save()
        report = evaluate_acceptance(min_days=30)
        assert report.overall == "FAIL"

    def test_restart_pending_until_observed(self):
        self._pass_state(days=30)
        TrialDay.objects.update(restarts=0)
        report = evaluate_acceptance(min_days=30)
        assert [c for c in report.criteria if c.name == "restart_recovery_verified"][
            0
        ].status == "PENDING"
        row = TrialDay.objects.first()
        row.restarts = 1
        row.save()
        report = evaluate_acceptance(min_days=30)
        assert [c for c in report.criteria if c.name == "restart_recovery_verified"][
            0
        ].status == "PASS"


class TestDivergence:
    def _candles(self, symbol="RELIANCE", n=30):
        from market_data.models import Candle

        inst = _instrument(symbol)
        for i in range(n):
            ts = datetime(2026, 1, 5, 4, 30, tzinfo=UTC) + timedelta(minutes=i)
            px = Decimal("20") + i * Decimal("0.1")
            Candle.objects.create(
                instrument=inst,
                symbol=symbol,
                exchange="NSE",
                timeframe="1m",
                open_ts=ts,
                open_price=px,
                high_price=px + 1,
                low_price=px - 1,
                close_price=px,
                volume=100,
                trades=1,
                is_complete=True,
                source="yahoo",
            )
        return inst

    def test_no_data_is_na(self):
        assert compare_with_replay(DAY, "sma_cross", ["RELIANCE"]) is None

    def test_flat_paper_and_replay_agree(self, account):
        self._candles()
        # No paper trading happened: replay of a quiet market agrees at 0.
        result = compare_with_replay(DAY, "donchian", ["RELIANCE"])
        assert result == Decimal("0.0000")


class TestCommands:
    def test_trial_report_command(self):
        out = StringIO()
        call_command("trial_report", stdout=out)
        assert "Overall after 0 days: IN PROGRESS" in out.getvalue()

    def test_trial_note_command(self):
        out = StringIO()
        call_command("trial_note", "--date", "2026-01-05", "--crash", stdout=out)
        row = TrialDay.objects.get(trial_date=DAY)
        assert row.crash_count == 1
        call_command("trial_note", "--date", "2026-01-05", "--restart", stdout=StringIO())
        row.refresh_from_db()
        assert row.restarts == 1
        call_command("trial_note", "--date", "2026-01-05", "--note", "all calm", stdout=StringIO())
        row.refresh_from_db()
        assert "all calm" in row.operator_notes

    def test_trial_note_requires_one_flag(self):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match="exactly one"):
            call_command("trial_note", stdout=StringIO())

    def test_trial_daily_command(self, account):
        _instrument()
        out = StringIO()
        call_command("trial_daily", "--date", "2026-01-05", stdout=out)
        assert "Trial day recorded" in out.getvalue()
        assert TrialDay.objects.filter(trial_date=DAY).exists()
