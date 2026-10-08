"""
Legal pages: /legal/ (index) and /legal/<name> (privacy-policy, terms-of-service, ...).

The text lives in docs/legal/*.md (the originals) and is copied to backend/legal/ so it
ships with the server image; apps.common.tests.test_legal fails if the copies differ.
The apps open these pages in the browser; the portal and payment pages link to them.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import markdown
from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.utils.safestring import mark_safe

LEGAL_DIR = Path(settings.BASE_DIR) / "legal"
DOCUMENTS = {
    "privacy-policy": "Privacy Policy",
    "terms-of-service": "Terms of Service",
    "merchant-terms": "Merchant Terms",
    "agent-terms": "Agent Terms",
    "complaints-and-disputes": "Complaints and Disputes",
    "acceptable-use": "Acceptable Use Policy",
}


@lru_cache(maxsize=16)
def _html(name: str) -> str:
    text = (LEGAL_DIR / f"{name}.md").read_text(encoding="utf-8")
    # Our own files (not user input); rendered once per process.
    return markdown.markdown(text, extensions=["tables", "sane_lists"])


def index(request):
    return render(request, "legal/index.html", {"docs": DOCUMENTS})


def page(request, name: str):
    if name not in DOCUMENTS:
        raise Http404
    return render(request, "legal/page.html", {"title": DOCUMENTS[name], "body": mark_safe(_html(name)),  # nosec B308 B703
                                               "docs": DOCUMENTS})
