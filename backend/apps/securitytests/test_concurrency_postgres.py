"""
Limit bypass by racing: simultaneous debits from one wallet must not get round a cap
between them (each passing the check before any is posted). Needs PostgreSQL.
"""

import threading

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone

from apps.ledger import accounts
from apps.ledger.services import credit, debit, post_entry
from apps.wallet import services as wallet

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="needs PostgreSQL"),
]
User = get_user_model()


def _race(n, fn):
    barrier, out, lock = threading.Barrier(n), [], threading.Lock()

    def run(i):
        try:
            barrier.wait()
            r = fn(i)
        except Exception as exc:   # noqa: BLE001 - collected for the assertions
            r = exc
        finally:
            connection.close()
        with lock:
            out.append(r)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return out


def test_racing_sends_cannot_exceed_the_post_reset_cap(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.PIN_RESET_COOLDOWN_DAILY_MINOR = 100_00          # GH₵ 100 in the 24 h after a PIN reset
    from apps.licensing.gate import _enabled_set
    _enabled_set.cache_clear()
    kofi = User.objects.create_user(phone="+233244000201", full_name="Kofi")
    ama = User.objects.create_user(phone="+233244000202", full_name="Ama")
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 1_000_00),
                        credit(accounts.customer_wallet(str(kofi.id)), 1_000_00)])
    User.objects.filter(pk=kofi.pk).update(pin_reset_at=timezone.now())
    kofi.refresh_from_db()

    results = _race(6, lambda i: wallet.send_p2p(sender=User.objects.get(pk=kofi.pk), recipient_phone=ama.phone,
                                                  amount_minor=40_00))
    sent = 1_000_00 - wallet.balance(kofi)
    assert sent <= 100_00, f"cap bypassed: GH₵ {sent / 100:.2f} sent; results={results}"
    assert sum(1 for r in results if isinstance(r, dict)) == 2
    _enabled_set.cache_clear()
