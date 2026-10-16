"""
Paper Trading Simulator — Streaming helpers (Phase 5)
=======================================================
Server-sent-event payload builders. No Django here — pure functions over
the market-data read path, so the same code serves the SSE view today
and a websocket handler tomorrow.
"""

import json
import logging
import time
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

logger = logging.getLogger("core")
UTC = ZoneInfo("UTC")

# Chunk length: 12 events x 5s ~= 60s per connection, then the browser
# reconnects (EventSource does this automatically). No worker is held
# forever, and reconnects re-authenticate.
CHUNK_EVENTS = 12
POLL_SECONDS = 5


def symbol_statuses(symbols: list[str]) -> list[dict]:
    """Per-symbol feed health: latest tick, age, live/stale verdict."""
    from market_data.services import get_latest_price

    now = datetime.now(UTC)
    out = []
    for symbol in symbols:
        quote = None
        try:
            quote = get_latest_price(symbol)
        except Exception:  # noqa: BLE001 - one bad symbol must not break the panel
            logger.warning("Health lookup failed for %s", symbol)
        if quote is None:
            out.append({"symbol": symbol, "status": "unknown", "age_seconds": None, "ltp": None})
            continue
        ts = quote["ts"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        age = (now - ts).total_seconds()
        out.append(
            {
                "symbol": symbol,
                "status": "live" if age <= 900 else "stale",
                "age_seconds": round(age, 1),
                "ltp": str(quote["ltp"]),
                "ts": ts.isoformat(),
            }
        )
    return out


def price_events(symbols: list[str], *, once: bool = False):
    """
    Yield SSE `data:` frames with the latest prices.

    Finite by design (CHUNK_EVENTS then StopIteration). `once=True`
    emits exactly one frame for tests and health checks.
    """
    from market_data.services import get_latest_price

    count = 0
    while True:
        payload: dict = {"ts": datetime.now(UTC).isoformat(), "prices": {}}
        for symbol in symbols:
            try:
                quote = get_latest_price(symbol)
            except Exception:  # noqa: BLE001 - gaps are data, not crashes
                quote = None
            payload["prices"][symbol] = (
                {"ltp": str(quote["ltp"]), "ts": quote["ts"].isoformat()} if quote else None
            )
        yield f"data: {json.dumps(payload)}\n\n"
        count += 1
        if once or count >= CHUNK_EVENTS:
            return
        time.sleep(POLL_SECONDS)


def as_decimal(value: object) -> Decimal:
    """Shared Decimal coercion for streaming-adjacent callers."""
    return Decimal(str(value))
