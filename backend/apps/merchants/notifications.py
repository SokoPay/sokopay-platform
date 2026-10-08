"""
Notifications to a merchant's team, delivered to the SokoPay Business app only.

Deep-link contract (the `data` the app reads when a notification is tapped — see
mobile/merchant/lib/core/deep_links.dart; all values are strings):

  {"type": "merchant_payment", "reference": "SP-XXXXXXXXXX"}  → payment detail
  {"type": "settlement", "settlement": "<uuid>"}              → settlements
  {"type": "settlement_accounts"}                              → settlements

Who hears what:
  * payment received  → every team member (the cashier at the counter needs it most)
  * settlement events → roles that can see money (Owner/Admin/Finance)
  * account changes   → the Owner (security)
"""

from __future__ import annotations

from apps.notifications.services import ghs, notify

from .models import Merchant, MerchantMember

MONEY_ROLES = ("owner", "admin", "finance")
APP = "merchant"


def _members(merchant: Merchant, roles: tuple | None = None):
    qs = MerchantMember.objects.filter(merchant=merchant).select_related("user")
    if roles:
        qs = qs.filter(role__in=roles)
    seen = set()
    for m in qs:
        if m.user_id not in seen and m.user.is_active:
            seen.add(m.user_id)
            yield m.user


def payment_received(payment, *, note: str = "") -> None:
    """Tell the team a customer has paid. Called inside the payment's transaction;
    pushes go out only after it commits (see notifications.services)."""
    merchant = payment.merchant
    if merchant is None:
        return
    body = f"{ghs(payment.amount_minor)} received"
    body += f" for {note}." if note else "."
    for user in _members(merchant):
        notify(user, kind="payment", title="Payment received", body=body, app=APP,
               data={"type": "merchant_payment", "reference": payment.reference})


def settlement_outcome(settlement) -> None:
    paid = settlement.status == "paid"
    title = "Settlement paid" if paid else "Settlement didn't go through"
    body = (f"{ghs(settlement.amount_minor)} has been paid to your settlement account." if paid else
            f"Your {ghs(settlement.amount_minor)} settlement was refused by the receiving account. "
            "The money is back in your SokoPay balance.")
    for user in _members(settlement.merchant, MONEY_ROLES):
        notify(user, kind="payment", title=title, body=body, app=APP,
               data={"type": "settlement", "settlement": str(settlement.id)})


def settlement_account_added(merchant: Merchant, account, *, added_by) -> None:
    if merchant.owner_id == added_by.id:
        return
    notify(merchant.owner, kind="security", title="New settlement account added", app=APP,
           body=f"{added_by.full_name or added_by.phone} added {account.get_kind_display()} "
                f"{account.account_no}. If this wasn't expected, contact SokoPay support now.",
           data={"type": "settlement_accounts"}, sms=True)


def refund_outcome(refund) -> None:
    ok = refund.status == "succeeded"
    title = "Refund sent" if ok else "Refund didn't go through"
    body = (f"{ghs(refund.amount_minor)} refunded on {refund.payment.reference}." if ok else
            f"The {ghs(refund.amount_minor)} refund on {refund.payment.reference} was refused by the "
            "customer's network. The money is back in your SokoPay balance.")
    for user in _members(refund.merchant, MONEY_ROLES):
        notify(user, kind="payment", title=title, body=body, app=APP,
               data={"type": "merchant_payment", "reference": refund.payment.reference})
