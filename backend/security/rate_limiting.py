"""
Paper Trading Simulator — Rate Limiting
=========================================
Custom throttle classes for DRF.
Different rates for different endpoint types:
- Auth endpoints: strict (prevent brute-force)
- Order endpoints: moderate (prevent runaway loops)
- Read endpoints: relaxed (support dashboard polling)
"""

from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class BurstRateThrottle(UserRateThrottle):
    """
    Short-term burst limiter for authenticated users.
    Prevents rapid-fire requests within a minute.
    Rate is set in settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['burst'].
    """

    scope = "burst"


class SustainedRateThrottle(UserRateThrottle):
    """
    Long-term sustained limiter for authenticated users.
    Prevents excessive usage over an hour.
    Rate is set in settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['sustained'].
    """

    scope = "sustained"


class AuthRateThrottle(AnonRateThrottle):
    """
    Strict rate limit for authentication endpoints (login, register).
    Prevents brute-force password attacks.
    5 attempts per minute by default.
    """

    rate = "5/min"


class OrderRateThrottle(UserRateThrottle):
    """
    Rate limit for order placement.
    Prevents runaway strategy loops from flooding the system.
    Configurable via RATE_LIMIT_ORDERS env var.
    """

    scope = "orders"
    rate = "30/min"


class KillSwitchThrottle(UserRateThrottle):
    """
    Kill switch has its own generous rate — you should always be able to
    trigger it, but we still prevent accidental rapid toggling.
    """

    rate = "10/min"
