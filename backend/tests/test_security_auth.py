"""
Tests for security.authentication — registration, login, profile, password change.

Covers the user-facing half of "add proper authentication", "hash passwords
properly", and "sanitize my forms".
"""

import pytest
from django.contrib.auth import get_user_model

User = get_user_model()

REGISTER_URL = "/api/v1/auth/register/"
LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/refresh/"
ME_URL = "/api/v1/auth/me/"
CHANGE_PASSWORD_URL = "/api/v1/auth/change-password/"

STRONG_PASSWORD = "correct-horse-battery-staple"


def register(client, **overrides):
    payload = {
        "username": "samarth",
        "email": "samarth@example.com",
        "password": STRONG_PASSWORD,
        "password_confirm": STRONG_PASSWORD,
    }
    payload.update(overrides)
    return client.post(REGISTER_URL, payload, format="json")


@pytest.mark.django_db
class TestRegistration:
    def test_successful_registration(self, api_client):
        response = register(api_client)
        assert response.status_code == 201
        body = response.json()
        assert body["success"] is True
        assert body["user"]["username"] == "samarth"
        assert body["user"]["email"] == "samarth@example.com"

    def test_password_is_hashed_not_stored_in_plaintext(self, api_client):
        register(api_client)
        user = User.objects.get(username="samarth")
        assert STRONG_PASSWORD not in user.password
        # And the hash verifies.
        assert user.check_password(STRONG_PASSWORD) is True

    def test_password_is_not_returned_in_the_response(self, api_client):
        response = register(api_client)
        assert STRONG_PASSWORD not in response.content.decode()
        assert "password" not in response.json()["user"]

    def test_email_is_lowercased_and_stripped(self, api_client):
        response = register(api_client, email="  Samarth@Example.COM ")
        assert response.json()["user"]["email"] == "samarth@example.com"

    def test_duplicate_username_is_rejected(self, api_client):
        register(api_client)
        response = register(api_client, email="other@example.com")
        assert response.status_code == 400
        assert response.json()["success"] is False

    def test_duplicate_email_is_rejected(self, api_client):
        register(api_client)
        response = register(api_client, username="someoneelse")
        assert response.status_code == 400

    def test_mismatched_passwords_are_rejected(self, api_client):
        response = register(api_client, password_confirm="a-different-passphrase")
        assert response.status_code == 400

    def test_short_password_is_rejected(self, api_client):
        """Minimum length is 12 characters, not the Django default of 8."""
        response = register(api_client, password="short12345", password_confirm="short12345")
        assert response.status_code == 400

    def test_username_with_script_tag_is_rejected(self, api_client):
        """XSS payload in the username must never become a stored value."""
        response = register(api_client, username="<script>alert(1)</script>")
        assert response.status_code == 400
        assert not User.objects.filter(username__contains="script").exists()

    def test_username_with_spaces_is_rejected(self, api_client):
        response = register(api_client, username="not a username")
        assert response.status_code == 400

    def test_invalid_email_is_rejected(self, api_client):
        response = register(api_client, email="not-an-email")
        assert response.status_code == 400

    def test_html_in_email_is_rejected(self, api_client):
        response = register(api_client, email="<b>x</b>@example.com")
        assert response.status_code == 400


@pytest.mark.django_db
class TestLogin:
    def _make_user(self):
        return User.objects.create_user(
            username="samarth", email="s@example.com", password=STRONG_PASSWORD
        )

    def test_successful_login_returns_tokens(self, api_client):
        self._make_user()
        response = api_client.post(
            LOGIN_URL, {"username": "samarth", "password": STRONG_PASSWORD}, format="json"
        )
        assert response.status_code == 200
        body = response.json()
        assert "access" in body and "refresh" in body
        assert body["user"]["username"] == "samarth"

    def test_wrong_password_is_rejected(self, api_client):
        self._make_user()
        response = api_client.post(
            LOGIN_URL, {"username": "samarth", "password": "wrong-password-123"}, format="json"
        )
        assert response.status_code == 401

    def test_unknown_user_is_rejected(self, api_client):
        response = api_client.post(
            LOGIN_URL, {"username": "ghost", "password": STRONG_PASSWORD}, format="json"
        )
        assert response.status_code == 401

    def test_login_response_does_not_echo_the_password(self, api_client):
        self._make_user()
        response = api_client.post(
            LOGIN_URL, {"username": "samarth", "password": STRONG_PASSWORD}, format="json"
        )
        assert STRONG_PASSWORD not in response.content.decode()


@pytest.mark.django_db
class TestRefresh:
    def test_refresh_returns_a_new_access_token(self, api_client):
        User.objects.create_user(username="samarth", password=STRONG_PASSWORD)
        login = api_client.post(
            LOGIN_URL, {"username": "samarth", "password": STRONG_PASSWORD}, format="json"
        )
        refresh = login.json()["refresh"]
        response = api_client.post(REFRESH_URL, {"refresh": refresh}, format="json")
        assert response.status_code == 200
        assert "access" in response.json()

    def test_invalid_refresh_token_is_rejected(self, api_client):
        response = api_client.post(REFRESH_URL, {"refresh": "nonsense"}, format="json")
        assert response.status_code == 401


@pytest.mark.django_db
class TestCurrentUser:
    def test_me_requires_authentication(self, api_client):
        assert api_client.get(ME_URL).status_code == 401

    def test_me_returns_own_details_only(self, auth_client, user):
        response = auth_client.get(ME_URL)
        assert response.status_code == 200
        body = response.json()
        assert body["user"]["id"] == user.id
        assert body["user"]["username"] == "samarth"
        # Staff flag is exposed for the requester's own record only.
        assert body["user"]["is_staff"] is False


@pytest.mark.django_db
class TestChangePassword:
    NEW_PASSWORD = "a-brand-new-strong-passphrase"

    def test_requires_authentication(self, api_client):
        assert api_client.post(CHANGE_PASSWORD_URL, {}).status_code == 401

    def test_successful_password_change(self, auth_client, user):
        response = auth_client.post(
            CHANGE_PASSWORD_URL,
            {
                "current_password": STRONG_PASSWORD,
                "new_password": self.NEW_PASSWORD,
                "new_password_confirm": self.NEW_PASSWORD,
            },
            format="json",
        )
        assert response.status_code == 200
        user.refresh_from_db()
        assert user.check_password(self.NEW_PASSWORD) is True
        assert user.check_password(STRONG_PASSWORD) is False

    def test_wrong_current_password_is_rejected(self, auth_client):
        response = auth_client.post(
            CHANGE_PASSWORD_URL,
            {
                "current_password": "totally-wrong-pass",
                "new_password": self.NEW_PASSWORD,
                "new_password_confirm": self.NEW_PASSWORD,
            },
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_PASSWORD"

    def test_mismatched_confirmation_is_rejected(self, auth_client, user):
        response = auth_client.post(
            CHANGE_PASSWORD_URL,
            {
                "current_password": STRONG_PASSWORD,
                "new_password": self.NEW_PASSWORD,
                "new_password_confirm": "different-entirely-123",
            },
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "PASSWORD_MISMATCH"
        user.refresh_from_db()
        assert user.check_password(STRONG_PASSWORD) is True

    def test_short_new_password_is_rejected(self, auth_client, user):
        response = auth_client.post(
            CHANGE_PASSWORD_URL,
            {
                "current_password": STRONG_PASSWORD,
                "new_password": "short12345",
                "new_password_confirm": "short12345",
            },
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "PASSWORD_TOO_SHORT"
        user.refresh_from_db()
        assert user.check_password(STRONG_PASSWORD) is True
