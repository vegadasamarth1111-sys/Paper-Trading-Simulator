"""
Paper Trading Simulator — Security Middleware
===============================================
Three custom middleware layers:
1. SecurityHeadersMiddleware  — CSP, anti-XSS, anti-clickjacking headers
2. AdminIPWhitelistMiddleware — Restrict /admin/ to specific IPs
3. RequestSanitizationMiddleware — Strip/flag suspicious input patterns
"""

import logging
import re

from django.conf import settings
from django.http import HttpResponseForbidden, JsonResponse

logger = logging.getLogger("security")


class SecurityHeadersMiddleware:
    """
    Add security headers to every response.
    These supplement Django's built-in SecurityMiddleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Prevent MIME-type sniffing
        response["X-Content-Type-Options"] = "nosniff"

        # XSS protection (legacy browsers)
        response["X-XSS-Protection"] = "1; mode=block"

        # Prevent embedding in iframes (clickjacking)
        response["X-Frame-Options"] = "DENY"

        # Referrer policy
        response["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions policy (restrict browser features)
        response["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), "
            "gyroscope=(), magnetometer=(), microphone=(), "
            "payment=(), usb=()"
        )

        # Cache control for API responses (prevent caching sensitive data)
        if request.path.startswith("/api/"):
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response["Pragma"] = "no-cache"

        # Remove server header (information disclosure)
        if "Server" in response:
            del response["Server"]

        return response


class AdminIPWhitelistMiddleware:
    """
    Restrict access to the Django admin panel by IP address.
    In development, an empty whitelist allows all IPs.
    In production, only whitelisted IPs can reach /admin/.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.whitelist = set(getattr(settings, "ADMIN_IP_WHITELIST", []))

    def __call__(self, request):
        if request.path.startswith("/admin/"):
            # If whitelist is empty (dev mode), allow all
            if not self.whitelist:
                return self.get_response(request)

            client_ip = self._get_client_ip(request)

            if client_ip not in self.whitelist:
                logger.warning(
                    "Admin access denied for IP: %s (path: %s)",
                    client_ip,
                    request.path,
                )
                return HttpResponseForbidden(
                    "Access denied. Your IP is not authorized to access this resource."
                )

        return self.get_response(request)

    @staticmethod
    def _get_client_ip(request) -> str:
        """
        Extract client IP, accounting for reverse proxies.
        X-Forwarded-For can be spoofed, so this should be used
        in conjunction with a trusted proxy configuration.
        """
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            # Take the first IP (client IP when behind a trusted proxy)
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")


class RequestSanitizationMiddleware:
    """
    Inspect incoming requests for common attack patterns.
    This is a defense-in-depth layer — DRF serializers handle primary validation.

    Actions:
    - Log suspicious patterns (SQL injection, XSS attempts)
    - Block requests with obviously malicious payloads
    - Strip null bytes from all string values
    """

    # Patterns that indicate attack attempts
    SUSPICIOUS_PATTERNS = [
        re.compile(r"<script", re.IGNORECASE),
        re.compile(r"javascript:", re.IGNORECASE),
        re.compile(r"on\w+\s*=", re.IGNORECASE),  # onload=, onerror=, etc.
        re.compile(r"union\s+select", re.IGNORECASE),
        re.compile(r";\s*drop\s+table", re.IGNORECASE),
        re.compile(r";\s*delete\s+from", re.IGNORECASE),
        re.compile(r"'\s*or\s+'1'\s*=\s*'1", re.IGNORECASE),
        re.compile(r"\x00"),  # Null bytes
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Screen the URL path and the query string. We check BOTH the raw
        # query string and the URL-decoded parameter values: an attacker who
        # sends `UNION%20SELECT` would otherwise sail past a regex written for
        # `UNION SELECT`, because percent-encoding contains no whitespace.
        if self._is_suspicious(request.path) or self._is_suspicious_query(request):
            logger.warning(
                "Suspicious request blocked: path=%s query=%s from IP %s",
                request.path[:200],
                request.META.get("QUERY_STRING", "")[:200],
                request.META.get("REMOTE_ADDR", "unknown"),
            )
            return JsonResponse(
                {
                    "success": False,
                    "error": {"code": "BLOCKED", "message": "Request blocked by security filter."},
                },
                status=403,
            )

        return self.get_response(request)

    def _is_suspicious_query(self, request) -> bool:
        """Check the raw query string and every decoded key/value pair."""
        if self._is_suspicious(request.META.get("QUERY_STRING", "")):
            return True

        # request.GET gives us URL-decoded values, which is what the patterns
        # in SUSPICIOUS_PATTERNS are actually written against.
        try:
            for key, values in request.GET.lists():
                if self._is_suspicious(key):
                    return True
                for value in values:
                    if self._is_suspicious(value):
                        return True
        except UnicodeDecodeError:
            # Undecodable bytes are themselves suspicious.
            return True
        return False

    def _is_suspicious(self, value: str) -> bool:
        """Check if a string matches any suspicious pattern."""
        return any(pattern.search(value) for pattern in self.SUSPICIOUS_PATTERNS)
