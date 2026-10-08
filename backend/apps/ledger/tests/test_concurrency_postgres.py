"""
Concurrency guarantees that only a real database can prove (PostgreSQL row locks).

Each test starts several threads that race the same money operation at the same
moment and checks that the books still balance and nothing is spent twice.
Skipped on SQLite; CI runs them against Postgres (see .github/workflows/ci.yml).
"""

import threading

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Sum

from apps.ledger import accounts
from apps.ledger.exceptions import InsufficientFunds
from apps.ledger.models import JournalEntry, Posting
from apps.ledger.services import credit, debit, natural_balance_of, post_entry, recompute_balance

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="needs PostgreSQL row locking"),
]
User = get_user_model()


def _race(n, fn):
    """Run fn(i) in n threads released together; return the results / exceptions."""
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
        t.join(30)
    return out


def _zero():
    assert (Posting.objects.aggregate(s=Sum("amount"))["s"] or 0) == 0


def test_no_double_spend_under_concurrent_debits():
    import uuid
    a, b = accounts.customer_wallet(str(uuid.uuid4())), accounts.customer_wallet(str(uuid.uuid4()))
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 100_00), credit(a, 100_00)])
    results = _race(10, lambda i: post_entry(f"spend {i}", [debit(a, 30_00), credit(b, 30_00)]))
    ok = [r for r in results if not isinstance(r, Exception)]
    others = [f"{type(r).__name__}: {r}" for r in results if r not in ok and not isinstance(r, InsufficientFunds)]
    assert len(ok) == 3 and not others, others
    assert natural_balance_of(a) == 10_00 and natural_balance_of(b) == 90_00
    assert recompute_balance(a) * a.normal_sign == natural_balance_of(a)
    _zero()


def test_same_idempotency_key_posts_once():
    import uuid
    a = accounts.customer_wallet(str(uuid.uuid4()))
    results = _race(8, lambda i: post_entry("top-up", [debit(accounts.partner_clearing("mock"), 5_00),
                                                        credit(a, 5_00)], idempotency_key="same-key"))
    assert JournalEntry.objects.filter(idempotency_key="same-key").count() == 1
    assert natural_balance_of(a) == 5_00
    assert not [r for r in results if isinstance(r, Exception) and "IntegrityError" not in type(r).__name__]
    _zero()


def test_concurrent_refunds_never_exceed_the_payment(settings):
    from apps.licensing.gate import _enabled_set
    from apps.merchants import onboarding, qr, refunds
    from apps.wallet import merchant_pay
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    _enabled_set.cache_clear()
    owner = User.objects.create_user(phone="+233200000010", full_name="Owner")
    shop = onboarding.create_merchant(owner=owner, legal_name="Shop", business_type="registered")
    onboarding.submit_for_review(shop)
    onboarding.begin_review(shop)
    onboarding.approve(shop)
    kofi = User.objects.create_user(phone="+233244058519")
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 100_00),
                        credit(accounts.customer_wallet(str(kofi.id)), 100_00)])
    p = merchant_pay.pay(user=kofi, code=qr.ensure_short_code(shop), amount_minor=50_00)
    results = _race(6, lambda i: refunds.create_refund(payment=p, amount_minor=20_00, reason="race",
                                                       requested_by=owner))
    ok = [r for r in results if not isinstance(r, Exception)]
    assert len(ok) == 2                                    # 2 × 20 ≤ 50 < 3 × 20
    assert refunds.refunded_minor(p) == 40_00
    _zero()
    _enabled_set.cache_clear()


def test_cash_out_double_approval_pays_once(settings):
    from apps.agents import services as agents
    from apps.agents.models import Agent
    from apps.licensing.gate import _enabled_set
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    _enabled_set.cache_clear()
    au = User.objects.create_user(phone="+233200000060")
    agent = Agent.objects.create(user=au, display_name="Esi", status="active")
    kofi = User.objects.create_user(phone="+233244058519")
    kofi.set_pin("428173")
    kofi.save()
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 100_00),
                        credit(accounts.customer_wallet(str(kofi.id)), 100_00)])
    agents.open_cash_out_window(kofi)
    req = agents.request_cash_out(agent=agent, customer_phone=kofi.phone, amount_minor=60_00)
    results = _race(5, lambda i: agents.approve_cash_out(customer=kofi, request_id=req.id, pin="428173"))
    approved = [r for r in results if not isinstance(r, Exception) and r.status == "approved"]
    assert len(approved) == 1
    assert natural_balance_of(accounts.customer_wallet(str(kofi.id))) == 40_00
    _zero()
    _enabled_set.cache_clear()
