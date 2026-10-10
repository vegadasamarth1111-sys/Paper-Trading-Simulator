"""
Paper Trading Simulator — Base Django Settings
================================================
Shared configuration across all environments.
Security settings are strict by default; development.py relaxes only what's needed.
"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from decouple import Csv, config

# =============================================================================
# PATH CONFIGURATION
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # backend/
PROJECT_DIR = BASE_DIR.parent  # project root

# =============================================================================
# ENVIRONMENT GUARD (Critical — prevents live keys in paper mode)
# =============================================================================
TRADING_MODE = config("TRADING_MODE", default="paper")
LIVE_TRADING_ENABLED = config("LIVE_TRADING_ENABLED", default=False, cast=bool)

if LIVE_TRADING_ENABLED and TRADING_MODE != "live":
    raise RuntimeError(
        "FATAL: LIVE_TRADING_ENABLED=True but TRADING_MODE is not 'live'. "
        "This is a configuration error that could cause real money loss. "
        "Set TRADING_MODE=live explicitly or disable live trading."
    )

# =============================================================================
# CORE DJANGO
# =============================================================================
SECRET_KEY = config("DJANGO_SECRET_KEY")

# Validate secret key strength at startup
if len(SECRET_KEY) < 50:
    raise RuntimeError(
        "DJANGO_SECRET_KEY is too short. Generate one with: "
        'python -c "from django.core.management.utils import '
        'get_random_secret_key; print(get_random_secret_key())"'
    )
if SECRET_KEY.startswith("CHANGE-ME"):
    raise RuntimeError(
        "DJANGO_SECRET_KEY still has placeholder value. Set a real secret key in .env"
    )

DEBUG = False  # Overridden ONLY in development.py

ALLOWED_HOSTS = config("DJANGO_ALLOWED_HOSTS", default="", cast=Csv())

# =============================================================================
# APPLICATION DEFINITIONS
# =============================================================================
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "corsheaders",
    "django_celery_beat",
]

LOCAL_APPS = [
    "core",
    "security",
    "instruments",
    "market_data",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# =============================================================================
# MIDDLEWARE (order matters for security)
# =============================================================================
MIDDLEWARE = [
    # Security headers first
    "django.middleware.security.SecurityMiddleware",
    "security.middleware.SecurityHeadersMiddleware",
    # Content-Security-Policy - must sit after SecurityMiddleware
    "csp.middleware.CSPMiddleware",
    # CORS before CommonMiddleware
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    # Session and auth
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # Clickjacking protection
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Custom security middleware
    "security.middleware.AdminIPWhitelistMiddleware",
    "security.middleware.RequestSanitizationMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
            # Auto-escape is ON by default in Django templates — never disable
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# =============================================================================
# DATABASE (MySQL 8 — InnoDB)
# =============================================================================
DATABASES = {
    "default": {
        "ENGINE": config("DB_ENGINE", default="django.db.backends.mysql"),
        "NAME": config("DB_NAME", default="paper_trading"),
        "USER": config("DB_USER", default="paper_trader"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST", default="127.0.0.1"),
        "PORT": config("DB_PORT", default="3306"),
        "CONN_MAX_AGE": config("DB_CONN_MAX_AGE", default=600, cast=int),
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            # Use parameterized queries only — Django ORM does this by default,
            # but we enforce STRICT mode to catch type errors early
        },
    }
}

# Add SSL for database if configured
_db_ssl_ca = config("DB_SSL_CA", default="")
if _db_ssl_ca:
    DATABASES["default"]["OPTIONS"]["ssl"] = {"ca": _db_ssl_ca}

# =============================================================================
# CACHE (Redis)
# =============================================================================
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": config("REDIS_CACHE_URL", default="redis://127.0.0.1:6379/1"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "SERIALIZER": "django_redis.serializers.json.JSONSerializer",
        },
        "KEY_PREFIX": "pts",  # paper trading simulator
        "TIMEOUT": 300,
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

# =============================================================================
# PASSWORD HASHING (Argon2 — memory-hard, GPU-attack resistant)
# =============================================================================
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",  # Primary — strongest
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",  # Fallback for migration
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },  # 12 chars minimum, not 8
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# =============================================================================
# INTERNATIONALIZATION & TIME
# =============================================================================
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"  # Store everything in UTC, convert to IST only for display
USE_I18N = True
USE_TZ = True

# IST offset for market-hour calculations (used in core.clock, NOT in Django)
MARKET_TIMEZONE = "Asia/Kolkata"

# =============================================================================
# STATIC FILES
# =============================================================================
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "static_collected"
# Only point at the static dir if it actually exists - a missing entry makes
# Django emit system check W004, which fails `manage.py check --fail-level WARNING`.
_static_dir = BASE_DIR / "static"
STATICFILES_DIRS = [_static_dir] if _static_dir.is_dir() else []

# =============================================================================
# DEFAULT PRIMARY KEY
# =============================================================================
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# =============================================================================
# DJANGO REST FRAMEWORK
# =============================================================================
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_THROTTLE_CLASSES": (
        "security.rate_limiting.BurstRateThrottle",
        "security.rate_limiting.SustainedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "burst": "60/min",
        "sustained": config("RATE_LIMIT_DEFAULT", default="1000/hour"),
    },
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
        # BrowsableAPI disabled in prod — only enabled in development.py
    ),
    "DEFAULT_PARSER_CLASSES": ("rest_framework.parsers.JSONParser",),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.CursorPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "core.exceptions.custom_exception_handler",
    # Never expose raw exception details to clients
    "NON_FIELD_ERRORS_KEY": "errors",
}

# =============================================================================
# JWT CONFIGURATION
# =============================================================================
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=config("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", default=15, cast=int)
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=config("JWT_REFRESH_TOKEN_LIFETIME_DAYS", default=7, cast=int)
    ),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "SIGNING_KEY": config("JWT_SIGNING_KEY", default=SECRET_KEY),
    "ALGORITHM": "HS256",
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    # A dotted-path to a serializer class, not a credential.
    "TOKEN_OBTAIN_SERIALIZER": "security.authentication.SecureTokenObtainPairSerializer",  # nosec B105
}

# =============================================================================
# CORS (Strict — whitelist only)
# =============================================================================
CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS",
    default="http://localhost:3000",
    cast=Csv(),
)
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = False  # Never True in production

# =============================================================================
# SECURITY HEADERS (Strict defaults)
# =============================================================================
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# HSTS — enabled in production settings
SECURE_HSTS_SECONDS = 0  # Overridden in production.py
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

# SSL redirect — enabled in production
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=False, cast=bool)

# Cookie security
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=False, cast=bool)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 3600  # 1 hour

CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=False, cast=bool)
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"

# =============================================================================
# CSP (Content Security Policy)
# =============================================================================
CSP_DEFAULT_SRC = ("'self'",)
CSP_SCRIPT_SRC = ("'self'",)
CSP_STYLE_SRC = ("'self'", "'unsafe-inline'")  # Inline styles for admin
CSP_IMG_SRC = ("'self'", "data:")
CSP_FONT_SRC = ("'self'",)
CSP_CONNECT_SRC = ("'self'",)
CSP_FRAME_SRC = ("'none'",)
CSP_OBJECT_SRC = ("'none'",)
CSP_BASE_URI = ("'self'",)
CSP_FORM_ACTION = ("'self'",)

# =============================================================================
# ADMIN SECURITY
# =============================================================================
ADMIN_IP_WHITELIST = config(
    "ADMIN_IP_WHITELIST",
    default="127.0.0.1,::1",
    cast=Csv(),
)

# =============================================================================
# CELERY
# =============================================================================
CELERY_BROKER_URL = config("CELERY_BROKER_URL", default="redis://127.0.0.1:6379/2")
# Result backend must NOT reuse the broker URL setting - they are separate
# concerns and can point at different Redis instances.
CELERY_RESULT_BACKEND = config(
    "CELERY_RESULT_BACKEND",
    default=config("REDIS_CELERY_URL", default="redis://127.0.0.1:6379/3"),
)
CELERY_ACCEPT_CONTENT = ["json"]  # Only JSON, never pickle (security risk)
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# =============================================================================
# MARKET DATA — Phase 1 (free tier)
# Yahoo is the default because it needs ZERO keys. Angel One's public
# master is also keyless; only Angel QUOTES need credentials (absent today
# by the user's choice, wired for later). Every value has a safe default
# so CI and fresh clones work with no .env entries for these.
# =============================================================================
MARKET_DATA_PROVIDER = config("MARKET_DATA_PROVIDER", default="yahoo")
POLL_INTERVAL_SECONDS = config("POLL_INTERVAL_SECONDS", default=60, cast=int)
STALE_TICK_SECONDS = config("STALE_TICK_SECONDS", default=900, cast=int)
DATA_GAP_SECONDS = config("DATA_GAP_SECONDS", default=300, cast=int)
BAD_TICK_RATIO_ALERT = config("BAD_TICK_RATIO_ALERT", default="0.05")
YAHOO_TIMEOUT_SECONDS = config("YAHOO_TIMEOUT_SECONDS", default=15.0, cast=float)
# Angel One (optional today; required only for Angel quotes/websocket).
# Kept empty by default — MissingCredentials explains exactly what to set.
ANGEL_API_KEY = config("ANGEL_API_KEY", default="")
ANGEL_CLIENT_ID = config("ANGEL_CLIENT_ID", default="")
ANGEL_PASSWORD = config("ANGEL_PASSWORD", default="")
ANGEL_TOTP_SECRET = config("ANGEL_TOTP_SECRET", default="")

# =============================================================================
# SENTRY (Error Tracking)
# =============================================================================
SENTRY_DSN = config("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.redis import RedisIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration(), CeleryIntegration(), RedisIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,  # Never send personally identifiable information
        environment=TRADING_MODE,
    )

# =============================================================================
# LOGGING (Structured JSON)
# =============================================================================
# Typed loosely: Django's LOGGING schema is nested and heterogeneous, and
# the settings modules below mutate it. `dict[str, Any]` keeps those mutations
# statically checkable instead of inferring an unusable `dict[str, object]`.
LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {
            "()": "structlog.stdlib.ProcessorFormatter",
            "processor": "structlog.dev.ConsoleRenderer",
        },
        "json": {
            "()": "structlog.stdlib.ProcessorFormatter",
            "processor": "structlog.processors.JSONRenderer",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "structured",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(BASE_DIR / "logs" / "paper_trading.log"),
            "maxBytes": 10 * 1024 * 1024,  # 10 MB
            "backupCount": 5,
            "formatter": "json",
        },
        "security_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(BASE_DIR / "logs" / "security.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 10,
            "formatter": "json",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": True,
        },
        "django.security": {
            "handlers": ["console", "security_file"],
            "level": "WARNING",
            "propagate": False,
        },
        "core": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "security": {
            "handlers": ["console", "security_file"],
            "level": "INFO",
            "propagate": False,
        },
        "trading": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
}

# Create log directory
(BASE_DIR / "logs").mkdir(exist_ok=True)

# =============================================================================
# TRADING CONFIGURATION (Domain-specific)
# =============================================================================
TRADING = {
    "DEFAULT_CAPITAL": Decimal("1000000.00"),  # 10 lakh virtual rupees
    "MARKET_OPEN_TIME": "09:15",  # IST
    "MARKET_CLOSE_TIME": "15:30",  # IST
    "PRE_OPEN_START": "09:00",
    "PRE_OPEN_END": "09:15",
    "AUTO_SQUARE_OFF_START": "15:15",
    "AUTO_SQUARE_OFF_END": "15:20",
    "TICK_STALENESS_THRESHOLD_SECONDS": 30,
    "MAX_RECONNECT_ATTEMPTS": 10,
    "RECONNECT_BACKOFF_BASE": 2,
}
