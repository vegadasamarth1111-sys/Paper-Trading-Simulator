"""
Paper Trading Simulator — Custom Permissions
==============================================
Role-based access control for API endpoints.
Users only access what they are supposed to.
"""

import logging

from rest_framework.permissions import BasePermission

logger = logging.getLogger("security")


class IsAccountOwner(BasePermission):
    """
    Object-level permission: users can only access their own trading account.
    Prevents user A from seeing user B's orders, positions, or balance.
    """

    message = "You can only access your own trading account."

    def has_object_permission(self, request, view, obj):
        # If the object has a direct user field
        if hasattr(obj, "user"):
            return obj.user == request.user
        # If the object has an account → user chain
        if hasattr(obj, "account"):
            return obj.account.user == request.user
        return False


class IsAccountOwnerForList(BasePermission):
    """
    View-level permission that ensures list queries are filtered
    to the requesting user's account.
    This is enforced in the view's get_queryset() — this permission
    serves as a safety net.
    """

    message = "You can only list your own trading data."

    def has_permission(self, request, view):
        # Authenticated users can access list views.
        # Actual filtering happens in get_queryset().
        # Cast to a real bool: DRF treats a None return as "deny", but an
        # explicit False keeps the intent unambiguous.
        return bool(request.user and request.user.is_authenticated)


class IsAdminUser(BasePermission):
    """
    Restrict endpoint to admin/staff users only.
    """

    message = "This action requires administrator privileges."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if not request.user.is_staff:
            logger.warning(
                "Non-admin user %s attempted to access admin endpoint: %s",
                request.user.username,
                request.path,
            )
            return False
        return True


class IsReadOnly(BasePermission):
    """
    Allow read-only access (GET, HEAD, OPTIONS).
    Write operations require additional permissions.
    """

    message = "This endpoint is read-only."

    def has_permission(self, request, view):
        return request.method in ("GET", "HEAD", "OPTIONS")


class CanPlaceOrders(BasePermission):
    """
    Check that the user's account is active and the kill switch is not engaged.
    """

    message = "Your account is not eligible for placing orders."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        try:
            account = request.user.trading_account
        except Exception:
            logger.warning(
                "User %s has no trading account but attempted to place an order.",
                request.user.username,
            )
            return False

        if not account.is_active:
            logger.warning(
                "Inactive account %s attempted to place order.",
                account.id,
            )
            return False

        if account.kill_switch_active:
            logger.warning(
                "Kill switch active on account %s — order placement blocked.",
                account.id,
            )
            return False

        return True


class CanModifyRiskConfig(BasePermission):
    """
    Only admin or account owner can modify risk configuration.
    Changes are logged in the audit trail.
    """

    message = "You cannot modify risk configuration."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        # Admins can always modify
        if request.user.is_staff:
            return True
        # Regular users can only modify their own
        return True  # Object-level check handles ownership

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        if hasattr(obj, "account"):
            return obj.account.user == request.user
        return False
