"""
USSD gateway callback.

  POST /api/v1/ussd/callback?key=<USSD_SHARED_SECRET>
       form fields (Africa's Talking-style, also used by several Ghanaian aggregators):
       sessionId, serviceCode, phoneNumber, text   (text = every entry so far, joined by "*")
       → text/plain "CON <screen>" or "END <screen>"

[VERIFY] the chosen aggregator's exact format and how it authenticates (shared secret,
IP allow-list or both). The caller's number is taken from the gateway, never typed.
"""

from __future__ import annotations

import hmac

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.bulk.validate import normalise_phone

from . import engine


def _authorised(request) -> bool:
    secret = getattr(settings, "USSD_SHARED_SECRET", "") or ""
    if not secret:
        return bool(getattr(settings, "ALLOW_MOCK_INTEGRATIONS", False))   # dev only
    given = request.GET.get("key", "") or request.headers.get("X-Ussd-Key", "")
    return hmac.compare_digest(given.encode(), secret.encode())


@csrf_exempt
@require_POST
def callback(request):
    if not _authorised(request):
        return HttpResponseForbidden("forbidden")
    session_id = (request.POST.get("sessionId") or "")[:64]
    phone = normalise_phone(request.POST.get("phoneNumber") or "")
    if not session_id or phone is None:
        return HttpResponse("END Sorry, we couldn't read your number.", content_type="text/plain")
    text = request.POST.get("text") or ""
    parts = text.split("*") if text else []
    seen_key = f"ussd-seen:{session_id}"
    seen = cache.get(seen_key, 0)
    entry = parts[-1] if len(parts) > seen else ""
    screen = engine.handle(session_id=session_id, phone=phone, entry=entry)
    if screen.end:
        cache.delete(seen_key)
    else:
        cache.set(seen_key, len(parts), timeout=engine.SESSION_TTL)
    return HttpResponse(screen.render(), content_type="text/plain; charset=utf-8")
