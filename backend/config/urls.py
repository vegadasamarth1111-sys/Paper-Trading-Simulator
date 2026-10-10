"""
Paper Trading Simulator — URL Configuration
=============================================
API-only routing. Admin is restricted via middleware.
"""

from django.contrib import admin
from django.urls import include, path

from core.views import HealthCheckView

urlpatterns = [
    # Health check — unauthenticated, for load balancers and monitoring
    path("api/v1/health/", HealthCheckView.as_view(), name="health-check"),
    # Authentication endpoints
    path("api/v1/auth/", include("security.urls")),
    # Instrument data
    path("api/v1/instruments/", include("instruments.urls")),
    # Admin — protected by AdminIPWhitelistMiddleware
    path("admin/", admin.site.urls),
]

# Customize admin site header
admin.site.site_header = "Paper Trading Simulator — Admin"
admin.site.site_title = "PTS Admin"
admin.site.index_title = "System Administration"
