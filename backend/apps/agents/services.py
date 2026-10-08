"""
Agent services: registration, float top-up, and cash-in / cash-out.

LICENCE: these are DEMI activities. Each money operation is gated:
  * float top-up and cash-in credit customer e-money → HOLD_CUSTOMER_FUNDS
  * cash-in / cash-out are agent operations        → CASH_IN_OUT
  * activating an agent runs the agent network      → AGENT_NETWORK
So the whole agent app is built now and simply refuses to move money until the DEMI
licence is active — the same build-all/roll-out-per-licence model used everywhere.

Money model (all internal ledger moves; no rail needed for the cash legs):
  top-up:   customer/agent pays SokoPay  →  Dr partner_clearing   Cr agent_float
  cash-in:  customer gives agent cash    →  Dr agent_float        Cr customer_wallet
  cash-out: customer takes cash from agent → Dr customer_wallet   Cr agent_float
            Three steps, so an agent can never debit a wallet alone:
              1. customer presses "Cash out" in their app (open_cash_out_window)
              2. agent raises a request for the amount     (request_cash_out)
              3. customer approves with their PIN          (approve_cash_out)
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.kyc import limits as kyc_limits
from apps.kyc.exceptions import KycError
from apps.ledger import accounts
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.capabilities import Capability
from apps.licensing.gate import require_capability
from apps.notifications.services import ghs, notify
from apps.common.audit import audited

from .exceptions import AgentError
from .models import Agent, AgentTxn, CashOutRequest, CashOutWindow, FloatTopUp

User = get_user_model()


# --- registration -----------------------------------------------------------
def register_agent(*, user, display_name: str, location: str = "") -> Agent:
    """Create a pending agent profile for a user (licence-independent onboarding)."""
    if hasattr(user, "agent_profile"):
        raise AgentError("This user is already an agent.")
    user.user_type = "agent"
    user.save(update_fields=["user_type", "updated_at"])
    return Agent.objects.create(user=user, display_name=display_name, location=location)


def activate_agent(agent: Agent) -> Agent:
    require_capability(Capability.AGENT_NETWORK)
    agent.status = Agent.Status.ACTIVE
    agent.save(update_fields=["status", "updated_at"])
    _tell_agent(agent, title="You're now a SokoPay agent",
                body="Your agent account is active. Top up your float to start serving customers.",
                data={"type": "agent_status"})
    return agent


# --- agent notifications (Agent app only) -------------------------------------
# Deep-link contract (mobile/agent/lib/core/deep_links.dart), string values:
#   {"type": "float"}        → home (float balance)
#   {"type": "agent_status"} → home
def _tell_agent(agent: Agent, *, title: str, body: str, data: dict) -> None:
    notify(agent.user, kind="wallet", title=title, body=body, data=data, app="agent")


def low_float_threshold() -> int:
    return int(getattr(settings, "AGENT_LOW_FLOAT_MINOR", 200_00))   # GH₵200 default


# --- balances ---------------------------------------------------------------
def float_balance(agent: Agent, currency: str = "GHS") -> int:
    return natural_balance_of(accounts.agent_float(str(agent.id), currency))


def wallet_balance(user, currency: str = "GHS") -> int:
    return natural_balance_of(accounts.customer_wallet(str(user.id), currency))


# --- money operations -------------------------------------------------------
@transaction.atomic
def topup_float(*, agent: Agent, amount_minor: int, currency: str = "GHS") -> AgentTxn:
    """Increase an agent's float (they have paid SokoPay for it)."""
    require_capability(Capability.HOLD_CUSTOMER_FUNDS)
    _require_active(agent)
    _require_positive(amount_minor)

    # The agent paid for this float through the configured rail (was hard-coded to the
    # mock rail's clearing account — wrong books in production).
    clearing = accounts.partner_clearing(getattr(settings, "RAIL_PROVIDER", "mock"), currency)
    agent_acc = accounts.agent_float(str(agent.id), currency)
    post_entry(
        f"Agent {agent.id} float top-up",
        [debit(clearing, amount_minor), credit(agent_acc, amount_minor)],
        reference=("agent_topup", str(agent.id)),
        allow_negative={clearing.code},
    )
    _tell_agent(agent, title="Float topped up",
                body=f"{ghs(amount_minor)} added. Your float is now {ghs(float_balance(agent, currency))}.",
                data={"type": "float"})
    return AgentTxn.objects.create(
        agent=agent, kind=AgentTxn.Kind.TOPUP, amount_minor=amount_minor, currency=currency
    )


@transaction.atomic
def cash_in(*, agent: Agent, customer_phone: str, amount_minor: int,
            currency: str = "GHS") -> AgentTxn:
    """
    Customer hands the agent cash; the agent credits the customer's wallet from float.
    The agent's float decreases and may not go negative (enforced by the ledger).
    """
    require_capability(Capability.CASH_IN_OUT)
    require_capability(Capability.HOLD_CUSTOMER_FUNDS)
    _require_active(agent)
    _require_positive(amount_minor)

    customer = _get_customer(customer_phone)
    agent_acc = accounts.agent_float(str(agent.id), currency)
    wallet = accounts.customer_wallet(str(customer.id), currency)

    before = float_balance(agent, currency)
    if before < amount_minor:
        raise AgentError("Not enough float for this cash-in.")
    kyc_limits.check_credit(customer, amount_minor, currency)

    post_entry(
        f"Cash-in {amount_minor} to {customer.phone}",
        [debit(agent_acc, amount_minor), credit(wallet, amount_minor)],
        reference=("cash_in", str(agent.id)),
    )
    notify(customer, kind="wallet", title="Cash deposited",
           body=f"{ghs(amount_minor)} deposited at {agent.display_name} is in your wallet.",
           data={"type": "wallet"}, app="customer")
    # Warn once, when this cash-in takes the float below the threshold (not on every
    # cash-in after that), so the agent can top up before turning customers away.
    after, threshold = before - amount_minor, low_float_threshold()
    if before >= threshold > after:
        _tell_agent(agent, title="Float running low",
                    body=f"Your float is down to {ghs(after)}. Top up soon so you can keep "
                         "serving cash-in customers.",
                    data={"type": "float"})
    txn = AgentTxn.objects.create(
        agent=agent, kind=AgentTxn.Kind.CASH_IN, customer_phone=customer.phone,
        amount_minor=amount_minor, currency=currency,
    )
    from apps.pricing.services import accrue_commission
    accrue_commission(txn=txn, product="cash_in", customer=customer)
    return txn


# --- cash-out: agent requests, customer approves ------------------------------------
CASH_OUT_TTL = timedelta(minutes=5)       # customer must approve within this
CASH_OUT_WINDOW = timedelta(minutes=10)   # after pressing "Cash out", agent may request within this


def open_cash_out_window(customer) -> CashOutWindow:
    """
    Step 0 (customer, standing at the agent): press "Cash out" in the app. Opens a short
    window in which ONE agent may raise a request against this account. Re-pressing
    replaces any open window.
    """
    require_capability(Capability.CASH_IN_OUT)
    now = timezone.now()
    with transaction.atomic():
        CashOutWindow.objects.select_for_update().filter(
            customer=customer, used_at__isnull=True, cancelled_at__isnull=True, expires_at__gt=now,
        ).update(cancelled_at=now)
        return CashOutWindow.objects.create(customer=customer, expires_at=now + CASH_OUT_WINDOW)


def close_cash_out_window(customer) -> None:
    now = timezone.now()
    CashOutWindow.objects.filter(customer=customer, used_at__isnull=True, cancelled_at__isnull=True,
                                 expires_at__gt=now).update(cancelled_at=now)


def open_window(customer) -> CashOutWindow | None:
    now = timezone.now()
    return (CashOutWindow.objects.filter(customer=customer, used_at__isnull=True,
                                         cancelled_at__isnull=True, expires_at__gt=now)
            .order_by("-created_at").first())


def request_cash_out(*, agent: Agent, customer_phone: str, amount_minor: int,
                     currency: str = "GHS") -> CashOutRequest:
    """
    Step 1 (agent): ask to pay a customer cash. Nothing moves. The customer gets a push
    and must approve in their app with their PIN ("Allow CashOut") within 5 minutes.
    The agent is never told the customer's balance - an insufficient balance shows up
    only to the customer, at approval.
    """
    require_capability(Capability.CASH_IN_OUT)
    require_capability(Capability.HOLD_CUSTOMER_FUNDS)
    _require_active(agent)
    _require_positive(amount_minor)
    customer = _get_customer(customer_phone)
    if customer.id == agent.user_id:
        raise AgentError("You can't cash out from your own wallet as the agent.")

    with transaction.atomic():
        # The customer must have pressed "Cash out" in their app first.
        now = timezone.now()
        window = (CashOutWindow.objects.select_for_update()
                  .filter(customer=customer, used_at__isnull=True, cancelled_at__isnull=True,
                          expires_at__gt=now).order_by("-created_at").first())
        if window is None:
            raise AgentError("Ask the customer to press Cash out in their SokoPay app first.")
        # One open request per customer at a time - stops prompt spamming.
        _expire_due(CashOutRequest.objects.select_for_update().filter(customer=customer))
        if CashOutRequest.objects.filter(customer=customer, status=CashOutRequest.Status.PENDING).exists():
            raise AgentError("This customer already has a cash-out waiting for approval.")
        window.used_at = now                      # one window, one request
        window.save(update_fields=["used_at", "updated_at"])
        from apps.pricing.services import fee as price_fee
        fee_minor = price_fee("cash_out", amount_minor)
        req = CashOutRequest.objects.create(
            agent=agent, customer=customer, amount_minor=amount_minor, fee_minor=fee_minor, currency=currency,
            expires_at=timezone.now() + CASH_OUT_TTL,
        )
        fee_text = f" (fee {ghs(fee_minor)})" if fee_minor else ""
        notify(customer, kind="wallet", title="Approve cash-out?",
               body=f"{agent.display_name} wants to pay you {ghs(amount_minor)} cash from your wallet{fee_text}. "
                    "Approve only if you're at the agent now.",
               data={"type": "cashout_request", "request": str(req.id)}, app="customer", sms=True)
    return req


def pending_cash_outs(customer):
    """Open requests the customer can approve, newest first (expired ones are closed)."""
    _expire_due(CashOutRequest.objects.filter(customer=customer, status=CashOutRequest.Status.PENDING))
    return (CashOutRequest.objects.filter(customer=customer, status=CashOutRequest.Status.PENDING)
            .select_related("agent").order_by("-created_at"))


def approve_cash_out(*, customer, request_id, pin: str) -> CashOutRequest:
    """Step 2 (customer): approve with PIN -> wallet debited, agent float credited."""
    from apps.accounts import services as auth_services
    try:
        auth_services.confirm_pin(customer, pin)       # shared lockout with sign-in
    except auth_services.AuthError as exc:
        raise AgentError(str(exc)) from exc

    require_capability(Capability.CASH_IN_OUT)
    require_capability(Capability.HOLD_CUSTOMER_FUNDS)
    with transaction.atomic():
        req = (CashOutRequest.objects.select_for_update(of=("self",)).select_related("agent")
               .filter(pk=request_id, customer=customer).first())
        if req is None:
            raise AgentError("Cash-out request not found.")
        _expire_due([req])
        if req.status != CashOutRequest.Status.PENDING:
            raise AgentError(f"This request is {req.get_status_display().lower()}.")
        agent = req.agent
        failure = ""
        if not agent.is_active:
            failure = "The agent is no longer active."
        elif wallet_balance(customer, req.currency) < req.amount_minor + req.fee_minor:
            failure = "You don't have enough in your wallet for this cash-out and its fee."
        else:
            try:
                kyc_limits.check_debit(customer, req.amount_minor + req.fee_minor)
            except KycError as exc:
                failure = str(exc)
        if failure:
            req.status, req.failure_reason, req.decided_at = CashOutRequest.Status.FAILED, failure, timezone.now()
            req.save(update_fields=["status", "failure_reason", "decided_at", "updated_at"])
            _tell_agent(agent, title="Cash-out couldn't be completed",
                        body=f"The {ghs(req.amount_minor)} cash-out didn't go through. Don't hand over cash.",
                        data={"type": "cashout"})
            return req

        lines = [debit(accounts.customer_wallet(str(customer.id), req.currency), req.amount_minor + req.fee_minor),
                 credit(accounts.agent_float(str(agent.id), req.currency), req.amount_minor)]
        if req.fee_minor:
            lines.append(credit(accounts.fee_revenue(req.currency), req.fee_minor))
        post_entry(
            f"Cash-out {req.amount_minor} (request {req.id})", lines,
            idempotency_key=f"cash-out:{req.id}",
            reference=("cash_out", str(agent.id)),
        )
        req.txn = AgentTxn.objects.create(
            agent=agent, kind=AgentTxn.Kind.CASH_OUT, customer_phone=customer.phone,
            amount_minor=req.amount_minor, currency=req.currency,
        )
        from apps.pricing.services import accrue_commission
        accrue_commission(txn=req.txn, product="cash_out", customer=customer)
        req.status, req.decided_at = CashOutRequest.Status.APPROVED, timezone.now()
        req.save(update_fields=["txn", "status", "decided_at", "updated_at"])
        notify(customer, kind="wallet", title="Cash withdrawn",
               body=f"{ghs(req.amount_minor)} withdrawn at {agent.display_name}.",
               data={"type": "wallet"}, app="customer", sms=True)
        _tell_agent(agent, title="Cash-out approved",
                    body=f"The customer approved. Hand over {ghs(req.amount_minor)}.",
                    data={"type": "cashout"})
    return req


def decline_cash_out(*, customer, request_id) -> CashOutRequest:
    with transaction.atomic():
        req = (CashOutRequest.objects.select_for_update(of=("self",)).select_related("agent")
               .filter(pk=request_id, customer=customer).first())
        if req is None:
            raise AgentError("Cash-out request not found.")
        _expire_due([req])
        if req.status == CashOutRequest.Status.PENDING:
            req.status, req.decided_at = CashOutRequest.Status.DECLINED, timezone.now()
            req.save(update_fields=["status", "decided_at", "updated_at"])
            _tell_agent(req.agent, title="Cash-out declined",
                        body=f"The customer declined the {ghs(req.amount_minor)} cash-out. Don't hand over cash.",
                        data={"type": "cashout"})
    return req


def agent_cash_out_status(agent: Agent, request_id) -> CashOutRequest | None:
    req = CashOutRequest.objects.filter(pk=request_id, agent=agent).first()
    if req is not None:
        _expire_due([req])
    return req


def _expire_due(requests) -> None:
    now = timezone.now()
    for req in requests:
        if req.status == CashOutRequest.Status.PENDING and req.expires_at <= now:
            req.status, req.decided_at = CashOutRequest.Status.EXPIRED, now
            req.save(update_fields=["status", "decided_at", "updated_at"])


# --- back-office: agent lifecycle & float purchases (maker-checker) ---------------------
FLOAT_TOPUP_MAX_MINOR = 100_000_00          # per request [VERIFY policy]


@audited("agent.suspend", obj="agent", fields=("reason",))
def suspend_agent(agent: Agent, *, actor, reason: str) -> Agent:
    reason = (reason or "").strip()
    if not reason:
        raise AgentError("Give a reason for suspending the agent.")
    agent.status = Agent.Status.SUSPENDED
    agent.save(update_fields=["status", "updated_at"])
    _tell_agent(agent, title="Agent account suspended",
                body="Your SokoPay agent account has been suspended. Please contact SokoPay operations.",
                data={"type": "agent_status"})
    return agent


@audited("float.request", actor="requested_by", fields=("amount_minor", "payment_reference"))
def request_float_topup(*, agent: Agent, amount_minor: int, payment_reference: str, requested_by) -> FloatTopUp:
    """Maker: record an agent's float purchase against their payment reference."""
    _require_positive(amount_minor)
    if amount_minor > FLOAT_TOPUP_MAX_MINOR:
        raise AgentError("That's above the per-request float limit. Split it or ask finance.")
    ref = (payment_reference or "").strip()
    if not ref:
        raise AgentError("Enter the bank / MoMo reference of the agent's payment.")
    if FloatTopUp.objects.filter(payment_reference=ref).exists():
        raise AgentError("That payment reference has already been used for a float top-up.")
    from django.db import IntegrityError
    try:
        with transaction.atomic():
            return FloatTopUp.objects.create(agent=agent, amount_minor=amount_minor, payment_reference=ref[:64],
                                             requested_by=requested_by)
    except IntegrityError:   # two staff recording the same payment at the same moment
        raise AgentError("That payment reference has already been used for a float top-up.") from None


@audited("float.decide", actor="approver", fields=("approve", "note"))
def decide_float_topup(topup: FloatTopUp, *, approver, approve: bool, note: str = "") -> FloatTopUp:
    """Checker: a DIFFERENT staff member approves (credits the float) or rejects."""
    from django.utils import timezone as tz
    with transaction.atomic():
        topup = FloatTopUp.objects.select_for_update(of=("self",)).select_related("agent").get(pk=topup.pk)
        if topup.status != FloatTopUp.Status.PENDING:
            raise AgentError("This top-up has already been decided.")
        if approver.id == topup.requested_by_id:
            raise AgentError("A different staff member must approve this top-up.")
        if approve:
            topup_float(agent=topup.agent, amount_minor=topup.amount_minor)
            topup.status = FloatTopUp.Status.APPROVED
        else:
            topup.status = FloatTopUp.Status.REJECTED
        topup.decided_by, topup.decided_at, topup.note = approver, tz.now(), (note or "")[:255]
        topup.save(update_fields=["status", "decided_by", "decided_at", "note", "updated_at"])
    return topup


# --- helpers ----------------------------------------------------------------
def _require_active(agent: Agent) -> None:
    if not agent.is_active:
        raise AgentError("Agent is not active.")


def _require_positive(amount_minor: int) -> None:
    if not isinstance(amount_minor, int) or amount_minor <= 0:
        raise AgentError("Amount must be a positive integer of minor units.")


def _get_customer(identifier: str):
    """
    The customer's phone number or SokoPay wallet ID. They must already have an account.
    (This used to create one on the fly, so a mistyped number at cash-in put real money
    into a wallet nobody owned.)
    """
    from apps.wallet.accounts_lookup import AccountNotFound, resolve_account
    try:
        return resolve_account(identifier)
    except AccountNotFound as exc:
        raise AgentError(str(exc)) from exc


def lookup_customer(identifier: str) -> dict:
    """What the agent sees before crediting: a name to read back to the customer."""
    from apps.wallet.services import display_name
    user = _get_customer(identifier)
    return {"name": display_name(user), "phone": f"{user.phone[:6]}•••{user.phone[-4:]}"}
