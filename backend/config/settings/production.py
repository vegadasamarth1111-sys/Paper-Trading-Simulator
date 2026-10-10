"""
Paper Trading Simulator — Production Settings
===============================================
Maximum security. Every protection enabled. No exceptions.
"""

from .base import *  # noqa: F401, F403

# =============================================================================
# DEBUG — NEVER True in production
# =============================================================================
DEBUG = False

# Double-check via environment
if config("DJANGO_DEBUG", default="False", cast=bool):  # noqa: F405
    raise RuntimeError(
        "FATAL: DJANGO_DEBUG=True in production environment. "
        "This exposes sensitive information. Fix your .env file immediately."
    )

# =============================================================================
# SECURITY — Full lockdown
# =============================================================================

# HTTPS enforcement
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# HSTS — tell browsers to always use HTTPS
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Cookie security — all cookies require HTTPS
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True

# =============================================================================
# STATIC FILES — Served via whitenoise, not Django
# =============================================================================
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")  # noqa: F405
STORAGES = {  # noqa: F405
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# =============================================================================
# LOGGING — JSON format for log aggregation
# =============================================================================
LOGGING["handlers"]["console"]["formatter"] = "json"  # noqa: F405
LOGGING["loggers"]["django"]["level"] = "WARNING"  # noqa: F405
LOGGING["loggers"]["core"]["level"] = "INFO"  # noqa: F405

# =============================================================================
# RATE LIMITING — Strict in production
# =============================================================================
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {  # noqa: F405
    "burst": "30/min",
    "sustained": config("RATE_LIMIT_DEFAULT", default="500/hour"),  # noqa: F405
}

# =============================================================================
# DATABASE — Connection pooling for production
# =============================================================================
DATABASES["default"]["CONN_MAX_AGE"] = 600  # noqa: F405
