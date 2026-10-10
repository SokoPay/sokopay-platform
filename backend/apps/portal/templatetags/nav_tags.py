"""Sidebar helpers: highlight the menu item for the page being viewed."""

from django import template
from django.urls import NoReverseMatch, reverse

register = template.Library()

ACTIVE = "bg-white/10 text-white font-medium"
IDLE = "text-white/70 hover:text-white hover:bg-white/5"


@register.inclusion_tag("portal/_nav_link.html", takes_context=True)
def nav_link(context, url_name: str, label: str, exact: bool = False):
    """A menu link; `exact` for section home pages (otherwise sub-pages count as active)."""
    try:
        href = reverse(url_name)
    except NoReverseMatch:
        return {"href": "", "label": label, "css": IDLE, "current": False}
    path = context["request"].path
    current = path == href if exact else path.startswith(href)
    return {"href": href, "label": label, "css": ACTIVE if current else IDLE, "current": current}
