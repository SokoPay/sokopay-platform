"""
Development settings. Convenient, not hardened. Never used in production
(production sets DJANGO_SETTINGS_MODULE=config.settings.prod explicitly).
"""

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = True

# A fixed, clearly-insecure key so encrypted fields work out of the box in development
# and tests. Production must supply its own (prod.py refuses to start without one).
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="") or "dev-insecure-field-key"
# For LAN testing (e.g. the Flutter app on a physical phone), add your machine's
# LAN IP via the ALLOWED_HOSTS env var rather than binding to all interfaces here.
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# Use an in-memory cache in development and tests so a local Redis is not required
# just to run the app or the suite. Production uses Redis (see base.py).
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}

# Mock rails and connectors are fine locally (never in prod.py).
ALLOW_MOCK_INTEGRATIONS = True

# In development, route the not-yet-integrated transfer destinations to the mock
# connector so the whole wallet → bank / fintech flow can be exercised locally.
INTEROP_ROUTES = {"momo": "rail", "bank": "mock", "wallet": "mock"}

# Ghana Card checks use the mock identity connector in development.
KYC_IDENTITY_PROVIDER = env("KYC_IDENTITY_PROVIDER", default="mock")

# Run Celery tasks inline in development/tests — no worker or broker needed.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Email goes to the console during development.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Allow the local Flutter app and portals to call the API during development.
CORS_ALLOWED_ORIGINS = env(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:3000", "http://localhost:8080"],
)

# Browsable API is handy in development only.
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]

CROSS_BORDER_PROVIDER = "mock"
LIFESTYLE_PARTNERS = {"ticketing": "mock_ticketing", "food": "mock_food"}

# Show SokoPay's own INFO logs in the server console: in development the "SMS" with each
# sign-in code is printed here ([SMS:console] ...), as are pushes and webhooks.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"sokopay": {"handlers": ["console"], "level": "INFO"}},
}
