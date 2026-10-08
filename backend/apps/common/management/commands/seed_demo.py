"""
Demo data for walking through every SokoPay app (development / demo databases ONLY).

    python manage.py seed_demo            # into an empty database (after migrate)
    python manage.py seed_demo --out demo-credentials.md

Creates staff for every back-office role, two merchants (one live, one waiting for
review), agents, customers on each KYC tier, and real activity through the normal
services: cash-ins, transfers, bills, airtime, merchant payments (wallet and MoMo),
refunds, disputes, a settlement waiting for approval, cash-outs, cross-border sends,
tickets and food, savings, an AML case with a draft STR, a pending price change,
reconciliation breaks and a safeguarding check. Everything a guide step needs exists.

Refuses to run unless DEBUG or ALLOW_MOCK_INTEGRATIONS is on, and refuses if the demo
data is already there. All passwords, PINs and 2FA secrets are DEMO values.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

DEMO_PASSWORD = "SokoDemo#2026"     # staff and merchant portal (DEMO ONLY, local machines)
DEMO_PIN = "428173"                 # every app user's PIN (DEMO ONLY, local machines)
# On an internet-facing server (DEBUG off) the fixed values above are public (they are in the
# demo guide), so the seed generates random ones and prints them only on that server.

User = get_user_model()


def _totp_key(phone: str) -> tuple[str, str]:
    """Demo 2FA secret: (hex for django-otp, base32 for an authenticator app).
    Fixed per phone on a local machine (so the guide can list it); random on a server."""
    if settings.DEBUG:
        raw = hashlib.sha1(f"sokopay-demo-2fa:{phone}".encode()).digest()   # nosec B324 - demo secret only
    else:
        raw = secrets.token_bytes(20)
    return raw.hex(), base64.b32encode(raw).decode().rstrip("=")


class Command(BaseCommand):
    help = "Create demo data for every SokoPay app (dev/demo databases only)."

    def add_arguments(self, parser):
        parser.add_argument("--out", default="", help="also write the demo logins to this Markdown file")

    def handle(self, *args, out="", **opts):
        if not (settings.DEBUG or getattr(settings, "ALLOW_MOCK_INTEGRATIONS", False)):
            raise CommandError("seed_demo only runs on development / demo databases.")
        if User.objects.filter(phone="+233200000001").exists():
            raise CommandError("Demo data is already loaded. Start from a fresh database to reseed.")

        global DEMO_PASSWORD, DEMO_PIN
        if not settings.DEBUG:                       # internet-facing: never the published values
            DEMO_PASSWORD = "Soko-" + secrets.token_urlsafe(12)
            while True:
                DEMO_PIN = f"{secrets.randbelow(10**6):06d}"
                if len(set(DEMO_PIN)) >= 4 and DEMO_PIN not in "0123456789876543210":
                    break
        from apps.licensing.gate import _enabled_set
        settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"          # every feature on for the walkthrough
        settings.RAIL_PROVIDER = "mock"
        settings.CROSS_BORDER_PROVIDER = "mock"
        settings.LIFESTYLE_PARTNERS = {"ticketing": "mock_ticketing", "food": "mock_food"}
        _enabled_set.cache_clear()

        call_command("seed_billers", verbosity=0)
        call_command("seed_marketplace", dev=True, verbosity=0)

        self.logins = []
        self._staff()
        self._agents()
        self._customers()
        self._merchants()
        self._activity()
        self._back_office()
        self._report(out)

    # ------------------------------------------------------------------------------------------
    def _staff_user(self, phone, name, groups, superuser=False):
        from django_otp.plugins.otp_totp.models import TOTPDevice
        u = User.objects.create_user(phone=phone, full_name=name, password=DEMO_PASSWORD, user_type="staff",
                                     is_staff=True)
        if superuser:
            u.is_superuser = True
            u.save()
        for g in groups:
            u.groups.add(Group.objects.get(name=g))
        hexkey, b32 = _totp_key(phone)
        TOTPDevice.objects.create(user=u, name="Demo authenticator", key=hexkey, confirmed=True)
        self.logins.append(("Staff portal", name, phone, DEMO_PASSWORD, ", ".join(groups) or "all (superuser)", b32))
        return u

    def _staff(self):
        self.admin = self._staff_user("+233200000001", "Abena Admin", [], superuser=True)
        self.ops = self._staff_user("+233200000002", "Kwame Operations", ["operations"])
        self.ops2 = self._staff_user("+233200000003", "Efua Operations", ["operations"])
        self.comp = self._staff_user("+233200000004", "Kojo Compliance", ["compliance"])
        self.comp2 = self._staff_user("+233200000005", "Akua Compliance", ["compliance"])
        self.fin = self._staff_user("+233200000006", "Yaa Finance", ["finance"])
        self.fin2 = self._staff_user("+233200000007", "Nii Finance", ["finance"])
        self.sup = self._staff_user("+233200000008", "Selorm Support", ["support"])

    def _app_user(self, phone, name, label, role):
        u = User.objects.create_user(phone=phone, full_name=name)
        u.set_pin(DEMO_PIN)
        u.save()
        self.logins.append((label, name, phone, f"PIN {DEMO_PIN}", role, ""))
        return u

    def _agents(self):
        from apps.agents import services as agents
        from apps.agents.models import Agent
        self.agent_user = self._app_user("+233240000101", "Esi Mensah", "Agent app", "Active agent: Esi's Kiosk, Madina")
        self.agent = agents.register_agent(user=self.agent_user, display_name="Esi's Kiosk", location="Madina Market")
        agents.activate_agent(self.agent)
        # First float: recorded by one operations user, approved by another (maker-checker).
        t = agents.request_float_topup(agent=self.agent, amount_minor=20_000_00, payment_reference="GCB-DEMO-0001",
                                       requested_by=self.ops)
        agents.decide_float_topup(t, approver=self.ops2, approve=True, note="Seen in GCB statement")
        # A second purchase waiting for approval (for the Float approvals page).
        agents.request_float_topup(agent=self.agent, amount_minor=5_000_00, payment_reference="MTN-DEMO-0002",
                                   requested_by=self.ops)
        pending = self._app_user("+233240000102", "Yaw Boateng", "Agent app", "Pending agent (activate in back office)")
        Agent.objects.create(user=pending, display_name="Yaw's Shop", location="Kumasi Kejetia")

    def _customers(self):
        from apps.agents import services as agents
        from apps.kyc import services as kyc
        self.kofi = self._app_user("+233244000201", "Kofi Asante", "Customer app", "Tier 1 (Ghana Card verified)")
        self.ama = self._app_user("+233244000202", "Ama Serwaa", "Customer app", "Tier 2 (Ghana Card + selfie)")
        self.yaw = self._app_user("+233244000203", "Yaw Darko", "Customer app", "Tier 0 (phone only)")
        kyc.upgrade_to_verified(self.kofi, "GHA-712345678-1")
        kyc.upgrade_to_verified(self.ama, "GHA-723456789-2")
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 2048           # the mock identity partner checks format and size only
        kyc.upgrade_to_enhanced(self.ama, jpeg)
        for u, amount in ((self.kofi, 3_000_00), (self.ama, 5_000_00), (self.yaw, 400_00)):
            agents.cash_in(agent=self.agent, customer_phone=u.phone, amount_minor=amount)

    def _merchants(self):
        from apps.merchants import onboarding
        from apps.merchants.models import MerchantMember, SettlementAccount
        self.owner = User.objects.create_user(phone="+233200000301", full_name="Grace Owusu", password=DEMO_PASSWORD,
                                              user_type="merchant")
        self.owner.set_pin(DEMO_PIN)
        self.owner.save()
        from django_otp.plugins.otp_totp.models import TOTPDevice
        hexkey, b32 = _totp_key(self.owner.phone)
        TOTPDevice.objects.create(user=self.owner, name="Demo authenticator", key=hexkey, confirmed=True)
        self.logins.append(("Merchant portal + Business app", "Grace Owusu", self.owner.phone,
                            f"{DEMO_PASSWORD} (portal) / PIN {DEMO_PIN} (app)", "Owner of Grace Mountain Stores", b32))
        self.shop = onboarding.create_merchant(owner=self.owner, legal_name="Grace Mountain Stores Ltd",
                                               trading_name="Grace Mountain Stores", business_type="registered",
                                               category="Groceries", tin="C0001234567")
        for step in (onboarding.submit_for_review, onboarding.begin_review):
            step(self.shop)
        onboarding.approve(self.shop)
        self.shop.short_code = "GRACEMTN"           # fixed so the guide can name it (Crockford letters only)
        self.shop.save(update_fields=["short_code", "updated_at"])
        self.cashier = self._app_user("+233200000302", "Kwesi Cashier", "Business app", "Cashier at Grace Mountain Stores")
        MerchantMember.objects.create(merchant=self.shop, user=self.cashier, role="cashier")
        self.settle_acct = SettlementAccount.objects.create(
            merchant=self.shop, kind="momo", provider="mtn", account_no="+233244000301",
            account_name="Grace Owusu", is_default=True, name_check_status=SettlementAccount.NameCheck.MATCHED)
        # A second business waiting for review (Merchants page in the back office).
        other = User.objects.create_user(phone="+233200000303", full_name="Kofi Electronics Owner",
                                         password=DEMO_PASSWORD, user_type="merchant")
        pending = onboarding.create_merchant(owner=other, legal_name="Kofi Electronics Enterprise",
                                             trading_name="Kofi Electronics", business_type="registered")
        onboarding.submit_for_review(pending)

    def _activity(self):
        from apps.agents import services as agents
        from apps.marketplace import services as mkt
        from apps.marketplace.models import FinancialProduct, FinancialProvider, ProductApplication
        from apps.merchants import checkout, disputes, hosted, qr, refunds, settlement
        from apps.payments import services as payments
        from apps.payments.models import Biller, Payment
        from apps.pricing import services as pricing
        from apps.rails.mock import MockRail
        from apps.rails.types import Network, RailStatus
        from apps.wallet import cross_border as xb
        from apps.wallet import lifestyle, merchant_pay, saved
        from apps.wallet import services as wallet

        # Person to person, and saved recipients.
        wallet.send_p2p(sender=self.kofi, recipient_phone=self.ama.phone, amount_minor=150_00)
        wallet.send_p2p(sender=self.ama, recipient_phone=self.yaw.phone, amount_minor=60_00)
        saved.save(owner=self.kofi, kind="sokopay", value=self.ama.phone)

        # Bills and airtime from the wallet.
        ecg = Biller.objects.get(code="ECG_PREPAID")
        payments.initiate_bill_payment(user=self.kofi, biller=ecg, account_ref="01234567890", amount_minor=100_00,
                                       source=Payment.Source.WALLET)
        payments.initiate_airtime(user=self.ama, biller=Biller.objects.get(code="MTN_AIRTIME"), phone=self.ama.phone,
                                  amount_minor=20_00, source=Payment.Source.WALLET)

        # Paying the shop: wallet scan-to-pay, a dynamic QR, and a MoMo payment.
        code = self.shop.short_code
        self.p1 = merchant_pay.pay(user=self.kofi, code=code, amount_minor=85_50)
        p2 = merchant_pay.pay(user=self.ama, code=code, amount_minor=240_00)
        req = qr.create_payment_request(merchant=self.shop, created_by=self.cashier, amount_minor=35_00,
                                        description="Order 104")
        merchant_pay.pay(user=self.yaw, code=qr.request_payload(req), amount_minor=None)
        momo = checkout.initiate_merchant_charge(merchant=self.shop, amount_minor=120_00, network=Network.MTN,
                                                 payer=self.kofi.phone)
        MockRail.drive(momo.rail_ref, RailStatus.SUCCEEDED)
        checkout.confirm_merchant_collection(momo)
        saved.save(owner=self.kofi, kind="merchant", value=code)
        hosted.create_link(merchant=self.shop, title="Weekly groceries box", amount_minor=150_00, created_by=self.owner)
        hosted.create_link(merchant=self.shop, title="Pay any amount", amount_minor=None, created_by=self.owner)

        # A partial refund, and two disputes (one waiting for the shop, one answered for staff).
        refunds.create_refund(payment=p2, amount_minor=40_00, reason="One item out of stock", requested_by=self.owner)
        disputes.open_dispute(customer=self.kofi, reference=self.p1.reference, reason="not_as_described",
                              description="The rice bag was torn when I opened it at home.")
        p3 = merchant_pay.pay(user=self.ama, code=code, amount_minor=60_00)
        d2 = disputes.open_dispute(customer=self.ama, reference=p3.reference, reason="duplicate",
                                   description="I think I was charged twice for the same order.")
        disputes.merchant_respond(d2, by=self.owner, accept=False,
                                  response="Only one charge on our side; the other was a different day.")

        # Settlement to an account changed today: needs a SokoPay finance approval.
        settlement.request_settlement(merchant=self.shop, destination=self.settle_acct, requested_by=self.owner,
                                      amount_minor=200_00)

        # Cash-out at the agent (window, request, PIN approval), plus a cash-out fee and commissions.
        r = pricing.propose(kind="commission", product="cash_in", percent_bp=20, note="Launch agent commission",
                            proposed_by=self.fin)
        pricing.decide(r, approver=self.fin2, approve=True)
        agents.open_cash_out_window(self.kofi)
        co = agents.request_cash_out(agent=self.agent, customer_phone=self.kofi.phone, amount_minor=200_00)
        agents.approve_cash_out(customer=self.kofi, request_id=co.id, pin=DEMO_PIN)
        pricing.propose(kind="fee", product="cash_out", percent_bp=100, min_minor=50,
                        note="Cash-out tariff 1%, minimum GH₵0.50", proposed_by=self.fin)

        # Cross-border (one on its way, one delivered).
        recipient = {"country": "NG", "method": "mobile_money", "account": "+2348031234567", "institution": "mtn_ng",
                     "name": "Chidi Okafor"}
        q = xb.get_quote(user=self.ama, amount_minor=300_00, **recipient)
        xb.send(user=self.ama, quote_id=q["quote_id"], purpose="family_support", pin=DEMO_PIN)
        q2 = xb.get_quote(user=self.ama, amount_minor=100_00, **{**recipient, "country": "KE", "account": "+254712345678",
                                                                  "institution": "mpesa", "name": "Wanjiru Kamau"})
        t2 = xb.send(user=self.ama, quote_id=q2["quote_id"], purpose="gift", pin=DEMO_PIN)
        from apps.connectors.cross_border import MockCrossBorderConnector
        MockCrossBorderConnector.drive(t2.provider_ref, RailStatus.SUCCEEDED)
        xb.resolve_pending()

        # Tickets and food.
        lifestyle.order(user=self.kofi, category="ticketing", offering_code="KOTOKO-HEARTS", quantity=2, pin=DEMO_PIN)
        lifestyle.order(user=self.ama, category="food", offering_code="JOLLOF-CHK", quantity=1, pin=DEMO_PIN)

        # Savings: an approved account with a payment in and a withdrawal waiting for the provider.
        prov = FinancialProvider.objects.create(key="demo-savings", name="Akwaaba Savings & Loans", category="savings",
                                                regulator="Bank of Ghana", connector="mock", is_active=True)
        prod = FinancialProduct.objects.create(provider=prov, code="AKWAABA-SAVE", name="Akwaaba Easy Save",
                                               category="savings", min_minor=10_00,
                                               summary="Flexible savings, 12% p.a. [demo product]")
        app = ProductApplication.objects.create(reference="MP-DEMOSAVE1", user=self.kofi, product=prod,
                                                status="approved", consent_given_at=timezone.now())
        mkt.contribute(user=self.kofi, application=app, amount_minor=250_00, pin=DEMO_PIN)
        mkt.request_withdrawal(user=self.kofi, application=app, amount_minor=50_00, pin=DEMO_PIN)
        insurance = FinancialProduct.objects.filter(category="insurance", is_active=True).first()
        if insurance:
            mkt.apply(user=self.ama, product=insurance, amount_minor=None, consent=True)

    def _back_office(self):
        from apps.compliance import alerts, reports
        from apps.compliance.models import WatchlistEntry
        from apps.compliance.screening import normalise
        from apps.reconciliation.services import reconcile
        from apps.safeguarding import services as safeguarding
        from apps.safeguarding.models import TrustAccount

        # An AML case with notes and a draft STR waiting for a second officer.
        a = alerts.raise_alert(rule="PASS_THROUGH", title="Money in and straight out", severity="high",
                               subject_kind="customer", subject_id=str(self.yaw.id), subject_label="Yaw Darko",
                               user=self.yaw, evidence={"inflow": "GH₵ 400.00", "outflow": "GH₵ 395.00",
                                                        "window": "35 minutes"})
        alerts.assign(a, actor=self.comp)
        alerts.add_note(a, author=self.comp, text="Called the customer; says it was school fees for a relative.")
        reports.draft_str(alerts=[a], prepared_by=self.comp,
                          narrative="Customer received GH₵400 from an agent cash-in and forwarded it within 35 minutes "
                                    "to an unrelated wallet. Pattern repeated twice this week.")
        alerts.raise_alert(rule="LARGE_TRANSACTION", title="Large cash-in", severity="medium", subject_kind="customer",
                           subject_id=str(self.ama.id), subject_label="Ama Serwaa", user=self.ama,
                           evidence={"amount": "GH₵ 5,000.00"})
        WatchlistEntry.objects.create(source="custom", kind="sanctions", name="Demo Sanctioned Person",
                                      normalised=[normalise("Demo Sanctioned Person")])

        # Reconciliation: yesterday's run with one break of each kind.
        yesterday = timezone.localdate() - dt.timedelta(days=1)
        reconcile("mock", yesterday, ours={"MOCK-A1": 120_00, "MOCK-A2": 50_00, "MOCK-A3": 75_00},
                  theirs={"MOCK-A1": 120_00, "MOCK-A2": 49_00, "MOCK-B9": 30_00})

        # Safeguarding: trust account balance recorded, then the daily check.
        acct = TrustAccount.objects.create(bank_name="GCB Bank", account_name="SokoPay Customer Trust",
                                           account_last4="4821")
        safeguarding.record_trust_balance(account=acct, balance_minor=60_000_00, as_of=timezone.now(),
                                          actor=self.fin)
        safeguarding.run_check()

    # ------------------------------------------------------------------------------------------
    def _report(self, out):
        lines = ["# SokoPay demo logins (DEMO DATA ONLY)", "",
                 "| App | Name | Phone | Password / PIN | Role | 2FA secret (authenticator app) |",
                 "|---|---|---|---|---|---|"]
        for app, name, phone, secret, role, b32 in self.logins:
            lines.append(f"| {app} | {name} | {phone} | {secret} | {role} | {b32 or '-'} |")
        lines += ["", f"Merchant code (QR) for Grace Mountain Stores: **{self.shop.short_code}**",
                  f"Wallet IDs: Kofi {self._wid(self.kofi)}, Ama {self._wid(self.ama)}, Yaw {self._wid(self.yaw)}"]
        text = "\n".join(lines) + "\n"
        if out:
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
        self.stdout.write(text)
        self.stdout.write(self.style.SUCCESS("Demo data ready."))

    @staticmethod
    def _wid(user):
        from apps.wallet.accounts_lookup import format_wallet_number, wallet_number_for
        return format_wallet_number(wallet_number_for(user))
