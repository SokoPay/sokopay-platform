"""
Marketplace services. LICENCE: listing and referring partner financial products is a
PSP **Enhanced** activity (FINANCIAL_MARKETPLACE). Paying premiums from, or
disbursing loans into, the SokoPay wallet is DEMI (EMBEDDED_FINANCIAL_PRODUCTS) and is
not built yet — applications here go to the partner, who pays out directly.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.connectors import registry
from apps.connectors.placeholder import ConnectorNotImplemented
from apps.connectors.types import ApplicationRequest
from apps.kyc import limits as kyc_limits
from apps.ledger import accounts
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.capabilities import Capability
from apps.licensing.gate import require_capability
from apps.notifications.services import ghs, notify
from apps.payments.reference import new_reference

from .models import Category, FinancialProduct, ProductApplication, ProductTransaction


class MarketplaceError(Exception):
    pass


def list_products(category: str | None = None):
    require_capability(Capability.FINANCIAL_MARKETPLACE)
    qs = FinancialProduct.objects.filter(is_active=True, provider__is_active=True) \
        .select_related("provider")
    if category:
        qs = qs.filter(category=category)
    return qs.order_by("category", "name")


def apply(*, user, product: FinancialProduct, amount_minor: int | None,
          consent: bool) -> ProductApplication:
    require_capability(Capability.FINANCIAL_MARKETPLACE)
    if not consent:
        raise MarketplaceError(
            "We need your consent to share your details with the provider."
        )
    if not (product.is_active and product.provider.is_active):
        raise MarketplaceError("This product is not available.")
    if amount_minor is not None:
        if product.min_minor and amount_minor < product.min_minor:
            raise MarketplaceError("Amount is below this product's minimum.")
        if product.max_minor and amount_minor > product.max_minor:
            raise MarketplaceError("Amount is above this product's maximum.")

    connector = registry.get_connector("financial", product.provider.connector)
    if not connector.available:
        raise MarketplaceError("This provider is not available yet.")

    application = ProductApplication.objects.create(
        reference=new_reference(),
        user=user,
        product=product,
        amount_minor=amount_minor,
        status=ProductApplication.Status.SUBMITTED,
        consent_given_at=timezone.now(),
    )

    try:
        result = connector.submit_application(ApplicationRequest(
            product_code=product.code,
            applicant_phone=user.phone,
            applicant_name=user.full_name,
            amount_minor=amount_minor,
            reference=application.reference,
        ))
    except ConnectorNotImplemented as exc:
        application.status = ProductApplication.Status.FAILED
        application.message = "Provider integration not live yet."
        application.save(update_fields=["status", "message", "updated_at"])
        raise MarketplaceError("This provider is not available yet.") from exc

    application.status = result.status
    application.partner_ref = result.partner_ref
    application.message = result.message[:255]
    application.save(update_fields=["status", "partner_ref", "message", "updated_at"])
    return application


def set_status(application: ProductApplication, status: str, message: str = "") -> ProductApplication:
    """Record the partner's decision (from their callback, or ops on their instruction)."""
    application.status = status
    if message:
        application.message = message[:255]
    application.save(update_fields=["status", "message", "updated_at"])
    return application


# ---------------------------------------------------------------------------
# Embedded products — money between the wallet and the partner (DEMI)
# ---------------------------------------------------------------------------
def _require_approved(application: ProductApplication, category: str) -> None:
    if application.product.category != category:
        raise MarketplaceError("This action doesn't apply to this product.")
    if application.status != ProductApplication.Status.APPROVED:
        raise MarketplaceError("The provider hasn't approved this application yet.")


@transaction.atomic
@kyc_limits.debits_serialized("user")
def pay_premium(*, user, application: ProductApplication, amount_minor: int) -> ProductTransaction:
    """Pay an insurance premium from the customer's wallet to the insurer."""
    require_capability(Capability.EMBEDDED_FINANCIAL_PRODUCTS)
    if application.user_id != user.id:
        raise MarketplaceError("Application not found.")
    _require_approved(application, Category.INSURANCE)
    if amount_minor <= 0:
        raise MarketplaceError("Amount must be positive.")

    wallet = accounts.customer_wallet(str(user.id))
    if natural_balance_of(wallet) < amount_minor:
        raise MarketplaceError("Insufficient wallet balance.")
    kyc_limits.check_debit(user, amount_minor)

    partner = application.product.provider.key
    post_entry(
        f"Premium {application.reference} to {partner}",
        [debit(wallet, amount_minor),
         credit(accounts.financial_partner_payable(partner), amount_minor)],
        reference=("product_application", str(application.id)),
    )
    return ProductTransaction.objects.create(
        application=application, kind=ProductTransaction.Kind.PREMIUM, amount_minor=amount_minor)


@transaction.atomic
def disburse_loan(*, application: ProductApplication, amount_minor: int,
                  partner_ref: str) -> ProductTransaction:
    """
    The lender has approved and funded a loan: credit it to the customer's wallet.
    Called from the lender's (signed) callback. Idempotent on the lender's reference.
    """
    require_capability(Capability.EMBEDDED_FINANCIAL_PRODUCTS)
    existing = ProductTransaction.objects.filter(partner_ref=partner_ref).first()
    if existing:
        return existing
    _require_approved(application, Category.LENDING)
    if amount_minor <= 0:
        raise MarketplaceError("Amount must be positive.")
    kyc_limits.check_credit(application.user, amount_minor)

    partner = application.product.provider.key
    post_entry(
        f"Loan {application.reference} from {partner}",
        [debit(accounts.partner_clearing(partner), amount_minor),
         credit(accounts.customer_wallet(str(application.user_id)), amount_minor)],
        idempotency_key=f"loan:{partner_ref}",
        reference=("product_application", str(application.id)),
    )
    notify(application.user, kind="wallet", title="Loan received",
           body=f"{ghs(amount_minor)} from {application.product.provider.name} is in your wallet.",
           data={"type": "marketplace", "reference": application.reference}, app="customer")
    return ProductTransaction.objects.create(
        application=application, kind=ProductTransaction.Kind.DISBURSEMENT,
        amount_minor=amount_minor, partner_ref=partner_ref)


# ---------------------------------------------------------------------------
# Savings / investment / pension: paying in and taking out (DEMI)
# ---------------------------------------------------------------------------
SAVINGS_LIKE = (Category.SAVINGS, Category.INVESTMENT, Category.PENSION)


def _require_savings(user, application: ProductApplication) -> None:
    require_capability(Capability.EMBEDDED_FINANCIAL_PRODUCTS)
    if application.user_id != user.id:
        raise MarketplaceError("Application not found.")
    if application.product.category not in SAVINGS_LIKE:
        raise MarketplaceError("This action doesn't apply to this product.")
    if application.status != ProductApplication.Status.APPROVED:
        raise MarketplaceError("The provider hasn't opened this account yet.")


def _check_pin(user, pin: str) -> None:
    from apps.accounts import services as auth_services
    try:
        auth_services.confirm_pin(user, pin)
    except auth_services.AuthError as exc:
        raise MarketplaceError(str(exc)) from exc


@kyc_limits.debits_serialized("user")
def contribute(*, user, application: ProductApplication, amount_minor: int, pin: str) -> ProductTransaction:
    """Pay into a savings / investment / pension account from the wallet.
        Dr customer_wallet   Cr financial_partner_payable:<partner>   (swept to the partner by ops)"""
    _check_pin(user, pin)
    _require_savings(user, application)
    product = application.product
    if amount_minor <= 0 or (product.min_minor and amount_minor < product.min_minor):
        raise MarketplaceError(f"The minimum is {ghs(product.min_minor or 1)}.")
    if product.max_minor and amount_minor > product.max_minor:
        raise MarketplaceError(f"The maximum is {ghs(product.max_minor)}.")
    with transaction.atomic():
        wallet = accounts.customer_wallet(str(user.id))
        if natural_balance_of(wallet) < amount_minor:
            raise MarketplaceError("Insufficient wallet balance.")
        kyc_limits.check_debit(user, amount_minor)
        partner = product.provider.key
        post_entry(f"{product.get_category_display()} {application.reference} to {partner}",
                   [debit(wallet, amount_minor), credit(accounts.financial_partner_payable(partner), amount_minor)],
                   reference=("product_application", str(application.id)))
        txn = ProductTransaction.objects.create(application=application, kind=ProductTransaction.Kind.CONTRIBUTION,
                                                amount_minor=amount_minor)
    notify(user, kind="wallet", title="Payment made",
           body=f"{ghs(amount_minor)} paid into {product.name} ({product.provider.name}).",
           data={"type": "marketplace", "reference": application.reference}, app="customer")
    return txn


def request_withdrawal(*, user, application: ProductApplication, amount_minor: int, pin: str) -> ProductTransaction:
    """Ask the provider to pay money back to the wallet. Nothing moves until they pay
    (confirm_withdrawal). Pension withdrawals follow NPRA rules and go through the trustee."""
    _check_pin(user, pin)
    _require_savings(user, application)
    if application.product.category == Category.PENSION:
        raise MarketplaceError("Pension withdrawals follow NPRA rules. Contact your pension trustee.")
    if amount_minor <= 0:
        raise MarketplaceError("Amount must be positive.")
    if application.transactions.filter(kind=ProductTransaction.Kind.WITHDRAWAL,
                                       status=ProductTransaction.Status.REQUESTED).exists():
        raise MarketplaceError("You already have a withdrawal waiting for the provider.")
    return ProductTransaction.objects.create(application=application, kind=ProductTransaction.Kind.WITHDRAWAL,
                                             amount_minor=amount_minor, status=ProductTransaction.Status.REQUESTED)


def confirm_withdrawal(*, application: ProductApplication, amount_minor: int, partner_ref: str) -> ProductTransaction:
    """The provider has paid a withdrawal: credit the wallet. Idempotent on their reference.
        Dr partner_clearing:<partner>   Cr customer_wallet
    Called from the provider's signed callback once connected, or by ops on a confirmed payment."""
    require_capability(Capability.EMBEDDED_FINANCIAL_PRODUCTS)
    partner_ref = (partner_ref or "").strip()
    if not partner_ref:
        raise MarketplaceError("The provider's payment reference is required.")
    with transaction.atomic():
        existing = ProductTransaction.objects.filter(partner_ref=partner_ref).first()
        if existing:
            return existing
        if application.product.category not in SAVINGS_LIKE:
            raise MarketplaceError("This action doesn't apply to this product.")
        pending = (application.transactions.select_for_update()
                   .filter(kind=ProductTransaction.Kind.WITHDRAWAL, status=ProductTransaction.Status.REQUESTED)
                   .first())
        if pending is None:
            raise MarketplaceError("No withdrawal was requested on this account.")
        if amount_minor <= 0 or amount_minor > pending.amount_minor:
            raise MarketplaceError("The paid amount doesn't match the request.")
        kyc_limits.check_credit(application.user, amount_minor)
        partner = application.product.provider.key
        clearing = accounts.partner_clearing(partner)
        post_entry(f"Withdrawal {application.reference} from {partner}",
                   [debit(clearing, amount_minor),
                    credit(accounts.customer_wallet(str(application.user_id)), amount_minor)],
                   idempotency_key=f"withdrawal:{partner}:{partner_ref}",
                   reference=("product_application", str(application.id)), allow_negative={clearing.code})
        pending.status, pending.partner_ref, pending.amount_minor = (
            ProductTransaction.Status.COMPLETED, partner_ref, amount_minor)
        pending.save(update_fields=["status", "partner_ref", "amount_minor", "updated_at"])
    notify(application.user, kind="wallet", title="Withdrawal received",
           body=f"{ghs(amount_minor)} from {application.product.provider.name} is in your wallet.",
           data={"type": "marketplace", "reference": application.reference}, app="customer")
    return pending


def position(application: ProductApplication) -> dict:
    """What SokoPay has seen move (the provider's statement is the authority on value/returns)."""
    from django.db.models import Sum
    done = application.transactions.filter(status=ProductTransaction.Status.COMPLETED)
    paid_in = int(done.filter(kind="contribution").aggregate(s=Sum("amount_minor"))["s"] or 0)
    paid_out = int(done.filter(kind="withdrawal").aggregate(s=Sum("amount_minor"))["s"] or 0)
    waiting = application.transactions.filter(kind="withdrawal", status="requested").first()
    return {"paid_in_display": ghs(paid_in), "paid_out_display": ghs(paid_out),
            "pending_withdrawal_display": ghs(waiting.amount_minor) if waiting else None}
