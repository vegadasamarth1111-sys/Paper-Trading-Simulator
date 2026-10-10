"""
Paper Trading Simulator — Security URLs
=========================================
Authentication endpoints with rate limiting.
"""

from django.urls import path

from security.authentication import (
    ChangePasswordView,
    CurrentUserView,
    RegisterView,
    SecureTokenObtainPairView,
    SecureTokenRefreshView,
)

app_name = "security"

urlpatterns = [
    path("login/", SecureTokenObtainPairView.as_view(), name="token-obtain"),
    path("refresh/", SecureTokenRefreshView.as_view(), name="token-refresh"),
    path("register/", RegisterView.as_view(), name="register"),
    path("me/", CurrentUserView.as_view(), name="current-user"),
    path("change-password/", ChangePasswordView.as_view(), name="change-password"),
]
