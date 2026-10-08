"""
Representative data for a backup-restore drill (dev/drill databases only, never production).

    DATABASE_URL=... python manage.py shell -c "exec(open('scripts/dr_drill_seed.py').read())"

Creates customers with wallets, an agent, a merchant, and runs real money flows through
the normal services (cash-in, P2P, scan-to-pay, refund, cash-out), so the backup holds
a ledger with many accounts and entries, not an empty schema.
"""

from django.conf import settings
from django.contrib.auth import get_user_model

assert settings.DEBUG or getattr(settings, "ALLOW_MOCK_INTEGRATIONS", False), "drill seed refuses to run here"

from apps.agents import services as agents  # noqa: E402
from apps.agents.models import Agent  # noqa: E402
from apps.ledger import accounts  # noqa: E402
from apps.ledger.services import credit, debit, post_entry  # noqa: E402
from apps.licensing.gate import _enabled_set  # noqa: E402
from apps.merchants import onboarding, qr, refunds  # noqa: E402
from apps.wallet import merchant_pay  # noqa: E402
from apps.wallet import services as wallet  # noqa: E402

settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
_enabled_set.cache_clear()
User = get_user_model()
PIN = "428173"

agent_user = User.objects.create_user(phone="+233200900001", full_name="Drill Agent")
agent = Agent.objects.create(user=agent_user, display_name="Drill Kiosk", status="active")
post_entry("Drill float", [debit(accounts.partner_clearing("mock"), 50_000_00),
                           credit(accounts.agent_float(str(agent.id)), 50_000_00)])
owner = User.objects.create_user(phone="+233200900002", full_name="Drill Owner")
shop = onboarding.create_merchant(owner=owner, legal_name="Drill Shop Ltd", business_type="registered")
onboarding.submit_for_review(shop)
onboarding.begin_review(shop)
onboarding.approve(shop)
code = qr.ensure_short_code(shop)

customers = []
for i in range(40):
    u = User.objects.create_user(phone=f"+2332449{i:05d}", full_name=f"Drill Customer {i}")
    u.set_pin(PIN)
    u.save()
    customers.append(u)
    agents.cash_in(agent=agent, customer_phone=u.phone, amount_minor=500_00 + i * 10_00)

for i, u in enumerate(customers):
    wallet.send_p2p(sender=u, recipient_phone=customers[(i + 1) % len(customers)].phone, amount_minor=25_00)
    p = merchant_pay.pay(user=u, code=code, amount_minor=40_00 + i * 50)
    if i % 5 == 0:
        refunds.create_refund(payment=p, amount_minor=10_00, reason="Drill refund", requested_by=owner)
    if i % 4 == 0:
        agents.open_cash_out_window(u)
        r = agents.request_cash_out(agent=agent, customer_phone=u.phone, amount_minor=60_00)
        agents.approve_cash_out(customer=u, request_id=r.id, pin=PIN)

print("drill data ready:", len(customers), "customers")
