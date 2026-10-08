"""Template helpers for formatting money consistently in the portals."""

from django import template

from apps.common.money import Money

register = template.Library()


@register.filter
def ghs(minor):
    """Format an integer number of pesewas as 'GH₵ 1,234.56'."""
    try:
        return Money(int(minor), "GHS").format()
    except (TypeError, ValueError):
        return minor
