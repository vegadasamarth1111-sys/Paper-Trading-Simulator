"""
Security-review tests (Phase 7, plan section 12).

Executable checklist: the posture claims in README/docs/SECURITY.md are
pinned here, so a future settings edit that weakens them breaks the
build instead of shipping silently. Anything needing secrets or a live
server stays in the runbook, not in this file.
"""

import pytest

pytestmark = pytest.mark.django_db


class TestAuthenticationPosture:
    def test_jwt_is_the_only_auth(self):
        from django.conf import settings

        classes = settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]
        assert classes == ("rest_framework_simplejwt.authentication.JWTAuthentication",)

    def test_authenticated_by_default(self):
        from django.conf import settings

        assert (
            "rest_framework.permissions.IsAuthenticated"
            in settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]
        )

    def test_short_lived_access_tokens(self):
        from django.conf import settings

        access = settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"]
        assert access.total_seconds() <= 30 * 60
        assert settings.SIMPLE_JWT["ROTATE_REFRESH_TOKENS"] is True
        assert settings.SIMPLE_JWT["BLACKLIST_AFTER_ROTATION"] is True

    def test_argon2_first(self):
        """Production hasher order, read from base settings in a subprocess.

        The test runner deliberately overrides to MD5 for speed (pinned
        below), so this must read config.settings.base itself — asserting
        on the imported test settings would certify the wrong thing.
        """
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os;"
                "os.environ['DJANGO_SETTINGS_MODULE']='config.settings.base';"
                "os.environ['DJANGO_SECRET_KEY']='x'*64;"
                "os.environ['DB_PASSWORD']='pw';"
                "import django;django.setup();"
                "from django.conf import settings;"
                "print(settings.PASSWORD_HASHERS[0])",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "django.contrib.auth.hashers.Argon2PasswordHasher"

    def test_md5_only_as_test_speed_override(self):
        """Documents the deliberate testing exception (fast, insecure, local)."""
        from django.conf import settings

        assert settings.PASSWORD_HASHERS == ["django.contrib.auth.hashers.MD5PasswordHasher"]


class TestTransportPosture:
    def test_no_browsable_api_in_base(self):
        from django.conf import settings

        renderers = settings.REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"]
        assert "rest_framework.renderers.JSONRenderer" in renderers
        assert not any("Browsable" in renderer for renderer in renderers)

    def test_cors_not_wildcard(self):
        from django.conf import settings

        assert settings.CORS_ALLOW_ALL_ORIGINS is False
        assert len(settings.CORS_ALLOWED_ORIGINS) >= 1

    def test_cookies_locked_down(self):
        from django.conf import settings

        assert settings.SESSION_COOKIE_HTTPONLY is True
        assert settings.CSRF_COOKIE_HTTPONLY is True
        assert settings.SESSION_COOKIE_SAMESITE == "Lax"
        assert settings.CSRF_COOKIE_SAMESITE == "Lax"


class TestMiddlewarePosture:
    def test_security_chain_present(self):
        from django.conf import settings

        middleware = list(settings.MIDDLEWARE)
        for required in (
            "django.middleware.security.SecurityMiddleware",
            "csp.middleware.CSPMiddleware",
            "security.middleware.AdminIPWhitelistMiddleware",
            "security.middleware.RequestSanitizationMiddleware",
        ):
            assert required in middleware, f"missing middleware: {required}"

    def test_csp_is_deny_by_default(self):
        from django.conf import settings

        assert settings.CSP_DEFAULT_SRC == ("'self'",)
        assert settings.CSP_FRAME_SRC == ("'none'",)
        assert settings.CSP_OBJECT_SRC == ("'none'",)


class TestDataProtectionPosture:
    def test_backup_encryption_available(self):
        import cryptography
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        token = Fernet(key).encrypt(b"paper-trading")
        assert Fernet(key).decrypt(token) == b"paper-trading"
        assert cryptography.__version__ >= "40"

    def test_backup_key_has_no_default(self):
        """An empty key must refuse, never silently write plaintext."""
        from core.backups import _fernet

        with pytest.raises(ValueError, match="Refusing unencrypted backup"):
            _fernet("")

    def test_backup_roundtrip(self, tmp_path):
        """Encrypted backup restores exactly (the tested-restore mandate)."""
        from cryptography.fernet import Fernet
        from django.contrib.auth import get_user_model

        from core.backups import create_backup, restore_backup
        from core.models import Account, Instrument

        key = Fernet.generate_key().decode()
        user = get_user_model().objects.create_user(username="bk", email="b@e.com", password="x")
        Account.objects.create(user=user)
        Instrument.objects.create(
            symbol="BK",
            exchange="NSE",
            segment="EQ",
            name="Backup Co",
            exchange_token="9",
            lot_size=1,
            tick_size="0.05",
            effective_from="2024-01-01",
        )
        summary = create_backup(key=key, directory=tmp_path / "backups")
        assert summary["objects"] > 0
        manifest = tmp_path / "backups" / (summary["path"].rsplit("/", 1)[-1] + ".manifest.json")
        assert manifest.is_file()

        Account.objects.all().delete()
        user.delete()
        assert Account.objects.count() == 0
        restored = restore_backup(key=key, path=summary["path"])
        assert restored["objects"] == summary["objects"]
        assert Account.objects.count() == 1
        assert Instrument.objects.filter(symbol="BK").exists()

    def test_tampered_backup_refused(self, tmp_path):
        from pathlib import Path

        from cryptography.fernet import Fernet

        from core.backups import create_backup, restore_backup

        key = Fernet.generate_key().decode()
        summary = create_backup(key=key, directory=tmp_path / "backups")
        target = Path(summary["path"])
        raw = bytearray(target.read_bytes())
        raw[len(raw) // 2] ^= 0xFF  # flip one ciphertext byte
        target.write_bytes(bytes(raw))
        with pytest.raises(ValueError, match="hash mismatch|decrypt failed"):
            restore_backup(key=key, path=summary["path"])

    def test_wrong_key_refused(self, tmp_path):
        from pathlib import Path

        from cryptography.fernet import Fernet

        from core.backups import create_backup, restore_backup

        key = Fernet.generate_key().decode()
        summary = create_backup(key=key, directory=tmp_path / "backups")
        # Wrong key must fail at decrypt while the hash still matches:
        # re-sign the manifest so only the key is wrong.
        import hashlib
        import json

        raw = Path(summary["path"]).read_bytes()
        manifest_path = Path(summary["path"] + ".manifest.json")
        manifest = json.loads(manifest_path.read_text())
        manifest["sha256"] = hashlib.sha256(raw).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        other = Fernet.generate_key().decode()
        with pytest.raises(ValueError, match="decrypt failed"):
            restore_backup(key=other, path=summary["path"])

    def test_backup_commands(self, tmp_path, monkeypatch, account):
        from io import StringIO

        from cryptography.fernet import Fernet
        from django.core.management import call_command
        from django.core.management.base import CommandError

        monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", Fernet.generate_key().decode())
        monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
        out = StringIO()
        call_command("backup_db", stdout=out)
        assert "objects" in out.getvalue()

        monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", "")
        with pytest.raises(CommandError, match="Refusing unencrypted"):
            call_command("backup_db", stdout=StringIO())

        monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", Fernet.generate_key().decode())
        with pytest.raises(CommandError, match="--force"):
            call_command("restore_db", "--file", "whatever", stdout=StringIO())
