"""
Paper Trading Simulator — Clock Abstraction
=============================================
Single source of time for the entire system.
Rule: NEVER call datetime.now() directly in trading logic.

Three modes:
- LIVE:     Returns real wall-clock time (UTC)
- REPLAY:   Returns simulated time driven by recorded tick timestamps
- BACKTEST: Returns simulated time advanced by the backtest engine

The same trading code runs identically in all three modes because
it only ever asks the Clock what time it is.
"""

import threading
from datetime import datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

# IST timezone for market-hour calculations.
#
# NOTE: we deliberately use stdlib `zoneinfo` rather than `pytz`. With pytz,
# writing `datetime(..., tzinfo=IST)` silently yields LMT (+05:53) instead of
# IST (+05:30), because pytz requires `IST.localize(naive)`. In a trading system
# that 23-minute skew would silently mis-classify market hours. ZoneInfo is
# correct when used directly as `tzinfo`, so that mistake is impossible here.
IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


class ClockMode(Enum):
    """Operating mode of the clock."""

    LIVE = "live"
    REPLAY = "replay"
    BACKTEST = "backtest"


class TradingCalendar:
    """
    NSE/BSE trading calendar.
    Determines whether the market is open at a given time.

    Holidays are loaded from the database (instruments.models.TradingHoliday),
    not hard-coded. This class provides the logic; data comes from outside.
    """

    MARKET_OPEN = time(9, 15)  # IST
    MARKET_CLOSE = time(15, 30)  # IST
    PRE_OPEN_START = time(9, 0)  # IST
    PRE_OPEN_END = time(9, 15)  # IST

    # Auto square-off window (mimics real broker behavior)
    AUTO_SQUAREOFF_START = time(15, 15)  # IST
    AUTO_SQUAREOFF_END = time(15, 20)  # IST

    def __init__(self, holidays: set[datetime] | None = None):
        """
        Args:
            holidays: Set of holiday dates (date objects, no times).
                      Loaded from DB at startup and refreshed daily.
        """
        self._holidays: set = holidays or set()
        self._lock = threading.Lock()

    def update_holidays(self, holidays: set[datetime]) -> None:
        """Thread-safe holiday list update."""
        with self._lock:
            self._holidays = {h.date() if isinstance(h, datetime) else h for h in holidays}

    def is_trading_day(self, dt: datetime) -> bool:
        """Check if a given datetime falls on a trading day (not weekend, not holiday)."""
        ist_dt = self._to_ist(dt)
        date_only = ist_dt.date()

        # Weekends
        if ist_dt.weekday() >= 5:  # Saturday=5, Sunday=6
            return False

        # Holidays
        with self._lock:
            if date_only in self._holidays:
                return False

        return True

    def is_market_open(self, dt: datetime) -> bool:
        """Check if the market is currently open for regular trading."""
        if not self.is_trading_day(dt):
            return False

        ist_dt = self._to_ist(dt)
        current_time = ist_dt.time()
        return self.MARKET_OPEN <= current_time <= self.MARKET_CLOSE

    def is_pre_open(self, dt: datetime) -> bool:
        """Check if we're in the pre-open session."""
        if not self.is_trading_day(dt):
            return False

        ist_dt = self._to_ist(dt)
        current_time = ist_dt.time()
        return self.PRE_OPEN_START <= current_time < self.PRE_OPEN_END

    def is_squareoff_window(self, dt: datetime) -> bool:
        """Check if we're in the auto square-off window for intraday positions."""
        if not self.is_trading_day(dt):
            return False

        ist_dt = self._to_ist(dt)
        current_time = ist_dt.time()
        return self.AUTO_SQUAREOFF_START <= current_time <= self.AUTO_SQUAREOFF_END

    def next_market_open(self, dt: datetime) -> datetime:
        """Find the next market open time from a given datetime."""
        ist_dt = self._to_ist(dt)
        candidate = ist_dt.replace(
            hour=self.MARKET_OPEN.hour,
            minute=self.MARKET_OPEN.minute,
            second=0,
            microsecond=0,
        )

        # If we're past today's open, start from tomorrow
        if ist_dt.time() >= self.MARKET_OPEN:
            candidate += timedelta(days=1)

        # Skip weekends and holidays
        while not self.is_trading_day(candidate):
            candidate += timedelta(days=1)

        return candidate.astimezone(UTC)

    @staticmethod
    def _to_ist(dt: datetime) -> datetime:
        """Convert any datetime to IST. Assumes naive datetimes are UTC."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(IST)


class Clock:
    """
    Single source of time for the trading system.

    Usage:
        clock = Clock(mode=ClockMode.LIVE)
        now = clock.now()          # UTC datetime
        clock.is_market_open()     # Checks against trading calendar

    In replay/backtest mode:
        clock = Clock(mode=ClockMode.REPLAY)
        clock.set_time(some_past_datetime)  # Advance time manually
        now = clock.now()                    # Returns the set time
    """

    def __init__(
        self,
        mode: ClockMode = ClockMode.LIVE,
        calendar: TradingCalendar | None = None,
    ):
        self._mode = mode
        self._calendar = calendar or TradingCalendar()
        self._simulated_time: datetime | None = None
        self._lock = threading.Lock()

    @property
    def mode(self) -> ClockMode:
        """Current clock mode."""
        return self._mode

    @property
    def calendar(self) -> TradingCalendar:
        """Access the trading calendar."""
        return self._calendar

    def now(self) -> datetime:
        """
        Get the current time (always UTC).

        In LIVE mode: real wall-clock time.
        In REPLAY/BACKTEST mode: the last time set via set_time().
        """
        if self._mode == ClockMode.LIVE:
            return datetime.now(UTC)

        with self._lock:
            if self._simulated_time is None:
                raise RuntimeError(
                    f"Clock is in {self._mode.value} mode but no simulated time has been set. "
                    f"Call clock.set_time() before using clock.now()."
                )
            return self._simulated_time

    def set_time(self, dt: datetime) -> None:
        """
        Set the simulated time (replay/backtest mode only).
        Raises RuntimeError if called in LIVE mode.
        """
        if self._mode == ClockMode.LIVE:
            raise RuntimeError("Cannot set time in LIVE mode. The clock uses real wall-clock time.")

        # Ensure timezone-aware UTC
        dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)

        with self._lock:
            # In replay mode, time must only move forward
            if (
                self._mode == ClockMode.REPLAY
                and self._simulated_time is not None
                and dt < self._simulated_time
            ):
                raise ValueError(
                    f"In REPLAY mode, time cannot move backwards. "
                    f"Current: {self._simulated_time}, Attempted: {dt}"
                )
            self._simulated_time = dt

    def advance(self, delta: timedelta) -> datetime:
        """
        Advance simulated time by a delta (backtest mode).
        Returns the new time.
        """
        if self._mode == ClockMode.LIVE:
            raise RuntimeError("Cannot advance time in LIVE mode.")

        with self._lock:
            if self._simulated_time is None:
                raise RuntimeError("Set an initial time before advancing.")
            self._simulated_time += delta
            return self._simulated_time

    def is_market_open(self) -> bool:
        """Check if the market is currently open."""
        return self._calendar.is_market_open(self.now())

    def is_pre_open(self) -> bool:
        """Check if the pre-open session is active."""
        return self._calendar.is_pre_open(self.now())

    def is_squareoff_window(self) -> bool:
        """Check if the auto square-off window is active."""
        return self._calendar.is_squareoff_window(self.now())

    def is_trading_day(self) -> bool:
        """Check if today is a trading day."""
        return self._calendar.is_trading_day(self.now())

    def to_ist(self) -> datetime:
        """Get current time converted to IST (for display only)."""
        return self.now().astimezone(IST)

    def __repr__(self) -> str:
        return f"Clock(mode={self._mode.value}, time={self.now().isoformat()})"
