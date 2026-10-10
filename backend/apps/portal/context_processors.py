"""Template context for the portals: the staff member's roles, and whether to show the menu."""

from .decorators import STAFF_ROLES


def staff_roles(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"staff_roles": set(), "show_nav": False}
    show_nav = _passed_second_factor(user)
    if getattr(user, "user_type", None) != "staff":
        membership = user.merchant_memberships.select_related("merchant").first() if show_nav else None
        business = (membership.merchant.trading_name or membership.merchant.legal_name) if membership else ""
        return {"staff_roles": set(), "show_nav": show_nav, "nav_business": business}
    if user.is_superuser:
        return {"staff_roles": set(STAFF_ROLES), "show_nav": show_nav}
    return {"staff_roles": set(user.groups.values_list("name", flat=True)), "show_nav": show_nav}


def _passed_second_factor(user) -> bool:
    """The menu appears only after 2FA (when the person needs it): nothing is revealed before."""
    from .security import needs_2fa
    if getattr(user, "is_verified", lambda: False)():
        return True
    if getattr(user, "user_type", None) == "staff":
        return False
    membership = user.merchant_memberships.first() if hasattr(user, "merchant_memberships") else None
    return membership is not None and not needs_2fa(user, membership.role)
