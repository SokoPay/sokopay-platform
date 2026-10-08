"""
Access guards for the portals.

- staff_required: the user must be a SokoPay staff user (admin/back-office portal).
- merchant_required: the user must belong to a merchant; their merchant is attached
  to request.merchant and their role to request.merchant_role. Owner/Admin/Finance
  (and anyone who has enrolled) must have passed 2FA this session — see security.py.

These are coarse gates for *reaching* a portal. Fine-grained, per-action permission
checks (e.g. only Finance may request a settlement) live in the views, mirroring the
role model in apps.merchants.
"""

from __future__ import annotations

import functools

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .security import needs_2fa


def staff_required(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("portal:login")
        if getattr(request.user, "user_type", None) != "staff":
            raise PermissionDenied("Staff access only.")
        # Staff must complete two-factor verification for this session.
        if not request.user.is_verified():
            return redirect("portal:twofa")
        return view(request, *args, **kwargs)

    return wrapper


STAFF_ROLES = ("compliance", "operations", "support", "finance")


def has_staff_role(user, *roles: str) -> bool:
    """Superusers can do everything; other staff need membership of one of the groups."""
    if getattr(user, "is_superuser", False):
        return True
    return user.groups.filter(name__in=roles).exists()


def staff_role_required(*roles: str):
    """
    staff_required + a role: e.g. @staff_role_required("compliance") for AML case work.
    Roles are Django groups (created by migration portal.0002): compliance, operations,
    support, finance. Need-to-know: support staff can't read AML cases, and so on.
    """
    def decorator(view):
        @staff_required
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if not has_staff_role(request.user, *roles):
                raise PermissionDenied("Your staff role doesn't include this area.")
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


def merchant_required(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("portal:login")
        membership = request.user.merchant_memberships.select_related("merchant").first()
        if membership is None:
            raise PermissionDenied("No merchant is linked to this account.")
        # Money-moving roles (and anyone who has enrolled) must pass 2FA this session.
        if not request.user.is_verified() and needs_2fa(request.user, membership.role):
            return redirect("portal:twofa")
        request.merchant = membership.merchant
        request.merchant_role = membership.role
        return view(request, *args, **kwargs)

    return wrapper
