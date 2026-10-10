"""
Hardening shared by every internet-facing environment (production AND staging):
DEBUG off, HTTPS everywhere, secure cookies, HSTS, no wildcard CORS, an explicit host
allow-list, the field-encryption key required, JSON logs. Not used on its own:
config.settings.prod adds the "real partners only" checks, config.settings.staging
allows the mock partner for testing.
"""

import sentry_sdk

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False  # never True in production, regardless of env

# Personal data (Ghana Card numbers) is encrypted with this key; refuse to start without it.
from django.core.exceptions import ImproperlyConfigured  # noqa: E402

if not env("FIELD_ENCRYPTION_KEY", default=""):
    raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is required in production.")

# Real SMS providers need their credentials; fail at start-up, not at the first sign-in.
_SMS = env("SMS_PROVIDER", default="console").lower()
if _SMS == "arkesel" and not env("ARKESEL_API_KEY", default=""):
    raise ImproperlyConfigured("SMS_PROVIDER=arkesel needs ARKESEL_API_KEY.")
PORTAL_2FA_SMS = env.bool("PORTAL_2FA_SMS", default=True)

# The JSON API accepts app tokens only; portal session cookies never authenticate it.
REST_FRAMEWORK = {**REST_FRAMEWORK,  # noqa: F405
                  "DEFAULT_AUTHENTICATION_CLASSES": ("apps.accounts.authentication.JWTAuthentication",)}

# Required: an explicit host allow-list (no wildcards).
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# --- HTTPS / transport security ---
SECURE_SSL_REDIRECT = True
# Container / load-balancer health checks call these over plain HTTP from inside.
SECURE_REDIRECT_EXEMPT = [r"^healthz$", r"^readyz$"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # behind the ALB
# Exactly one trusted proxy (the ALB) appends the client IP to X-Forwarded-For;
# rate limits key on that entry. Change only if CloudFront etc. is put in front.
TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=1)
SECURE_HSTS_SECONDS = 31_536_000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# --- Cookies ---
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 30 * 60  # 30 minutes for staff/merchant portal sessions

# --- CORS: explicit allow-list only (old repo used CORS_ORIGIN_ALLOW_ALL = True) ---
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
# Our own HTTPS origin(s) for portal and checkout form posts behind the proxy.
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
CORS_URLS_REGEX = r"^/api/.*$"

# --- Email (transactional provider, not a personal Gmail) ---
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="SokoPay <no-reply@sokopay.com.gh>")

# --- Error monitoring ---
SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=0.1,
        send_default_pii=False,  # never ship PII to the error tracker
        environment=env("SENTRY_ENVIRONMENT", default="production"),
    )

# One JSON object per log line: CloudWatch metric filters read fields such as the
# 5-minute ops snapshot (apps.common.health) — see infra/monitoring.tf.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "apps.common.logging.JsonFormatter"}},
    "filters": {"request_id": {"()": "apps.common.audit.RequestIdLogFilter"}},
    "handlers": {"stdout": {"class": "logging.StreamHandler", "formatter": "json", "filters": ["request_id"]}},
    "root": {"handlers": ["stdout"], "level": "INFO"},
    "loggers": {
        "django.request": {"level": "WARNING"},
        "django.db.backends": {"level": "WARNING"},
    },
}
