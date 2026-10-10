"""
Paper Trading Simulator — Test Settings
=========================================
Fast, isolated, deterministic.
"""

from .base import *  # noqa: F401, F403

# =============================================================================
# TEST OPTIMIZATIONS
# =============================================================================
DEBUG = False  # Test in production-like conditions

# Faster password hashing for tests (Argon2 is intentionally slow)
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# In-memory SQLite for speed (MySQL-specific features tested in integration tests)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# In-memory cache
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# Disable rate limiting in tests
REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = ()  # noqa: F405
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {}  # noqa: F405

# Disable Celery in tests — run tasks synchronously
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Disable Sentry in tests
SENTRY_DSN = ""

# Structured logging at WARNING to reduce test noise
LOGGING["loggers"]["django"]["level"] = "WARNING"  # noqa: F405
LOGGING["loggers"]["core"]["level"] = "WARNING"  # noqa: F405
LOGGING["loggers"]["security"]["level"] = "WARNING"  # noqa: F405

# Admin IP whitelist disabled for tests
ADMIN_IP_WHITELIST = []  # noqa: F405
