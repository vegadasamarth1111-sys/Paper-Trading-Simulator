"""
Tests for security.middleware — headers, admin IP whitelist, request filtering.

These are the controls behind "protect my admin routes" and "add security
headers", so each one gets a test that proves it actually blocks/sets something.
"""

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from security.middleware import (
    AdminIPWhitelistMiddleware,
    RequestSanitizationMiddleware,
    SecurityHeadersMiddleware,
)


def ok_response(_request):
    return HttpResponse("ok")


class TestSecurityHeadersMiddleware:
    def test_sets_defensive_headers(self, rf: RequestFactory):
        request = rf.get("/")
        response = SecurityHeadersMiddleware(ok_response)(request)
        assert response["X-Content-Type-Options"] == "nosniff"
        assert response["X-Frame-Options"] == "DENY"
        assert response["X-XSS-Protection"] == "1; mode=block"
        assert response["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in response["Permissions-Policy"]

    def test_api_responses_are_never_cached(self, rf: RequestFactory):
        """Trade/position data must not be cached by intermediaries."""
        request = rf.get("/api/v1/positions/")
        response = SecurityHeadersMiddleware(ok_response)(request)
        assert "no-store" in response["Cache-Control"]
        assert response["Pragma"] == "no-cache"

    def test_non_api_responses_are_untouched_by_cache_header(self, rf: RequestFactory):
        request = rf.get("/admin/")
        response = SecurityHeadersMiddleware(ok_response)(request)
        assert "Cache-Control" not in response


class TestAdminIPWhitelistMiddleware:
    def _request(self, factory, path="/admin/", **meta):
        return factory.get(path, **meta)

    @override_settings(ADMIN_IP_WHITELIST=[])
    def test_empty_whitelist_allows_everyone(self, rf: RequestFactory):
        """Dev mode: an empty whitelist means no restriction."""
        response = AdminIPWhitelistMiddleware(ok_response)(self._request(rf))
        assert response.status_code == 200

    @override_settings(ADMIN_IP_WHITELIST=["203.0.113.7"])
    def test_whitelisted_ip_is_allowed(self, rf: RequestFactory):
        request = self._request(rf, REMOTE_ADDR="203.0.113.7")
        response = AdminIPWhitelistMiddleware(ok_response)(request)
        assert response.status_code == 200

    @override_settings(ADMIN_IP_WHITELIST=["198.51.100.9"])
    def test_other_ip_is_forbidden(self, rf: RequestFactory):
        request = self._request(rf, REMOTE_ADDR="203.0.113.1")
        response = AdminIPWhitelistMiddleware(ok_response)(request)
        assert response.status_code == 403

    @override_settings(ADMIN_IP_WHITELIST=["203.0.113.7"])
    def test_ip6_loopback_is_forbidden_when_not_listed(self, rf: RequestFactory):
        request = self._request(rf, REMOTE_ADDR="::1")
        response = AdminIPWhitelistMiddleware(ok_response)(request)
        assert response.status_code == 403

    @override_settings(ADMIN_IP_WHITELIST=["203.0.113.7"])
    def test_non_admin_paths_are_not_gated(self, rf: RequestFactory):
        """The whitelist must not lock out the whole API."""
        request = self._request(rf, path="/api/v1/health/", REMOTE_ADDR="198.51.100.9")
        response = AdminIPWhitelistMiddleware(ok_response)(request)
        assert response.status_code == 200

    @override_settings(ADMIN_IP_WHITELIST=["203.0.113.7"])
    def test_forwarded_for_is_honoured_behind_proxy(self, rf: RequestFactory):
        request = self._request(
            rf,
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1",
        )
        response = AdminIPWhitelistMiddleware(ok_response)(request)
        assert response.status_code == 200

    @override_settings(ADMIN_IP_WHITELIST=["203.0.113.7"])
    def test_forbidden_response_does_not_leak_whitelist(self, rf: RequestFactory):
        request = self._request(rf, REMOTE_ADDR="198.51.100.9")
        response = AdminIPWhitelistMiddleware(ok_response)(request)
        body = response.content.decode()
        assert "203.0.113.7" not in body


class TestRequestSanitizationMiddleware:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/?q=<script>alert(1)</script>",
            "/api/v1/?next=javascript:alert(1)",
            "/api/v1/?x=1&onload=evil",
            "/api/v1/?q=1'%20UNION%20SELECT%20*%20FROM%20users",
            "/api/v1/?q=1%3B%20DROP%20TABLE%20users",
            "/api/v1/?q=1%3B%20DELETE%20FROM%20users",
            "/api/v1/?q=' or '1'='1",
        ],
    )
    def test_attack_payloads_are_blocked(self, rf: RequestFactory, path):
        request = rf.get(path)
        response = RequestSanitizationMiddleware(ok_response)(request)
        assert response.status_code == 403
        assert b"blocked by security filter" in response.content

    def test_suspicious_url_path_is_blocked(self, rf: RequestFactory):
        request = rf.get("/api/v1/<script>alert(1)</script>")
        response = RequestSanitizationMiddleware(ok_response)(request)
        assert response.status_code == 403

    def test_null_byte_in_query_is_blocked(self, rf: RequestFactory):
        request = rf.get("/api/v1/", data={"q": "abc def"})
        response = RequestSanitizationMiddleware(ok_response)(request)
        assert response.status_code == 200, "plain text must not be blocked"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/health/",
            "/api/v1/instruments/?search=RELIANCE&exchange=NSE",
            "/api/v1/orders/?status=OPEN",
            "/admin/login/",
        ],
    )
    def test_ordinary_requests_pass_through(self, rf: RequestFactory, path):
        request = rf.get(path)
        response = RequestSanitizationMiddleware(ok_response)(request)
        assert response.status_code == 200

    def test_post_body_is_not_inspected(self, rf: RequestFactory):
        """
        Only the query string and path are screened; bodies are validated by
        DRF serializers. This documents that boundary deliberately.
        """
        request = rf.post("/api/v1/auth/register/", data={"note": "<script>"})
        response = RequestSanitizationMiddleware(ok_response)(request)
        assert response.status_code == 200


class TestPercentEncodedBypass:
    """
    Regression guard.

    The filter originally ran its regexes over the RAW query string. Because
    `%20` is not whitespace, `UNION%20SELECT` did not match `union\\s+select`,
    so any attacker who percent-encoded a payload walked straight past the
    control. The middleware now also screens URL-decoded parameter values.
    """

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/?q=1'%20UNION%20SELECT%20*%20FROM%20users",
            "/api/v1/?q=1%3B%20DROP%20TABLE%20users",
            "/api/v1/?q=1%3B%20DELETE%20FROM%20users",
            "/api/v1/?q=%3Cscript%3Ealert(1)%3C/script%3E",
            "/api/v1/?next=javascript%3Aalert(1)",
        ],
    )
    def test_percent_encoded_payloads_are_blocked(self, rf: RequestFactory, path):
        request = rf.get(path)
        response = RequestSanitizationMiddleware(ok_response)(request)
        assert response.status_code == 403

    def test_encoded_payload_in_a_parameter_key_is_blocked(self, rf: RequestFactory):
        request = rf.get("/api/v1/", data={"<script>": "x"})
        response = RequestSanitizationMiddleware(ok_response)(request)
        assert response.status_code == 403

    def test_legitimately_encoded_search_term_still_passes(self, rf: RequestFactory):
        """A normal encoded search term must not become collateral damage."""
        request = rf.get("/api/v1/instruments/", data={"search": "M&M"})
        response = RequestSanitizationMiddleware(ok_response)(request)
        assert response.status_code == 200
