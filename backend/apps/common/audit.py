"""
Audit trail and request tracing.

record(action, actor=..., obj=..., summary=..., **data)
    Append one AuditEvent for a privileged or sensitive action. Never raises into the
    caller's business logic (an audit failure is logged CRITICAL instead), but it is
    written in the caller's transaction, so a rolled-back action leaves no audit row.

RequestIdMiddleware
    Gives every HTTP request an id (the incoming X-Request-ID if well-formed, else new),
    returns it in the X-Request-ID response header, and makes it available to logs
    (RequestIdLogFilter) and to audit events — so one customer complaint can be followed
    from the app, through the API log lines, to the audit trail.
"""

from __future__ import annotations

import contextvars
import logging
import re
import uuid

logger = logging.getLogger("sokopay.audit")

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
_client_ip: contextvars.ContextVar[str] = contextvars.ContextVar("client_ip", default="")
_VALID = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


def current_request_id() -> str:
    return _request_id.get()


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-ID", "")
        rid = incoming if _VALID.match(incoming or "") else uuid.uuid4().hex
        request.request_id = rid
        t1 = _request_id.set(rid)
        try:
            from apps.portal.ratelimit import client_ip
            ip = client_ip(request)
        except Exception:   # noqa: BLE001
            ip = ""
        t2 = _client_ip.set(ip)
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(t1)
            _client_ip.reset(t2)
        response["X-Request-ID"] = rid
        return response


class RequestIdLogFilter(logging.Filter):
    """Adds request_id to every log record (empty outside a request)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = _request_id.get()
        return True


def _label(user) -> str:
    if user is None:
        return "system"
    parts = [getattr(user, "full_name", "") or "", getattr(user, "phone", "") or ""]
    return " · ".join(p for p in parts if p)[:160] or str(user.pk)


def record(action: str, *, actor=None, obj=None, summary: str = "", **data):
    """Append an audit event. `obj` may be a model instance or a (type, id) tuple."""
    from .models import AuditEvent
    if obj is None:
        otype, oid = "", ""
    elif isinstance(obj, tuple):
        otype, oid = obj
    else:
        otype, oid = obj._meta.label_lower, str(obj.pk)
    safe = {k: (v if isinstance(v, (int, float, bool, type(None))) else str(v)[:500]) for k, v in data.items()}
    from django.db import transaction
    try:
        with transaction.atomic():          # savepoint: a failed insert can't poison the caller's transaction
            return AuditEvent.objects.create(
                actor=actor if getattr(actor, "pk", None) else None, actor_label=_label(actor), action=action[:64],
                object_type=str(otype)[:48], object_id=str(oid)[:64], summary=(summary or action)[:255], data=safe,
                request_id=_request_id.get(), ip=_client_ip.get())
    except Exception:   # noqa: BLE001 - never break the action; make the gap loud instead
        logger.critical("AUDIT WRITE FAILED for %s on %s:%s", action, otype, oid, exc_info=True)
        return None


def audited(action: str, *, actor: str | None = "actor", obj: str | None = None, fields: tuple = ()):
    """
    Decorator: after the wrapped function returns successfully, append an AuditEvent.
      actor   name of the keyword argument holding the acting user (None = system)
      obj     name of the argument holding the object acted on (default: the return value,
              else the first positional argument)
      fields  keyword arguments to copy into the event data (e.g. "reason", "approve")
    """
    import functools
    import inspect

    def wrap(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def inner(*args, **kwargs):
            result = fn(*args, **kwargs)
            try:
                bound = sig.bind_partial(*args, **kwargs)
                who = bound.arguments.get(actor) if actor else None
                target = bound.arguments.get(obj) if obj else None
                if target is None:
                    target = result if hasattr(result, "_meta") else (args[0] if args and hasattr(args[0], "_meta") else None)
                if isinstance(target, tuple) and target and hasattr(target[0], "_meta"):
                    target = target[0]
                data = {f: bound.arguments.get(f) for f in fields if f in bound.arguments}
                bits = ", ".join(f"{k}={v}" for k, v in data.items() if v not in (None, ""))
                record(action, actor=who, obj=target, summary=f"{action}{(': ' + bits) if bits else ''}", **data)
            except Exception:   # noqa: BLE001
                logger.critical("AUDIT WRITE FAILED for %s", action, exc_info=True)
            return result
        return inner
    return wrap
