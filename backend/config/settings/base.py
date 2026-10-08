"""
Base settings shared by every environment.

Environment-specific overrides live in dev.py and prod.py. Secrets and anything
that changes per environment are read from environment variables (12-factor),
never hardcoded. This is a direct lesson from the old codebase, which shipped a
Gmail password and DEBUG=True in source.
"""

from pathlib import Path

import environ
from celery.schedules import crontab

# BASE_DIR points at the `backend/` directory.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
)
# Read a .env file if present (development convenience; production injects real env vars).
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# ---------------------------------------------------------------------------
# The licence gate. See apps/licensing. This single value decides which
# capabilities are legally enabled in this deployment.
# ---------------------------------------------------------------------------
SOKOPAY_ACTIVE_LICENCE = env("SOKOPAY_ACTIVE_LICENCE", default="PSP_STANDARD")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "django_filters",
    "corsheaders",
    "django_htmx",
    "drf_spectacular",
    "django_otp",
    "django_otp.plugins.otp_totp",
]

# Order matters for migrations: accounts (custom user) and common load first.
LOCAL_APPS = [
    "apps.common",
    "apps.accounts",
    "apps.licensing",
    "apps.ledger",
    "apps.kyc",
    "apps.notifications",
    "apps.rails",
    "apps.connectors",
    "apps.payments",
    "apps.merchants",
    "apps.agents",
    "apps.wallet",
    "apps.marketplace",
    "apps.safeguarding",
    "apps.reconciliation",
    "apps.bulk",
    "apps.activity",
    "apps.compliance",
    "apps.pricing",
    "apps.ussd",
    "apps.portal",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    "apps.common.audit.RequestIdMiddleware",   # X-Request-ID on every request, response, log line and audit event
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",  # 2FA state for staff/merchant portals
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

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
                "apps.portal.context_processors.staff_roles",
                "apps.common.context_processors.environment",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database — PostgreSQL. The ledger requires real row locking (SELECT ... FOR
# UPDATE), so PostgreSQL is mandatory in every environment that moves money.
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        **env.db("DATABASE_URL"),
        # Keep connections alive but recycle to avoid stale sockets.
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        # Enforce the serializable-enough default; individual money operations
        # additionally take explicit row locks inside a transaction.
        "ATOMIC_REQUESTS": False,
    }
}

# ---------------------------------------------------------------------------
# Caching / Redis
# ---------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://localhost:6379/0"),
    }
}

# ---------------------------------------------------------------------------
# Authentication & passwords
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"  # custom user from day one (avoids painful later migration)

# Argon2 first — stronger than the PBKDF2 default, and far stronger than the old repo.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalisation — Ghana
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Accra"   # GMT, no DST. All timestamps stored in UTC, displayed here.
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Field-level encryption key for PII at rest. See apps/common/encryption.py.
# ---------------------------------------------------------------------------
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")

# ---------------------------------------------------------------------------
# Rail provider (Enhanced PSP partner) configuration. See apps/rails (later).
# ---------------------------------------------------------------------------
RAIL_PROVIDER = env("RAIL_PROVIDER", default="mock")
# Mock rails/connectors (MockRail, mock transfer/identity/remittance connectors) accept
# any input and can credit real ledger accounts. They are only constructible when this
# is True; dev.py turns it on, prod.py forces it off and refuses to boot if any route
# still points at a mock. Default off so a mis-set DJANGO_SETTINGS_MODULE fails safe.
ENVIRONMENT_BANNER = ""      # set on staging: shown on every web page
STAGING_TOOLS = False
ALLOW_MOCK_INTEGRATIONS = env.bool("ALLOW_MOCK_INTEGRATIONS", default=False)

# ---------------------------------------------------------------------------
# Portal security (apps/portal/security.py, apps/portal/ratelimit.py)
# ---------------------------------------------------------------------------
# Merchant roles that must use 2FA (they can move money). Others may opt in.
MERCHANT_2FA_REQUIRED_ROLES = ("owner", "admin", "finance")
# Fraud & AML monitoring (apps/compliance). Override any rule threshold, e.g.
# AML_RULES = {"velocity_out_count": 8}. [VERIFY all thresholds with the MLRO / FIC guidance]
AML_RULES: dict = {}
# After a forgot-PIN reset, sends are capped at this for 24 h (SIM-swap protection). [VERIFY]
PIN_RESET_COOLDOWN_DAILY_MINOR = 500_00
# Closed accounts keep their records this long (AML Act), then are anonymised. [VERIFY]
DATA_RETENTION_YEARS = 5

# Merchant webhooks: allow http:// and private addresses. Dev only, never in production.
WEBHOOK_ALLOW_INSECURE = False
AML_SCREENING_THRESHOLD = 0.88            # fuzzy name-match score for a watchlist hit
AML_AUTO_HOLD_RULES = {"SANCTIONS_MATCH"}  # rules that put the wallet on hold automatically
# Agents get a "Float running low" push when a cash-in takes them below this (pesewas).
AGENT_LOW_FLOAT_MINOR = env.int("AGENT_LOW_FLOAT_MINOR", default=200_00)
# Overrides for portal rate limits: {"name": (max_events, window_seconds)}.
PORTAL_RATE_LIMITS: dict = {}
# Proxies in front of Django that append to X-Forwarded-For (the ALB = 1). 0 = ignore
# X-Forwarded-For (it is client-controlled). Wrong values let attackers dodge IP limits.
TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=0)
RAIL_API_BASE_URL = env("RAIL_API_BASE_URL", default="")
RAIL_API_KEY = env("RAIL_API_KEY", default="")
RAIL_WEBHOOK_SECRET = env("RAIL_WEBHOOK_SECRET", default="")   # MockRail (dev) only


def _rail_partner(prefix: str) -> dict:
    return {
        "base_url": env(f"{prefix}_BASE_URL", default=""),
        "client_id": env(f"{prefix}_CLIENT_ID", default=""),
        "client_secret": env(f"{prefix}_CLIENT_SECRET", default=""),
        "webhook_secret": env(f"{prefix}_WEBHOOK_SECRET", default=""),
        # Where the partner sends callbacks: https://<api-host>/api/v1/rails/<name>/webhook
        "callback_url": env(f"{prefix}_CALLBACK_URL", default=""),
    }


# Real Enhanced PSP partners (apps/rails/korba.py, nsano.py).
RAIL_PARTNERS = {"korba": _rail_partner("KORBA"), "nsano": _rail_partner("NSANO")}
# Partners whose adapter passed sandbox certification (docs/RAILS-INTEGRATION.md).
# Outside dev, an adapter refuses to start unless listed here AND its spec is confirmed.
RAIL_CERTIFIED_PARTNERS = env.list("RAIL_CERTIFIED_PARTNERS", default=[])

# ---------------------------------------------------------------------------
# REST framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "apps.common.drf.sokopay_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Mobile apps authenticate with a JWT access token; session auth remains for the
    # browsable API in development. (Merchant API views override this with API-key auth.)
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "apps.accounts.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "30/min", "user": "120/min"},
}

SPECTACULAR_SETTINGS = {
    "TITLE": "SokoPay API",
    "DESCRIPTION": "SokoPay payments platform API.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# Base money currency for Phase 1. The ledger is multi-currency capable; Ghana
# launches single-currency (GHS).
DEFAULT_CURRENCY = "GHS"

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Celery (background + scheduled jobs). Broker is Redis (ElastiCache in prod).
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=env("REDIS_URL", default="redis://localhost:6379/0"))
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=CELERY_BROKER_URL)
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ACKS_LATE = True            # re-run a task if a worker dies mid-way
CELERY_TASK_REJECT_ON_WORKER_LOST = True

CELERY_BEAT_SCHEDULE = {
    "poll-pending-payments": {
        "task": "apps.payments.tasks.poll_pending_payments",
        "schedule": crontab(minute="*/5"),           # every 5 minutes
    },
    "verify-ledger-integrity": {
        "task": "apps.ledger.tasks.verify_ledger_integrity",
        "schedule": crontab(minute=0),               # hourly, on the hour
    },
    "daily-reconciliation": {
        "task": "apps.reconciliation.tasks.run_daily_reconciliation",
        "schedule": crontab(hour=4, minute=0),       # 04:00 GMT
    },
    "daily-settlements": {
        "task": "apps.merchants.tasks.run_daily_settlements",
        "schedule": crontab(hour=6, minute=0),       # 06:00 GMT
    },
    "poll-external-transfers": {
        "task": "apps.wallet.tasks.poll_external_transfers",
        "schedule": crontab(minute="*/5"),
    },
    "daily-safeguarding-check": {
        "task": "apps.safeguarding.tasks.daily_safeguarding_check",
        "schedule": crontab(hour=5, minute=0),       # 05:00 GMT, after reconciliation
    },
    "ops-snapshot": {
        "task": "apps.common.tasks.ops_snapshot",
        "schedule": crontab(minute="*/5"),
    },
    "pay-agent-commissions": {
        "task": "apps.pricing.tasks.pay_agent_commissions",
        "schedule": crontab(day_of_month="1", hour="2", minute="0"),   # monthly, for last month
    },
    "poll-lifestyle-orders": {
        "task": "apps.wallet.tasks.poll_lifestyle_orders",
        "schedule": crontab(minute="*/5"),
    },
    "poll-cross-border": {
        "task": "apps.wallet.tasks.poll_cross_border",
        "schedule": crontab(minute="*/5"),
    },
    "retry-merchant-webhooks": {
        "task": "apps.merchants.tasks.retry_webhooks",
        "schedule": crontab(minute="*"),               # every minute
    },
    "poll-refund-payouts": {
        "task": "apps.merchants.tasks.poll_refund_payouts",
        "schedule": crontab(minute="*/5"),
    },
    "poll-settlement-payouts": {
        "task": "apps.merchants.tasks.poll_settlement_payouts",
        "schedule": crontab(minute="*/5"),
    },
    "poll-bulk-items": {
        "task": "apps.bulk.tasks.poll_bulk_items",   # bulk payout rows pending at the institution
        "schedule": crontab(minute="*/5"),
    },
    "aml-daily-monitoring": {
        "task": "apps.compliance.tasks.daily_monitoring",   # merchant spikes etc.
        "schedule": crontab(hour=6, minute=30),
    },
    "anonymise-closed-accounts": {
        "task": "apps.accounts.tasks.anonymise_closed_accounts",
        "schedule": crontab(day_of_month=1, hour=2, minute=0),
    },
    "purge-revoked-tokens": {
        "task": "apps.accounts.tasks.purge_revoked_tokens",
        "schedule": crontab(hour=3, minute=30),
    },
}

# ---------------------------------------------------------------------------
# KYC tiers & wallet limits (DEMI). All amounts in pesewas; None = no limit.
# Values follow the Bank of Ghana mobile-money account tiers:
#   Minimum  — max balance GH₵5,000;  daily GH₵3,000;  monthly GH₵10,000
#   Medium   — max balance GH₵40,000; daily GH₵15,000; monthly unlimited
#   Enhanced — max balance GH₵75,000; daily GH₵25,000; monthly unlimited
# BoG publishes no separate per-transaction cap, so a single transaction may be up
# to the daily limit. [VERIFY against the current BoG directive at go-live.]
# ---------------------------------------------------------------------------
KYC_TIER_LIMITS = {
    0: {"name": "Minimum (phone verified)",
        "max_balance": 5_000_00, "max_txn": 3_000_00,
        "daily_out": 3_000_00, "monthly_out": 10_000_00},
    1: {"name": "Medium (Ghana Card verified)",
        "max_balance": 40_000_00, "max_txn": 15_000_00,
        "daily_out": 15_000_00, "monthly_out": None},
    2: {"name": "Enhanced (Ghana Card + selfie)",
        "max_balance": 75_000_00, "max_txn": 25_000_00,
        "daily_out": 25_000_00, "monthly_out": None},
}
# Who verifies Ghana Cards: "nia" (direct) | "kyc_provider" | "mock" (dev).
KYC_IDENTITY_PROVIDER = env("KYC_IDENTITY_PROVIDER", default="nia")

# Signing secret for inbound-remittance partner webhooks (per partner in production).
REMITTANCE_WEBHOOK_SECRET = env("REMITTANCE_WEBHOOK_SECRET", default="")

# Base URL encoded in merchant QR codes (static /m/<code>, dynamic /q/<token>).
QR_BASE_URL = env("QR_BASE_URL", default="https://pay.sokopay.com.gh")

# ---------------------------------------------------------------------------
# Connector routing (see apps.connectors.registry and docs/CONNECTORS.md).
# Each value is a connector key. "rail" = through the Enhanced PSP partner.
# ---------------------------------------------------------------------------
# Airtime & data per telco. Switch a network to its direct connector ("mtn",
# "telecel", "at") once that integration is live.
TELCO_ROUTES = {"mtn": "rail", "telecel": "rail", "at": "rail"}

# Outbound interop transfers from a SokoPay wallet. Most specific key wins:
# "<type>:<institution>" (e.g. "wallet:zeepay") before "<type>".
# Lifestyle partners by category ("" = not offered yet), e.g. {"ticketing": "<key>"}.
LIFESTYLE_PARTNERS = {"ticketing": env("TICKETING_PARTNER", default=""), "food": env("FOOD_PARTNER", default="")}

# USSD aggregator callback secret (required in production).
USSD_SHARED_SECRET = env("USSD_SHARED_SECRET", default="")

# Cross-border partner (Onafriq or Brij) for outbound sends; "" = not offered yet.
CROSS_BORDER_PROVIDER = env("CROSS_BORDER_PROVIDER", default="")

INTEROP_ROUTES = {
    "momo": "rail",          # MTN MoMo / Telecel Cash / AT Money via the partner
    "bank": "ghipss_gip",    # GhIPSS Instant Pay (placeholder until live)
    "wallet": "ghipss_mmi",  # other fintech wallets via GhIPSS MMI (placeholder)
}

# Push notifications. Provider: console (dev) | fcm (Firebase Cloud Messaging).
PUSH_PROVIDER = env("PUSH_PROVIDER", default="console")
FCM_PROJECT_ID = env("FCM_PROJECT_ID", default="")
FCM_SERVICE_ACCOUNT_FILE = env("FCM_SERVICE_ACCOUNT_FILE", default="")

# SMS (OTP delivery and alerts). Provider: console | arkesel | hubtel | mnotify.
# Credentials come from the environment; nothing secret lives in code.
# ---------------------------------------------------------------------------
SMS_PROVIDER = env("SMS_PROVIDER", default="console")
SMS_SENDER_ID = env("SMS_SENDER_ID", default="SokoPay")
ARKESEL_API_KEY = env("ARKESEL_API_KEY", default="")
# Sender ID approved on the Arkesel dashboard (max 11 chars); falls back to SMS_SENDER_ID.
ARKESEL_SENDER_ID = env("ARKESEL_SENDER_ID", default="")
ARKESEL_SANDBOX = env.bool("ARKESEL_SANDBOX", default=False)  # accepted + logged by Arkesel, not delivered
HUBTEL_CLIENT_ID = env("HUBTEL_CLIENT_ID", default="")
HUBTEL_CLIENT_SECRET = env("HUBTEL_CLIENT_SECRET", default="")
MNOTIFY_API_KEY = env("MNOTIFY_API_KEY", default="")
# How long an SMS one-time code stays valid (app sign-in, PIN reset, portal 2FA).
OTP_TTL_SECONDS = env.int("OTP_TTL_SECONDS", default=10 * 60)
# Portal 2FA by SMS: the 2FA page texts a code to the user's phone. Authenticator-app
# and backup codes keep working alongside it. On by default on servers (hardened.py).
PORTAL_2FA_SMS = env.bool("PORTAL_2FA_SMS", default=False)
