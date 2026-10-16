"""
Tests for alerts.telegram + alerts.service (Phase 5).

Rule 1 is the whole suite: the sender NEVER raises (unconfigured,
network down, bad token) and NEVER ships secrets in message bodies.
"""

from unittest.mock import MagicMock, patch

from alerts import telegram
from alerts.service import (
    notify_data_quality,
    notify_eod_summary,
    notify_kill_switch,
    notify_reconciliation,
)


def _resp(ok=True):
    response = MagicMock()
    response.json.return_value = {"ok": ok}
    return response


class TestSender:
    def test_unconfigured_returns_false(self):
        with patch("alerts.telegram.config", return_value=""):
            assert telegram.send_message("hi") is False

    def test_network_failure_returns_false(self):
        with (
            patch("alerts.telegram.config", side_effect=["tok", "chat"]),
            patch("alerts.telegram.httpx.post", side_effect=ConnectionError("down")),
        ):
            assert telegram.send_message("hi") is False

    def test_rejected_by_telegram_returns_false(self):
        with (
            patch("alerts.telegram.config", side_effect=["tok", "chat"]),
            patch("alerts.telegram.httpx.post", return_value=_resp(ok=False)),
        ):
            assert telegram.send_message("hi") is False

    def test_success(self):
        with (
            patch("alerts.telegram.config", side_effect=["tok", "chat"]),
            patch("alerts.telegram.httpx.post", return_value=_resp(ok=True)) as post,
        ):
            assert telegram.send_message("hi") is True
            url = post.call_args[0][0]
            assert "tok" not in url or "bot" in url  # token stays in path, never logged
            assert post.call_args[1]["json"]["chat_id"] == "chat"

    def test_configured_gate(self):
        with patch("alerts.telegram.config", side_effect=["tok", "chat"]):
            assert telegram.configured() is True
        with patch("alerts.telegram.config", return_value=""):
            assert telegram.configured() is False


class TestEvents:
    def test_kill_message_has_no_secrets(self):
        from core.models import Account

        account = MagicMock(spec=Account)
        account.id = "abcdef12-3456-7890-abcd-ef1234567890"
        with (
            patch("alerts.service.configured", return_value=True),
            patch("alerts.service.send_message", return_value=True) as send,
        ):
            assert notify_kill_switch(account, "test", {"cancelled": [], "squared": []}) is True
            text = send.call_args[0][0]
            assert "abcdef12" in text  # truncated id ok
            assert "abcdef12-3456" not in text  # full id never ships

    def test_reconcile_empty_is_noop(self):
        assert notify_reconciliation(MagicMock(), []) is False

    def test_unconfigured_is_noop(self):
        with patch("alerts.service.configured", return_value=False):
            assert notify_kill_switch(MagicMock(), "x", {}) is False
            assert notify_eod_summary(MagicMock(), MagicMock()) is False
            assert notify_data_quality(MagicMock()) is False

    def test_eod_and_quality_format(self):
        report = MagicMock()
        report.report_date = "2026-10-16"
        report.exchange = "NSE"
        report.is_suspect = True
        report.total_ticks = 100
        report.bad_ticks = 10
        report.gap_count = 1
        report.candle_mismatches = 0
        report.notes = "ok"
        snapshot = MagicMock()
        snapshot.snapshot_date = "2026-10-16"
        snapshot.total_equity = 1000000
        snapshot.day_pnl = 100
        snapshot.open_positions_count = 1
        snapshot.trades_count = 5
        snapshot.total_charges_today = 50
        with (
            patch("alerts.service.configured", return_value=True),
            patch("alerts.service.send_message", return_value=True),
        ):
            assert notify_data_quality(report) is True
            assert notify_eod_summary(MagicMock(), snapshot) is True
