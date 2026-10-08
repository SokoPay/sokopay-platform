"""
Staging / pilot-test settings for an internet-facing test server (e.g. a DigitalOcean
droplet, see docs/DEPLOY-DIGITALOCEAN.md).

Everything production has (hardened.py): DEBUG off, HTTPS only, secure cookies, an
explicit host list, encryption key required, PostgreSQL, Redis, real Celery workers,
JSON logs. The ONE difference: until a payment partner is contracted and certified,
staging may run against the MOCK partner, identity check and connectors, so the whole
system can be tested end to end with demo data. Every page then shows a
"TEST ENVIRONMENT" banner, and the mock-partner page needs a superuser with 2FA.

Never put real customer data or real money on a staging server.
"""

from .hardened import *  # noqa: F401,F403
from .hardened import env

SOKOPAY_ENVIRONMENT = "staging"

# Mock integrations allowed here (and ONLY here, besides local development).
ALLOW_MOCK_INTEGRATIONS = env.bool("ALLOW_MOCK_INTEGRATIONS", default=True)
STAGING_TOOLS = ALLOW_MOCK_INTEGRATIONS          # the /dev/mock-partner/ page (superuser + 2FA only)
if ALLOW_MOCK_INTEGRATIONS:
    RAIL_PROVIDER = env("RAIL_PROVIDER", default="mock")
    KYC_IDENTITY_PROVIDER = env("KYC_IDENTITY_PROVIDER", default="mock")
    INTEROP_ROUTES = {"momo": "rail", "bank": env("INTEROP_BANK", default="mock"),
                      "wallet": env("INTEROP_WALLET", default="mock")}
    CROSS_BORDER_PROVIDER = env("CROSS_BORDER_PROVIDER", default="mock")
    LIFESTYLE_PARTNERS = {"ticketing": env("TICKETING_PARTNER", default="mock_ticketing"),
                          "food": env("FOOD_PARTNER", default="mock_food")}

ENVIRONMENT_BANNER = env("ENVIRONMENT_BANNER",
                         default="TEST ENVIRONMENT · demo data only · no real money moves")

# A test domain must not be pinned into browsers for a year (HSTS preload): short HSTS.
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
