"""Public hosted-checkout pages (served on the QR_BASE_URL host: /m/, /q/, /l/, /c/)."""

from django.urls import path

from . import hosted

app_name = "checkout"

urlpatterns = [
    path("m/<str:key>", hosted.page, {"kind": "m"}, name="shop"),
    path("q/<str:key>", hosted.page, {"kind": "q"}, name="request"),
    path("l/<str:key>", hosted.page, {"kind": "l"}, name="link"),
    path("c/<str:key>", hosted.page, {"kind": "c"}, name="session"),
    path("checkout/wait/<str:reference>", hosted.wait, name="wait"),
    path("checkout/status/<str:reference>", hosted.status_json, name="status"),
]
