"""
Tests for security.permissions — role-based access control.

"Make sure users only access what they are supposed to" is enforced here and
in the object-level checks, so every rule gets a test that proves it denies.
"""

from decimal import Decimal

import pytest

from core.enums import RiskRuleType
from core.models import RiskConfig
from security.permissions import (
    CanModifyRiskConfig,
    CanPlaceOrders,
    IsAccountOwner,
    IsAccountOwnerForList,
    IsAdminUser,
    IsReadOnly,
)


class FakeRequest:
    """Minimal stand-in for DRF's Request."""

    def __init__(self, user=None, method="GET", path="/api/v1/test/"):
        self.user = user
        self.method = method
        self.path = path


class FakeView:
    pass


@pytest.mark.django_db
class TestIsAccountOwner:
    def test_owner_can_access_object_exposing_user(self, user, account, other_account):
        """Account exposes `.user`, so ownership is checked directly."""
        perm = IsAccountOwner()
        assert perm.has_object_permission(FakeRequest(user), FakeView(), account) is True
        assert perm.has_object_permission(FakeRequest(user), FakeView(), other_account) is False

    def test_owner_via_account_chain(self, user, account, other_account):
        """Objects such as Order/Position/Trade expose `.account`, not `.user`."""
        perm = IsAccountOwner()

        class OrderLike:
            def __init__(self, acct):
                self.account = acct

        assert perm.has_object_permission(FakeRequest(user), FakeView(), OrderLike(account)) is True
        assert (
            perm.has_object_permission(FakeRequest(user), FakeView(), OrderLike(other_account))
            is False
        )

    def test_denies_object_with_no_ownership_link(self, user):
        perm = IsAccountOwner()
        assert perm.has_object_permission(FakeRequest(user), FakeView(), object()) is False


@pytest.mark.django_db
class TestIsAccountOwnerForList:
    def test_authenticated_user_may_list(self, user):
        assert IsAccountOwnerForList().has_permission(FakeRequest(user), FakeView()) is True

    def test_anonymous_may_not_list(self):
        assert IsAccountOwnerForList().has_permission(FakeRequest(None), FakeView()) is False


@pytest.mark.django_db
class TestIsAdminUser:
    def test_staff_user_is_allowed(self, django_user_model):
        admin = django_user_model.objects.create_user(
            username="admin", password="strong-admin-passphrase"
        )
        admin.is_staff = True
        admin.save()
        assert IsAdminUser().has_permission(FakeRequest(admin), FakeView()) is True

    def test_regular_user_is_denied(self, user):
        assert IsAdminUser().has_permission(FakeRequest(user), FakeView()) is False

    def test_anonymous_is_denied(self):
        assert IsAdminUser().has_permission(FakeRequest(None), FakeView()) is False


class TestIsReadOnly:
    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
    def test_read_methods_allowed(self, method):
        assert IsReadOnly().has_permission(FakeRequest(method=method), FakeView()) is True

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_write_methods_denied(self, method):
        assert IsReadOnly().has_permission(FakeRequest(method=method), FakeView()) is False


@pytest.mark.django_db
class TestCanPlaceOrders:
    def test_active_account_is_allowed(self, user, account):
        assert CanPlaceOrders().has_permission(FakeRequest(user), FakeView()) is True

    def test_user_without_account_is_allowed(self, user):
        """No account yet: the order view auto-creates a fresh ACTIVE,
         unhalted account, so there is nothing to block at permission time.
         (Renamed from is_denied when auto-creation landed; the safety
        moved, not away: fresh accounts are active + unhalted by defaults.)"""
        assert CanPlaceOrders().has_permission(FakeRequest(user), FakeView()) is True

    def test_inactive_account_is_denied(self, user, account):
        account.is_active = False
        account.save()
        assert CanPlaceOrders().has_permission(FakeRequest(user), FakeView()) is False

    def test_kill_switch_blocks_order_placement(self, user, account):
        """The kill switch must stop order entry dead."""
        account.kill_switch_active = True
        account.save()
        assert CanPlaceOrders().has_permission(FakeRequest(user), FakeView()) is False

    def test_anonymous_is_denied(self):
        assert CanPlaceOrders().has_permission(FakeRequest(None), FakeView()) is False


@pytest.mark.django_db
class TestCanModifyRiskConfig:
    def _risk_config(self, account):
        return RiskConfig.objects.create(
            account=account,
            rule_type=RiskRuleType.MAX_DAILY_LOSS,
            limit_value=Decimal("5000"),
        )

    def test_staff_can_modify(self, django_user_model):
        admin = django_user_model.objects.create_user(
            username="admin", password="strong-admin-passphrase"
        )
        admin.is_staff = True
        admin.save()
        assert CanModifyRiskConfig().has_permission(FakeRequest(admin), FakeView()) is True

    def test_authenticated_user_passes_view_level_check(self, user):
        assert CanModifyRiskConfig().has_permission(FakeRequest(user), FakeView()) is True

    def test_object_level_owner_check(self, user, account):
        """RiskConfig exposes `.account`, which is the ownership path."""
        perm = CanModifyRiskConfig()
        assert (
            perm.has_object_permission(FakeRequest(user), FakeView(), self._risk_config(account))
            is True
        )

    def test_object_level_denies_other_account(self, user, other_account):
        perm = CanModifyRiskConfig()
        assert (
            perm.has_object_permission(
                FakeRequest(user), FakeView(), self._risk_config(other_account)
            )
            is False
        )

    def test_object_level_denies_object_without_account(self, user):
        perm = CanModifyRiskConfig()
        assert perm.has_object_permission(FakeRequest(user), FakeView(), object()) is False
