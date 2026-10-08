"""Template context shared by every page."""

from django.conf import settings


def environment(request):
    """A banner on every page of a non-production environment (staging / pilot test)."""
    return {"environment_banner": getattr(settings, "ENVIRONMENT_BANNER", "")}
