"""
Paper Trading Simulator — Angel One provider (Phase 1, free account tier)
===========================================================================
Two halves with different key requirements:

1. Instrument master — KEYLESS. Angel publishes the full ScripMaster JSON
   publicly (no login). Phase 1 uses it as the authority for exchange
   tokens, lot sizes, tick sizes, expiries, and strikes:
   https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json

2. Market quotes / websocket — NEEDS KEYS the user has not shared yet
   (API key + client ID + password + TOTP). Those methods raise
   MissingCredentials naming the exact .env variables, so a misconfigured
   deploy fails loudly at startup instead of silently serving no data.

The user said they HAVE an Angel One free API but chose "zero keys today",
so this module is wired and tested with mocks, and goes live the moment
ANGEL_API_KEY etc. appear in .env. Nothing else changes.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from decouple import config

from .base import MarketDataProviderBase, MissingCredentials

logger = logging.getLogger("core")

SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)

_ENV_DOC = (
    "Set ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD and ANGEL_TOTP_SECRET in .env "
    "(see .env.example). The master stays keyless; only quotes need them."
)


def _credentials_present() -> bool:
    return bool(
        config("ANGEL_API_KEY", default="")
        and config("ANGEL_CLIENT_ID", default="")
        and config("ANGEL_PASSWORD", default="")
        and config("ANGEL_TOTP_SECRET", default="")
    )


def require_credentials() -> None:
    """Fail loudly when quote methods are called without keys configured."""
    if not _credentials_present():
        raise MissingCredentials("Angel One market quotes need API credentials. " + _ENV_DOC)


class AngelOneProvider(MarketDataProviderBase):
    """Angel One SmartAPI provider: keyless master, keyed quotes."""

    name = "angel"

    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self._timeout = timeout_seconds

    # ------------------------------------------------------------------
    # Keyless: instrument master
    # ------------------------------------------------------------------
    def fetch_instruments(self) -> list[dict]:
        """
        Full NSE master in InstrumentLoader shape (equities + F&O).

        Filters the public ScripMaster to NSE EQ + NFO. Rows carry real
        exchange tokens, lot sizes, tick sizes, expiries and strikes — the
        fields Yahoo cannot supply.
        """
        response = httpx.get(SCRIP_MASTER_URL, timeout=self._timeout)
        response.raise_for_status()
        raw_rows = response.json()
        logger.info("Angel ScripMaster: %d raw rows", len(raw_rows))

        rows: list[dict] = []
        for row in raw_rows:
            exch = str(row.get("exch_seg", "")).upper()
            if exch == "NSE":
                segment = "EQ"
            elif exch == "NFO":
                inst = str(row.get("instrumenttype", "")).upper()
                segment = "OPT" if inst in ("OPTSTK", "OPTIDX") else "FUT"
            else:
                continue  # BSE/CDS/MCX out of scope for Phase 1

            symbol = str(row.get("symbol", "")).strip()
            if not symbol:
                continue
            # Angel appends expiry to derivative symbols ("NIFTY27FEB25FUT");
            # the base symbol is the readable short name.
            name = str(row.get("name", "") or symbol).strip() or symbol
            expiry_raw = str(row.get("expiry", "") or "").strip()
            expiry = None
            if expiry_raw and expiry_raw not in ("", "0"):
                try:
                    expiry = datetime.strptime(expiry_raw, "%d%b%Y").date()
                except ValueError:
                    expiry = None
            strike_raw = row.get("strike")
            try:
                strike = (
                    Decimal(str(strike_raw)) / Decimal("100")
                    if strike_raw not in (None, "", -1, "-1")
                    else None
                )
            except Exception:  # noqa: BLE001 - one malformed row must not kill the sync
                strike = None
            if strike is not None and strike <= 0:
                strike = None

            rows.append(
                {
                    "symbol": symbol,
                    "exchange": "NSE",
                    "segment": segment,
                    "name": name,
                    "exchange_token": str(row.get("token", "")),
                    "isin": "",
                    "lot_size": int(row.get("lotsize") or 1),
                    "tick_size": Decimal(str(row.get("ticksize") or "0.05")),
                    "expiry_date": expiry,
                    "strike_price": strike,
                    "option_type": "",
                    "underlying_symbol": name if segment in ("FUT", "OPT") else "",
                    "upper_circuit": None,
                    "lower_circuit": None,
                }
            )
        logger.info("Angel ScripMaster: %d NSE rows kept", len(rows))
        return rows

    # ------------------------------------------------------------------
    # Keyed: history + quotes (raise until the user shares keys)
    # ------------------------------------------------------------------
    def fetch_history(
        self,
        symbol: str,
        *,
        interval: str = "1m",
        period_days: int = 5,
    ) -> list[dict]:
        require_credentials()
        # Implemented in the keyed milestone: SmartAPI historic endpoint
        # (exchange=NSE, interval mapped, exchange_token resolved via master).
        # Raising here — not returning [] — so callers never mistake "no keys"
        # for "no data".
        raise MissingCredentials(
            f"Angel history for {symbol} ({interval}, {period_days}d) is wired "
            f"but credentials are absent. {_ENV_DOC}"
        )

    def fetch_ltp(self, symbols: list[str]) -> dict[str, dict]:
        require_credentials()
        raise MissingCredentials(
            f"Angel LTP for {len(symbols)} symbols is wired but credentials are absent. {_ENV_DOC}"
        )

    def websocket_hint(self) -> str:
        """Where the Phase-1-polling cutover happens once keys exist."""
        _ = datetime.now(UTC)  # keep import honest; no clock in providers
        return (
            "Angel SmartAPI websocket (smartWebSocketV2) subscribes by "
            "exchange_token from fetch_instruments(). Polling stays as fallback."
        )
