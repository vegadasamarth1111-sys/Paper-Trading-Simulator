"""
Paper Trading Simulator — Authentication
==========================================
JWT-based authentication with secure token handling.
Tokens are short-lived, rotated on refresh, and blacklisted after use.
"""

import logging
from typing import Any

import bleach
from django.contrib.auth import get_user_model
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

logger = logging.getLogger("security")
User = get_user_model()


class SecureTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Custom token serializer that:
    - Sanitizes input fields
    - Logs authentication attempts (without passwords)
    - Adds minimal user info to token claims
    """

    def validate(self, attrs):
        # Sanitize username input
        attrs["username"] = bleach.clean(attrs.get("username", "")).strip()

        # Log the attempt (NEVER log the password)
        logger.info(
            "Authentication attempt for user: %s",
            attrs["username"],
        )

        try:
            data = super().validate(attrs)
        except Exception:
            # Log failed attempt
            logger.warning(
                "Failed authentication attempt for user: %s",
                attrs["username"],
            )
            raise

        # Log success
        logger.info(
            "Successful authentication for user: %s (id=%s)",
            self.user.username,
            self.user.id,
        )

        # Add minimal user info to response (NOT to the token itself)
        data["user"] = {
            "id": self.user.id,
            "username": self.user.username,
            "email": self.user.email,
        }

        return data


class SecureTokenObtainPairView(TokenObtainPairView):
    """Login endpoint with secure token handling."""

    serializer_class = SecureTokenObtainPairSerializer


class SecureTokenRefreshView(TokenRefreshView):
    """Token refresh with rotation and blacklisting."""

    pass  # SimpleJWT handles rotation via settings


class RegisterSerializer(serializers.Serializer):
    """
    User registration with strict validation and sanitization.
    """

    username = serializers.CharField(
        min_length=3,
        max_length=30,
    )
    email = serializers.EmailField()
    password = serializers.CharField(
        min_length=12,
        write_only=True,
        help_text="Minimum 12 characters",
    )
    password_confirm = serializers.CharField(
        write_only=True,
    )

    def validate_username(self, value):
        """Sanitize and validate username."""
        clean_value = bleach.clean(value).strip()
        if not clean_value.isalnum() and "_" not in clean_value:
            raise serializers.ValidationError(
                "Username can only contain letters, numbers, and underscores."
            )
        if User.objects.filter(username__iexact=clean_value).exists():
            raise serializers.ValidationError("This username is already taken.")
        return clean_value

    def validate_email(self, value):
        """Sanitize and check uniqueness."""
        clean_value = bleach.clean(value).strip().lower()
        if User.objects.filter(email__iexact=clean_value).exists():
            raise serializers.ValidationError("This email is already registered.")
        return clean_value

    def validate(self, data):
        """Ensure passwords match."""
        if data["password"] != data["password_confirm"]:
            # User-facing validation copy, not a credential.  # nosec B105
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match."}  # nosec B105
            )
        return data

    def create(self, validated_data):
        """Create user with hashed password (Argon2 from settings)."""
        user = User.objects.create_user(
            username=validated_data["username"],
            email=validated_data["email"],
            password=validated_data["password"],  # Django hashes automatically
        )
        logger.info("New user registered: %s (id=%s)", user.username, user.id)
        return user


class RegisterView(APIView):
    """
    User registration endpoint.
    Rate-limited to prevent abuse (see rate_limiting.py).
    """

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(
            {
                "success": True,
                "message": "Account created successfully.",
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                },
            },
            status=status.HTTP_201_CREATED,
        )


class CurrentUserView(APIView):
    """Get current authenticated user's info."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response(
            {
                "success": True,
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "date_joined": user.date_joined.isoformat(),
                    "is_staff": user.is_staff,
                },
            }
        )


class ChangePasswordView(APIView):
    """
    Change password endpoint.
    Requires current password verification.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        current_password = request.data.get("current_password", "")
        new_password = request.data.get("new_password", "")
        new_password_confirm = request.data.get("new_password_confirm", "")

        if not request.user.check_password(current_password):
            logger.warning(
                "Failed password change attempt for user: %s",
                request.user.username,
            )
            return Response(
                {
                    "success": False,
                    "error": {
                        "code": "INVALID_PASSWORD",
                        "message": "Current password is incorrect.",
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_password != new_password_confirm:
            return Response(
                {
                    "success": False,
                    "error": {
                        "code": "PASSWORD_MISMATCH",
                        "message": "New passwords do not match.",
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(new_password) < 12:
            return Response(
                {
                    "success": False,
                    "error": {
                        "code": "PASSWORD_TOO_SHORT",
                        "message": "Password must be at least 12 characters.",
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        request.user.set_password(new_password)
        request.user.save()

        logger.info("Password changed for user: %s", request.user.username)
        return Response({"success": True, "message": "Password changed successfully."})
