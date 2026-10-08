import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    """OTP state lives in the cache; clear it between tests for isolation."""
    cache.clear()
    yield
    cache.clear()
