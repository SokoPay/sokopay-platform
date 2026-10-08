"""
Tests for the licence-capability gate — the mechanism that lets us build every
feature now and switch each on only when its licence is granted.
"""

import pytest
from django.test import override_settings

from apps.licensing.capabilities import Capability, Licence, capabilities_for
from apps.licensing.exceptions import CapabilityNotLicensed
from apps.licensing.gate import _enabled_set, is_enabled, require_capability


@pytest.fixture(autouse=True)
def _clear_cache():
    # The gate caches capability sets per licence; clear between tests so
    # override_settings takes effect.
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


def test_standard_permits_bill_pay_but_not_wallet():
    with override_settings(SOKOPAY_ACTIVE_LICENCE="PSP_STANDARD"):
        assert is_enabled(Capability.BILL_PAYMENT)
        assert is_enabled(Capability.AIRTIME_DATA)
        # Holding customer funds is a DEMI activity — must be blocked at Standard.
        assert not is_enabled(Capability.HOLD_CUSTOMER_FUNDS)
        assert not is_enabled(Capability.MERCHANT_AGGREGATION)


def test_require_capability_raises_when_not_licensed():
    with override_settings(SOKOPAY_ACTIVE_LICENCE="PSP_STANDARD"):
        with pytest.raises(CapabilityNotLicensed):
            require_capability(Capability.CASH_IN_OUT)


def test_medium_adds_aggregation_cumulatively():
    with override_settings(SOKOPAY_ACTIVE_LICENCE="PSP_MEDIUM"):
        # keeps Standard's capabilities...
        assert is_enabled(Capability.BILL_PAYMENT)
        # ...and gains Medium's
        assert is_enabled(Capability.MERCHANT_AGGREGATION)
        assert is_enabled(Capability.CARD_ACQUIRING)
        # still not DEMI
        assert not is_enabled(Capability.WALLET_P2P)


def test_demi_unlocks_wallet_and_agents():
    with override_settings(SOKOPAY_ACTIVE_LICENCE="DEMI"):
        assert is_enabled(Capability.HOLD_CUSTOMER_FUNDS)
        assert is_enabled(Capability.WALLET_P2P)
        assert is_enabled(Capability.CASH_IN_OUT)
        assert is_enabled(Capability.AGENT_NETWORK)
        # and everything below it, since the ladder is cumulative
        assert is_enabled(Capability.MERCHANT_AGGREGATION)
        assert is_enabled(Capability.BILL_PAYMENT)


def test_capabilities_are_strictly_growing_up_the_ladder():
    std = capabilities_for(Licence.PSP_STANDARD)
    med = capabilities_for(Licence.PSP_MEDIUM)
    enh = capabilities_for(Licence.PSP_ENHANCED)
    demi = capabilities_for(Licence.DEMI)
    assert std < med < enh < demi  # each is a strict superset of the previous


def test_cross_border_papss_requires_enhanced():
    with override_settings(SOKOPAY_ACTIVE_LICENCE="PSP_MEDIUM"):
        assert not is_enabled(Capability.CROSS_BORDER_PAPSS)
    _enabled_set.cache_clear()
    with override_settings(SOKOPAY_ACTIVE_LICENCE="PSP_ENHANCED"):
        assert is_enabled(Capability.CROSS_BORDER_PAPSS)
