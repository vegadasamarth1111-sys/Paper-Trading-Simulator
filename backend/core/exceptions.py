"""
Paper Trading Simulator — Custom Exceptions & DRF Error Handler
=================================================================
Centralised exception hierarchy. Every error has a code, so clients
can handle errors programmatically without parsing messages.
The custom DRF handler ensures raw stack traces NEVER leak to clients.
"""

import logging
import traceback

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger("core")
security_logger = logging.getLogger("security")


# =============================================================================
# BASE EXCEPTIONS
# =============================================================================


class PaperTradingError(Exception):
    """Base exception for all Paper Trading Simulator errors."""

    default_code = "PAPER_TRADING_ERROR"
    default_message = "An internal error occurred."

    def __init__(
        self, message: str | None = None, code: str | None = None, details: dict | None = None
    ):
        self.message = message or self.default_message
        self.code = code or self.default_code
        self.details = details or {}
        super().__init__(self.message)


# =============================================================================
# TRADING DOMAIN EXCEPTIONS
# =============================================================================


class OrderValidationError(PaperTradingError):
    """Order failed validation before reaching the broker."""

    default_code = "ORDER_VALIDATION_FAILED"
    default_message = "Order validation failed."


class InvalidOrderTransitionError(PaperTradingError):
    """Attempted an illegal state transition on an order."""

    default_code = "INVALID_ORDER_TRANSITION"
    default_message = "Invalid order state transition."


class InsufficientFundsError(PaperTradingError):
    """Not enough margin or capital for the operation."""

    default_code = "INSUFFICIENT_FUNDS"
    default_message = "Insufficient funds for this operation."


class RiskLimitBreachedError(PaperTradingError):
    """Operation blocked by a risk rule."""

    default_code = "RISK_LIMIT_BREACHED"
    default_message = "Risk limit breached. Operation blocked."


class KillSwitchActiveError(PaperTradingError):
    """Kill switch is active — all trading halted."""

    default_code = "KILL_SWITCH_ACTIVE"
    default_message = "Kill switch is active. All trading operations are halted."


class StaleDataError(PaperTradingError):
    """Market data is stale — trading blocked for safety."""

    default_code = "STALE_DATA"
    default_message = "Market data is stale. Trading blocked for this symbol."


class InstrumentNotFoundError(PaperTradingError):
    """Referenced instrument does not exist or is expired."""

    default_code = "INSTRUMENT_NOT_FOUND"
    default_message = "Instrument not found or expired."


class MarketClosedError(PaperTradingError):
    """Attempted to trade outside market hours."""

    default_code = "MARKET_CLOSED"
    default_message = "Market is closed. No trading allowed."


class DataValidationError(PaperTradingError):
    """Tick or candle data failed validation."""

    default_code = "DATA_VALIDATION_FAILED"
    default_message = "Market data failed validation checks."


class ReconciliationMismatchError(PaperTradingError):
    """End-of-day reconciliation found discrepancies."""

    default_code = "RECONCILIATION_MISMATCH"
    default_message = "Reconciliation mismatch detected."


class StrategyError(PaperTradingError):
    """Error within a trading strategy."""

    default_code = "STRATEGY_ERROR"
    default_message = "Strategy execution error."


class ConfigurationError(PaperTradingError):
    """Invalid system configuration."""

    default_code = "CONFIGURATION_ERROR"
    default_message = "Invalid system configuration."


# =============================================================================
# SECURITY EXCEPTIONS
# =============================================================================


class AuthenticationFailedError(PaperTradingError):
    """Authentication attempt failed."""

    default_code = "AUTHENTICATION_FAILED"
    default_message = "Authentication failed."


class PermissionDeniedError(PaperTradingError):
    """User does not have permission for this action."""

    default_code = "PERMISSION_DENIED"
    default_message = "You do not have permission to perform this action."


class RateLimitExceededError(PaperTradingError):
    """Too many requests."""

    default_code = "RATE_LIMIT_EXCEEDED"
    default_message = "Too many requests. Please try again later."


# =============================================================================
# CUSTOM DRF EXCEPTION HANDLER
# Never expose raw errors, stack traces, or internal details to clients.
# =============================================================================


def custom_exception_handler(exc, context):
    """
    Custom exception handler for Django REST Framework.

    Security rules:
    - NEVER expose stack traces to clients
    - NEVER expose database error details
    - Log full details server-side for debugging
    - Return sanitized, structured error responses
    """
    # Let DRF handle its own exceptions first
    response = exception_handler(exc, context)

    if response is not None:
        # Wrap DRF's response in our standard format
        response.data = {
            "success": False,
            "error": {
                "code": _get_error_code(exc),
                "message": _get_safe_message(exc, response),
            },
        }
        return response

    # Handle our custom exceptions
    if isinstance(exc, PaperTradingError):
        logger.warning(
            "Trading error: %s (code=%s, details=%s)",
            exc.message,
            exc.code,
            exc.details,
        )
        return Response(
            {
                "success": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details if exc.details else None,
                },
            },
            status=_get_http_status(exc),
        )

    # Unhandled exception — log the full traceback, return generic error
    logger.error(
        "Unhandled exception in %s: %s\n%s",
        context.get("view", "unknown"),
        str(exc),
        traceback.format_exc(),
    )
    security_logger.warning(
        "Unhandled exception suppressed from client response: %s", type(exc).__name__
    )

    return Response(
        {
            "success": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An internal error occurred. Please try again later.",
            },
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _get_error_code(exc) -> str:
    """Extract error code from exception."""
    if hasattr(exc, "code"):
        return str(exc.code) if exc.code else "UNKNOWN"
    if hasattr(exc, "default_code"):
        return exc.default_code
    return type(exc).__name__.upper()


def _get_safe_message(exc, response) -> str:
    """Get a client-safe error message."""
    if hasattr(exc, "detail"):
        detail = exc.detail
        if isinstance(detail, list):
            return "; ".join(str(d) for d in detail)
        if isinstance(detail, dict):
            messages = []
            for field, errors in detail.items():
                if isinstance(errors, list):
                    messages.append(f"{field}: {', '.join(str(e) for e in errors)}")
                else:
                    messages.append(f"{field}: {errors}")
            return "; ".join(messages)
        return str(detail)
    return "An error occurred."


def _get_http_status(exc: PaperTradingError) -> int:
    """Map custom exceptions to HTTP status codes."""
    status_map = {
        OrderValidationError: status.HTTP_400_BAD_REQUEST,
        InvalidOrderTransitionError: status.HTTP_409_CONFLICT,
        InsufficientFundsError: status.HTTP_400_BAD_REQUEST,
        RiskLimitBreachedError: status.HTTP_403_FORBIDDEN,
        KillSwitchActiveError: status.HTTP_503_SERVICE_UNAVAILABLE,
        StaleDataError: status.HTTP_503_SERVICE_UNAVAILABLE,
        InstrumentNotFoundError: status.HTTP_404_NOT_FOUND,
        MarketClosedError: status.HTTP_403_FORBIDDEN,
        DataValidationError: status.HTTP_400_BAD_REQUEST,
        AuthenticationFailedError: status.HTTP_401_UNAUTHORIZED,
        PermissionDeniedError: status.HTTP_403_FORBIDDEN,
        RateLimitExceededError: status.HTTP_429_TOO_MANY_REQUESTS,
        ConfigurationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    return status_map.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
