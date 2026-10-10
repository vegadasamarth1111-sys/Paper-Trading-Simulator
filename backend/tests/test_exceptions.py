"""
Tests for core.exceptions — the custom DRF exception handler.

The security requirement here is that raw exception details, stack traces and
database errors NEVER reach a client. These tests assert that explicitly.
"""

import pytest
from rest_framework import status
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied

from core.exceptions import (
    ConfigurationError,
    InsufficientFundsError,
    InvalidOrderTransitionError,
    KillSwitchActiveError,
    OrderValidationError,
    PaperTradingError,
    PermissionDeniedError,
    RateLimitExceededError,
    RiskLimitBreachedError,
    StaleDataError,
    custom_exception_handler,
)


def handle(exc, context=None):
    """Shorthand: run the handler and return (status_code, payload)."""
    response = custom_exception_handler(exc, context or {"view": "TestView"})
    assert response is not None
    return response.status_code, response.data


class TestCustomExceptions:
    def test_base_exception_defaults(self):
        exc = PaperTradingError()
        assert exc.code == "PAPER_TRADING_ERROR"
        assert exc.message
        assert exc.details == {}

    def test_base_exception_accepts_overrides(self):
        exc = PaperTradingError(message="custom", code="CUSTOM", details={"a": 1})
        assert (exc.message, exc.code, exc.details) == ("custom", "CUSTOM", {"a": 1})

    def test_subclasses_carry_their_own_codes(self):
        assert OrderValidationError().code == "ORDER_VALIDATION_FAILED"
        assert InvalidOrderTransitionError().code == "INVALID_ORDER_TRANSITION"
        assert InsufficientFundsError().code == "INSUFFICIENT_FUNDS"
        assert KillSwitchActiveError().code == "KILL_SWITCH_ACTIVE"
        assert RiskLimitBreachedError().code == "RISK_LIMIT_BREACHED"
        assert StaleDataError().code == "STALE_DATA"
        assert RateLimitExceededError().code == "RATE_LIMIT_EXCEEDED"
        assert PermissionDeniedError().code == "PERMISSION_DENIED"


class TestHttpStatusMapping:
    @pytest.mark.parametrize(
        "exc,expected_status",
        [
            (OrderValidationError(), status.HTTP_400_BAD_REQUEST),
            (InvalidOrderTransitionError(), status.HTTP_409_CONFLICT),
            (InsufficientFundsError(), status.HTTP_400_BAD_REQUEST),
            (RiskLimitBreachedError(), status.HTTP_403_FORBIDDEN),
            (KillSwitchActiveError(), status.HTTP_503_SERVICE_UNAVAILABLE),
            (StaleDataError(), status.HTTP_503_SERVICE_UNAVAILABLE),
            (RateLimitExceededError(), status.HTTP_429_TOO_MANY_REQUESTS),
            (PermissionDeniedError(), status.HTTP_403_FORBIDDEN),
            (ConfigurationError(), status.HTTP_500_INTERNAL_SERVER_ERROR),
        ],
    )
    def test_mapping(self, exc, expected_status):
        code, _ = handle(exc)
        assert code == expected_status

    def test_unknown_subclass_defaults_to_500(self):
        class WeirdError(PaperTradingError):
            pass

        code, _ = handle(WeirdError())
        assert code == status.HTTP_500_INTERNAL_SERVER_ERROR


class TestResponseShape:
    def test_successful_result_is_reported_as_failure_payload(self):
        _, data = handle(RiskLimitBreachedError(message="too big"))
        assert data["success"] is False
        assert data["error"]["code"] == "RISK_LIMIT_BREACHED"
        assert data["error"]["message"] == "too big"

    def test_details_are_included_when_present(self):
        _, data = handle(InvalidOrderTransitionError(details={"current": "NEW"}))
        assert data["error"]["details"] == {"current": "NEW"}

    def test_details_are_null_when_absent(self):
        _, data = handle(OrderValidationError())
        assert data["error"]["details"] is None


class TestUnhandledExceptionsAreSanitized:
    def test_generic_500_for_unexpected_exception(self):
        code, data = handle(RuntimeError("DB password is hunter2"))
        assert code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert data["error"]["code"] == "INTERNAL_ERROR"

    def test_internal_message_never_leaks_to_client(self):
        """The whole point: no stack traces, no exception text in the payload."""
        _, data = handle(ValueError("secret-table-name-xyz"))
        assert "secret-table-name-xyz" not in str(data)
        assert data["error"]["message"] == ("An internal error occurred. Please try again later.")

    def test_exception_instance_text_is_not_leaked(self):
        _, data = handle(KeyError("some/internal/key"))
        assert "some/internal/key" not in str(data)


class TestDrfExceptionsAreWrapped:
    def test_drf_permission_denied_is_wrapped(self):
        code, data = handle(DRFPermissionDenied())
        assert code == status.HTTP_403_FORBIDDEN
        assert data["success"] is False
        # DRF's own default_code is preserved verbatim.
        assert data["error"]["code"] == "permission_denied"
        assert data["error"]["message"]
