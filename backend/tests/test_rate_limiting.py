"""
Tests for security.rate_limiting — brute-force and runaway-loop protection.

Throttling is disabled globally in the testing settings so that the rest of
the suite is not slowed down or made flaky. These tests therefore attach the
throttles explicitly to a throwaway view, which proves each one really blocks.
"""

from typing import Any

import pytest
from django.core.cache import cache
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from security.rate_limiting import (
    AuthRateThrottle,
    BurstRateThrottle,
    KillSwitchThrottle,
    OrderRateThrottle,
    SustainedRateThrottle,
)


class ThrottledView(APIView):
    """Minimal view that applies one throttle class."""

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    # DRF's as_view() only accepts kwargs that are already class attributes,
    # so the throttle is declared here and overridden per test via as_view().
    throttle = None

    def __init__(self, throttle):
        self.throttle_classes = [throttle]
        super().__init__()

    def get(self, request):
        return Response({"ok": True})


def _hammer(throttle, times):
    """Drive a throttled view `times` times and collect HTTP status codes."""
    factory = APIRequestFactory()
    view = ThrottledView.as_view(throttle=throttle)
    codes = []
    for _ in range(times):
        request = factory.get("/api/v1/throttled/")
        response = view(request)
        codes.append(response.status_code)
    return codes


@pytest.fixture(autouse=True)
def _clear_cache():
    """Throttles keep state in the cache; isolate every test."""
    cache.clear()
    yield
    cache.clear()


class TestThrottleConfiguration:
    def test_burst_uses_the_burst_scope(self):
        assert BurstRateThrottle.scope == "burst"

    def test_sustained_uses_the_sustained_scope(self):
        assert SustainedRateThrottle.scope == "sustained"

    def test_order_throttle_uses_the_orders_scope(self):
        assert OrderRateThrottle.scope == "orders"

    def test_auth_throttle_is_strict(self):
        """5 attempts per minute — enough for a human, not for a cracker."""
        assert AuthRateThrottle.rate == "5/min"

    def test_order_throttle_is_moderate(self):
        assert OrderRateThrottle.rate == "30/min"

    def test_kill_switch_remains_reachable(self):
        """You must always be able to trigger the kill switch."""
        assert KillSwitchThrottle.rate == "10/min"

    def test_auth_throttle_is_anonymous_based(self):
        """Brute-force protection must key on IP, not on (missing) user id."""
        from rest_framework.throttling import AnonRateThrottle

        assert isinstance(AuthRateThrottle(), AnonRateThrottle)


class TestAuthThrottleBehaviour:
    def test_requests_under_the_limit_pass(self):
        codes = _hammer(AuthRateThrottle, 5)
        assert codes == [status.HTTP_200_OK] * 5

    def test_request_over_the_limit_is_429(self):
        codes = _hammer(AuthRateThrottle, 6)
        assert codes[-1] == status.HTTP_429_TOO_MANY_REQUESTS
        assert codes[:-1] == [status.HTTP_200_OK] * 5

    def test_throttle_response_does_not_leak_internals(self):
        _hammer(AuthRateThrottle, 6)
        factory = APIRequestFactory()
        view = ThrottledView.as_view(throttle=AuthRateThrottle)
        response = view(factory.get("/api/v1/throttled/"))
        response.render()
        body = response.content.decode()
        assert "Traceback" not in body
        assert "127.0.0.1" not in body


class TestOrderThrottleBehaviour:
    def test_allows_normal_order_flow(self):
        """30 per minute is plenty for a human, and caps a runaway loop."""
        codes = _hammer(OrderRateThrottle, 30)
        assert codes == [status.HTTP_200_OK] * 30

    def test_caps_a_runaway_loop(self):
        codes = _hammer(OrderRateThrottle, 31)
        assert codes[-1] == status.HTTP_429_TOO_MANY_REQUESTS


class TestKillSwitchThrottle:
    def test_kill_switch_allows_ten_calls(self):
        codes = _hammer(KillSwitchThrottle, 10)
        assert codes == [status.HTTP_200_OK] * 10

    def test_kill_switch_rate_limits_only_rapid_toggles(self):
        codes = _hammer(KillSwitchThrottle, 11)
        assert codes[-1] == status.HTTP_429_TOO_MANY_REQUESTS
