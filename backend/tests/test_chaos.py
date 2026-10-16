"""
Chaos tests (Phase 7, plan section 11 + exit gate).

Each test breaks one thing the way production breaks it — dead feed,
mid-day restart, duplicate delivery, ancient ticks, a DB failure mid-fill
— and proves the system lands in a correct, explainable state. Recovery,
not just survival: money must still reconcile afterwards.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from broker.fills import MarketSnapshot
from broker.ledger import reconcile_account
from broker.paper import PaperBroker
from core.clock import Clock, ClockMode, TradingCalendar
from core.enums import OrderStatus
from core.models import Order
from market_data.ingestion import PollingIngestor
from market_data.models import BadTick, Tick

UTC = ZoneInfo("UTC")
MON_10AM = datetime(2026, 1, 5, 4, 30, tzinfo=UTC)  # Monday 10:00 IST

pytestmark = pytest.mark.django_db


def _clock_at(ts=MON_10AM):
    clock = Clock(mode=ClockMode.LIVE)
    clock.now = lambda: ts  # type: ignore[method-assign]
    clock._calendar = TradingCalendar(holidays=set())
    return clock


def _frozen_broker(ts=MON_10AM):
    clock = Clock(mode=ClockMode.REPLAY)
    clock.set_time(ts)
    return PaperBroker(clock=clock)


def _tick(instrument, price="2500", vol=1000, ts=MON_10AM):
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


class TestFeedDeath:
    def test_outage_then_recovery(self, equity_instrument):
        """Feed dies mid-morning: errors + backoff, then polling resumes cleanly."""
        quotes = {"RELIANCE": {"ltp": Decimal("2500"), "ts": MON_10AM, "qty": 1, "volume": 100}}
        provider = MagicMock()
        provider.name = "yahoo"
        provider.fetch_ltp.side_effect = [
            ConnectionError("websocket down"),
            ConnectionError("still down"),
            quotes,  # feed back
        ]
        ingestor = PollingIngestor(provider, ["RELIANCE"], clock=_clock_at())
        first = ingestor.poll_once()
        second = ingestor.poll_once()
        assert first["errors"] and second["errors"]
        assert first["accepted"] == 0
        third = ingestor.poll_once()
        assert third["accepted"] == 1
        assert Tick.objects.filter(symbol="RELIANCE").count() == 1

    def test_heartbeat_names_the_silent(self, equity_instrument):
        provider = MagicMock()
        provider.name = "yahoo"
        provider.fetch_ltp.return_value = {
            "RELIANCE": {"ltp": Decimal("2500"), "ts": MON_10AM, "volume": 10}
        }
        ingestor = PollingIngestor(provider, ["RELIANCE"], clock=_clock_at(), stale_seconds=60)
        ingestor.poll_once()
        assert ingestor.silent_symbols(MON_10AM + timedelta(minutes=5)) == ["RELIANCE"]


class TestRestartMidday:
    def test_new_broker_continues_fills(self, account, equity_instrument):
        """Process dies after a partial fill; a fresh broker completes it."""
        _tick(equity_instrument, price="2500")
        broker_a = _frozen_broker()
        order = broker_a.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="LMT",
            quantity=10,
            price=Decimal("2400"),
            product_type="MIS",
            validity="DAY",
            client_order_id="restart-1",
            stop_loss_price=Decimal("2350"),
        )
        assert order.status == OrderStatus.OPEN
        del broker_a  # process death: all in-memory state gone

        broker_b = _frozen_broker()  # restart: state rebuilt purely from the DB
        px = Decimal("2399")
        broker_b.process_snapshot(
            equity_instrument,
            MarketSnapshot(bid=px, ask=px, ltp=px, bid_qty=1000, ask_qty=1000, volume=99999),
        )
        order.refresh_from_db()
        assert order.status == OrderStatus.FILLED
        account.refresh_from_db()  # reconcile reads stored state: refresh first
        assert reconcile_account(account).ok

    def test_new_ingestor_rebuilds_context(self, equity_instrument):
        """Restarted ingestor reloads previous-tick context from the DB."""
        _tick(equity_instrument, price="2500", vol=100)
        provider = MagicMock()
        provider.name = "yahoo"
        # LOWER volume than the DB row, later timestamp: only a validator
        # that reloaded prev_volume from the database can reject this.
        provider.fetch_ltp.return_value = {
            "RELIANCE": {
                "ltp": Decimal("2501"),
                "ts": MON_10AM + timedelta(seconds=60),
                "volume": 50,
            }
        }
        ingestor = PollingIngestor(provider, ["RELIANCE"], clock=_clock_at())
        summary = ingestor.poll_once()
        assert summary["accepted"] == 0
        assert summary["rejected"] == 1
        from market_data.models import BadTick as BadTickModel

        assert BadTickModel.objects.filter(symbol="RELIANCE", reason="VOL_DECREASE").exists()


class TestDuplicateDelivery:
    def test_replayed_batch_all_duplicates(self, equity_instrument):
        """The same raw batch delivered twice: zero new ticks, all flagged."""
        _tick(equity_instrument, price="2500", vol=100)
        provider = MagicMock()
        provider.name = "yahoo"
        provider.fetch_ltp.return_value = {
            "RELIANCE": {"ltp": Decimal("2500"), "ts": MON_10AM, "volume": 100}
        }
        ingestor = PollingIngestor(provider, ["RELIANCE"], clock=_clock_at())
        summary = ingestor.poll_once()
        assert summary["accepted"] == 0
        assert summary["rejected"] == 1
        assert BadTick.objects.filter(symbol="RELIANCE", reason="DUPLICATE").exists()
        assert Tick.objects.filter(symbol="RELIANCE").count() == 1

    def test_order_retry_is_idempotent(self, account, equity_instrument):
        _tick(equity_instrument)
        broker = _frozen_broker()
        kwargs = {
            "account": account,
            "instrument": equity_instrument,
            "side": "BUY",
            "order_type": "MKT",
            "quantity": 10,
            "product_type": "MIS",
            "validity": "DAY",
            "client_order_id": "retry-1",
            "stop_loss_price": Decimal("2400"),
        }
        first = broker.place_order(**kwargs)
        second = broker.place_order(**kwargs)
        assert first.id == second.id
        assert Order.objects.filter(client_order_id="retry-1").count() == 1


class TestStaleStorm:
    def test_all_quotes_stale_nothing_moves(self, account, equity_instrument):
        """Every quote ancient: rejected at ingestion, zero fills, zero drift."""
        _tick(equity_instrument, price="2500")
        ancient = MON_10AM - timedelta(hours=3)
        provider = MagicMock()
        provider.name = "yahoo"
        provider.fetch_ltp.return_value = {
            "RELIANCE": {"ltp": Decimal("2400"), "ts": ancient, "volume": 50}
        }
        ingestor = PollingIngestor(provider, ["RELIANCE"], clock=_clock_at())
        summary = ingestor.poll_once()
        assert summary["accepted"] == 0
        assert summary["rejected"] == 1
        assert BadTick.objects.filter(symbol="RELIANCE", reason="STALE_TS").exists()
        account.refresh_from_db()
        assert account.current_balance == Decimal("1000000.0000")
        assert reconcile_account(account).ok


class TestDbFailure:
    def test_fill_abort_leaves_no_trace(self, account, equity_instrument):
        """Trade write blows up mid-fill: the whole order rolls back cleanly."""
        _tick(equity_instrument)
        broker = _frozen_broker()
        with (
            patch("broker.ledger.record_fill_entries", side_effect=RuntimeError("db gone")),
            pytest.raises(RuntimeError, match="db gone"),
        ):
            broker.place_order(
                account=account,
                instrument=equity_instrument,
                side="BUY",
                order_type="MKT",
                quantity=10,
                product_type="MIS",
                validity="DAY",
                client_order_id="atomic-1",
                stop_loss_price=Decimal("2400"),
            )
        # Atomic rollback: no order row, no trades, no ledger entries.
        assert Order.objects.filter(client_order_id="atomic-1").count() == 0
        from core.models import AccountLedgerEntry, Trade

        assert Trade.objects.filter(account=account).count() == 0
        assert AccountLedgerEntry.objects.filter(account=account).count() == 0
        # Retry with the same idempotency key succeeds (nothing half-written).
        order = broker.place_order(
            account=account,
            instrument=equity_instrument,
            side="BUY",
            order_type="MKT",
            quantity=10,
            product_type="MIS",
            validity="DAY",
            client_order_id="atomic-1",
            stop_loss_price=Decimal("2400"),
        )
        assert order.status == OrderStatus.FILLED


class TestClockJump:
    def test_gap_counted_not_hidden(self, equity_instrument):
        """A 2-hour feed gap shows up in the quality report (exit-gate honesty)."""
        from market_data.quality import build_daily_report

        morning = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)
        _tick(equity_instrument, price="2500", vol=100, ts=morning)
        _tick(equity_instrument, price="2505", vol=300, ts=morning + timedelta(hours=2))
        report = build_daily_report(morning.date())
        assert report.gap_count >= 1
        assert report.gap_seconds_max >= 7000
