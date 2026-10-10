"""
Root URL configuration.

The admin path is deliberately non-obvious (not /admin/) as a small extra hurdle,
but real protection comes from staff 2FA and IP allow-listing, not obscurity.
"""

from django.contrib import admin
from django.http import JsonResponse
from django.conf import settings
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.common import legal
from apps.common.health import readyz


def health(_request):
    """Liveness probe for the load balancer. Returns no sensitive information."""
    return JsonResponse({"status": "ok", "service": "sokopay-api"})


urlpatterns = [
    path("healthz", health, name="health"),
    path("readyz", readyz, name="ready"),
    path("legal/", legal.index, name="legal"),
    path("legal/<str:name>", legal.page, name="legal-page"),
    path("sp-admin/", admin.site.urls),
    # API documentation (schema public-safe; access-controlled in production).
    # Feature app routes.
    path("api/v1/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.payments.urls")),
    path("api/v1/", include("apps.merchants.urls")),
    path("api/v1/", include("apps.agents.urls")),
    path("api/v1/", include("apps.wallet.urls")),
    path("api/v1/", include("apps.marketplace.urls")),
    path("api/v1/", include("apps.kyc.urls")),
    path("api/v1/", include("apps.notifications.urls")),
    path("api/v1/", include("apps.activity.urls")),
    path("api/v1/", include("apps.ussd.urls")),
    path("dashboard/", include("apps.portal.urls")),   # merchant + admin portals
    path("", include("apps.merchants.checkout_urls")),  # hosted checkout: /m/ /q/ /l/ /c/
]

# Development / demo only: the mock payment partner (apps/common/devtools.py). Never in production.

from apps.common import devtools  # noqa: E402

if devtools.enabled():
    urlpatterns.append(path("dev/mock-partner/", devtools.mock_partner, name="dev-mock-partner"))

# The API description is for developers: served only when DEBUG is on, never on servers.
if settings.DEBUG:
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    ]
