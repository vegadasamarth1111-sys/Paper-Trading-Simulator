"""
Paper Trading Simulator — Development Settings
================================================
Relaxes security for local development ONLY.
"""

from .base import *  # noqa: F401, F403

# =============================================================================
# DEBUG (Only place this is ever True)
# =============================================================================
DEBUG = True

# 0.0.0.0 is deliberate here: the dev server must be reachable from the
# docker-compose network. Production settings never contain it.
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "[::1]"]  # noqa: S104  # nosec B104

# =============================================================================
# DEVELOPMENT APPS
# =============================================================================
INSTALLED_APPS += [  # noqa: F405
    "debug_toolbar",
    "django_extensions",
]

MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")  # noqa: F405

INTERNAL_IPS = ["127.0.0.1", "::1"]

# =============================================================================
# DRF — Enable browsable API in dev only
# =============================================================================
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = (  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
)

# =============================================================================
# CORS — More permissive in dev (still not allow-all)
# =============================================================================
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

# =============================================================================
# EMAIL — Console backend for dev
# =============================================================================
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# =============================================================================
# LOGGING — More verbose in dev
# =============================================================================
LOGGING["loggers"]["core"]["level"] = "DEBUG"  # noqa: F405
LOGGING["loggers"]["django"]["level"] = "DEBUG"  # noqa: F405

# =============================================================================
# ADMIN IP WHITELIST — Allow all in dev
# =============================================================================
ADMIN_IP_WHITELIST = []  # Empty = no restriction in dev  # noqa: F405

# =============================================================================
# RATE LIMITING — Relaxed in dev
# =============================================================================
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {  # noqa: F405
    "burst": "600/min",
    "sustained": "10000/hour",
}
