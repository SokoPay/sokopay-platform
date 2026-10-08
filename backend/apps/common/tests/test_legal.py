"""Legal pages render, and the served copies never drift from docs/legal (the originals)."""

from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client

from apps.common.legal import DOCUMENTS

DOCS = Path(settings.BASE_DIR).parent / "docs" / "legal"


@pytest.mark.parametrize("name", list(DOCUMENTS))
def test_each_page_renders(name):
    r = Client().get(f"/legal/{name}")
    assert r.status_code == 200 and b"<h1>" in r.content
    assert b"<table" in r.content or b"<ul>" in r.content


def test_index_and_unknown():
    c = Client()
    assert all(n.encode() in c.get("/legal/").content for n in DOCUMENTS)
    assert c.get("/legal/secrets").status_code == 404
    assert c.get("/legal/..%2Fsettings").status_code == 404


@pytest.mark.skipif(not DOCS.exists(), reason="docs folder not shipped (e.g. inside the server image)")
@pytest.mark.parametrize("name", list(DOCUMENTS))
def test_served_copy_matches_the_docs_original(name):
    served = (Path(settings.BASE_DIR) / "legal" / f"{name}.md").read_text(encoding="utf-8")
    original = (DOCS / f"{name}.md").read_text(encoding="utf-8")
    assert served == original, f"backend/legal/{name}.md differs from docs/legal: run cp docs/legal/*.md backend/legal/"
