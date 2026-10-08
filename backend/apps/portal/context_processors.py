"""Expose the signed-in staff member's roles to templates (to show only their menus)."""

from .decorators import STAFF_ROLES


def staff_roles(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or getattr(user, "user_type", None) != "staff":
        return {"staff_roles": set()}
    if user.is_superuser:
        return {"staff_roles": set(STAFF_ROLES)}
    return {"staff_roles": set(user.groups.values_list("name", flat=True))}
