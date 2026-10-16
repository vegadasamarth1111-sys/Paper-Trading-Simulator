"""
Paper Trading Simulator — Telegram sender (Phase 5)
=====================================================
Free Bot API (create a bot with @BotFather, paste the token into .env).
Two hard rules:

1. NEVER raise into the caller. Alerts observe trading; a dead network
   must not break fills, kills, or settlements. Every failure is logged
   and returns False.
2. NEVER send secrets. Messages carry counts, symbols, and verdicts —
   never tokens, keys, or full account identifiers.
"""

import logging

import httpx
from decouple import config

logger = logging.getLogger("core")

_API = "https://api.telegram.org/bot{token}/sendMessage"


def configured() -> bool:
    """True when a token AND a chat are present (elsewhere: silent no-op)."""
    return bool(config("TELEGRAM_BOT_TOKEN", default="")) and bool(
        config("TELEGRAM_CHAT_ID", default="")
    )


def send_message(text: str, *, chat_id: str | None = None, timeout_seconds: float = 10.0) -> bool:
    """
    Send one Telegram message. Returns True on delivery, False otherwise
    (unconfigured, network down, bad token) — never raises.
    """
    token = config("TELEGRAM_BOT_TOKEN", default="")
    target = chat_id or config("TELEGRAM_CHAT_ID", default="")
    if not token or not target:
        logger.debug("Telegram unconfigured; alert dropped (not an error)")
        return False
    try:
        response = httpx.post(
            _API.format(token=token),
            json={"chat_id": target, "text": text[:4000], "parse_mode": "Markdown"},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok", False):
            logger.warning("Telegram rejected message: %s", body)
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - rule 1: alerts never raise
        logger.warning("Telegram send failed: %s", exc)
        return False
