"""
Paper Trading Simulator — Health Check View
=============================================
Unauthenticated endpoint for load balancers, Docker health checks,
and monitoring systems.
"""

from typing import Any

from django.core.cache import cache
from django.db import connection
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView


class HealthCheckThrottle(AnonRateThrottle):
    """Rate-limit health checks to prevent abuse."""

    rate = "60/min"


class HealthCheckView(APIView):
    """
    System health check endpoint.
    Returns status of all critical dependencies.
    No authentication required — but rate-limited.
    """

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_classes = [HealthCheckThrottle]

    def get(self, request):
        health = {
            "status": "healthy",
            "components": {},
        }
        overall_healthy = True

        # Check database
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            health["components"]["database"] = {"status": "up"}
        except Exception:
            health["components"]["database"] = {"status": "down"}
            overall_healthy = False

        # Check cache (Redis)
        try:
            cache.set("health_check", "ok", timeout=5)
            cached = cache.get("health_check")
            if cached == "ok":
                health["components"]["cache"] = {"status": "up"}
            else:
                health["components"]["cache"] = {"status": "degraded"}
                overall_healthy = False
        except Exception:
            health["components"]["cache"] = {"status": "down"}
            overall_healthy = False

        if not overall_healthy:
            health["status"] = "unhealthy"
            return Response(health, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response(health, status=status.HTTP_200_OK)
