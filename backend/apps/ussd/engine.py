"""
USSD menu (*XXX#) for customers on basic phones.

The gateway (an aggregator connected to MTN / Telecel / AT) calls us for every screen
with the session id, the caller's number (from the network, not typed by the user) and
what they just entered. We answer with the next screen: CON = wait for input, END = close.

    SokoPay
    1 Check balance
    2 Send to SokoPay user
    3 Buy airtime
    4 Cash out at agent
    5 Mini statement
    6 My wallet ID

Every money action asks for the PIN, checked with the same lockout as the app. The
session lives in the cache for USSD_SESSION_TTL seconds (networks end idle sessions in
about 2 minutes anyway). Nothing typed is logged.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.cache import cache

from apps.common.money import Money, MoneyError

SESSION_TTL = 180
MAX_SCREEN = 182
MENU = ("SokoPay\n1 Check balance\n2 Send to SokoPay user\n3 Buy airtime\n4 Cash out at agent\n"
        "5 Mini statement\n6 My wallet ID\n0 Exit")
NETWORKS = {"1": ("mtn", "MTN_AIRTIME"), "2": ("telecel", "TELECEL_AIRTIME"), "3": ("at", "AT_AIRTIME")}


@dataclass
class Screen:
    text: str
    end: bool

    def render(self) -> str:
        t = self.text if len(self.text) <= MAX_SCREEN else self.text[:MAX_SCREEN - 3] + "..."
        return ("END " if self.end else "CON ") + t


def con(text: str) -> Screen:
    return Screen(text, False)


def end(text: str) -> Screen:
    return Screen(text, True)


def _ghs(minor: int) -> str:
    return f"GHS {minor / 100:,.2f}"        # the cedi sign isn't in the GSM alphabet


def _key(session_id: str) -> str:
    return f"ussd:{session_id}"


def handle(*, session_id: str, phone: str, entry: str) -> Screen:
    """One screen. `entry` is what the user typed on this screen ('' on the first)."""
    state = cache.get(_key(session_id)) or {"step": "start"}
    try:
        screen, state = _step(state, phone, (entry or "").strip())
    except Exception as exc:   # noqa: BLE001 - any failure must close the session politely
        import logging
        logging.getLogger("sokopay.ussd").exception("USSD step failed: %s", type(exc).__name__)
        screen = end("Sorry, something went wrong. Please try again.")
    if screen.end:
        cache.delete(_key(session_id))
    else:
        cache.set(_key(session_id), state, timeout=SESSION_TTL)
    return screen


def _user(phone: str):
    return get_user_model().objects.filter(phone=phone, is_active=True).first()


def _pin_ok(user, pin: str) -> str | None:
    from apps.accounts import services as auth
    try:
        auth.confirm_pin(user, pin)
        return None
    except auth.AuthError as exc:
        return str(exc)


def _amount(raw: str) -> int | None:
    try:
        minor = Money.from_major(raw, "GHS").minor
    except MoneyError:
        return None
    return minor if minor > 0 else None


def _step(state: dict, phone: str, entry: str) -> tuple[Screen, dict]:
    user = _user(phone)
    if user is None:
        return end("Welcome to SokoPay. Get the SokoPay app or visit a SokoPay agent to open an account."), state
    if not user.pin_hash:
        return end("Set your PIN in the SokoPay app first, then dial again."), state
    step = state["step"]

    if step == "start":
        return con(MENU), {"step": "menu"}

    if step == "menu":
        if entry == "0":
            return end("Thank you for using SokoPay."), state
        if entry == "1":
            return con("Enter your PIN"), {"step": "balance_pin"}
        if entry == "2":
            return con("Enter phone number or SokoPay wallet ID"), {"step": "send_to"}
        if entry == "3":
            return con("Airtime network\n1 MTN\n2 Telecel\n3 AT"), {"step": "air_net"}
        if entry == "4":
            return _cash_out_start(user)
        if entry == "5":
            return con("Enter your PIN"), {"step": "mini_pin"}
        if entry == "6":
            from apps.wallet.accounts_lookup import format_wallet_number, wallet_number_for
            return end(f"Your SokoPay wallet ID: {format_wallet_number(wallet_number_for(user))}"), state
        return con("Invalid choice.\n" + MENU), state

    # --- 1 balance ---
    if step == "balance_pin":
        if (err := _pin_ok(user, entry)):
            return end(err), state
        from apps.wallet.services import balance
        return end(f"Your SokoPay balance is {_ghs(balance(user))}."), state

    # --- 2 send ---
    if step == "send_to":
        from apps.wallet.accounts_lookup import AccountNotFound, resolve_account
        from apps.wallet.services import display_name
        try:
            to = resolve_account(entry)
        except AccountNotFound:
            return end("No SokoPay account for that number or wallet ID."), state
        if to.id == user.id:
            return end("You can't send to yourself."), state
        return con(f"Send to {display_name(to)}\nEnter amount (GHS)"), {"step": "send_amount", "to": to.phone,
                                                                         "name": display_name(to)}
    if step == "send_amount":
        minor = _amount(entry)
        if minor is None:
            return con("Enter a valid amount (GHS)"), state
        return con(f"Send {_ghs(minor)} to {state['name']}\nEnter PIN to confirm"), {**state, "step": "send_pin",
                                                                                     "amount": minor}
    if step == "send_pin":
        if (err := _pin_ok(user, entry)):
            return end(err), state
        from apps.kyc.exceptions import KycError
        from apps.licensing.exceptions import CapabilityNotLicensed
        from apps.wallet.exceptions import WalletError
        from apps.wallet.services import send_p2p
        try:
            r = send_p2p(sender=user, recipient_phone=state["to"], amount_minor=state["amount"])
        except (WalletError, KycError) as exc:
            return end(str(exc)), state
        except CapabilityNotLicensed:
            return end("Sending money isn't available yet."), state
        return end(f"Sent {_ghs(state['amount'])} to {state['name'].rstrip('.')}. New balance {_ghs(r['new_balance_minor'])}."), state

    # --- 3 airtime ---
    if step == "air_net":
        if entry not in NETWORKS:
            return con("Choose 1, 2 or 3\n1 MTN\n2 Telecel\n3 AT"), state
        return con("Airtime for\n1 My number\n2 Another number"), {"step": "air_who", "net": entry}
    if step == "air_who":
        if entry == "1":
            return con("Enter amount (GHS)"), {**state, "step": "air_amount", "to": phone}
        if entry == "2":
            return con("Enter the phone number"), {**state, "step": "air_number"}
        return con("Choose 1 or 2\n1 My number\n2 Another number"), state
    if step == "air_number":
        from apps.bulk.validate import normalise_phone
        to = normalise_phone(entry)
        if to is None:
            return con("Enter a valid Ghana number, e.g. 0241234567"), state
        return con("Enter amount (GHS)"), {**state, "step": "air_amount", "to": to}
    if step == "air_amount":
        minor = _amount(entry)
        if minor is None:
            return con("Enter a valid amount (GHS)"), state
        return con(f"Buy {_ghs(minor)} airtime for {state['to']}\nEnter PIN to confirm"), {**state, "step": "air_pin",
                                                                                          "amount": minor}
    if step == "air_pin":
        if (err := _pin_ok(user, entry)):
            return end(err), state
        return _buy_airtime(user, state), state

    # --- 4 cash out ---
    if step == "co_decide":
        from apps.agents import services as agents
        from apps.agents.exceptions import AgentError
        try:
            if entry == "0":
                agents.decline_cash_out(customer=user, request_id=state["req"])
                return end("Cash-out declined. Don't take cash from the agent."), state
            req = agents.approve_cash_out(customer=user, request_id=state["req"], pin=entry)
        except AgentError as exc:
            return end(str(exc)), state
        if req.status == "approved":
            return end(f"Approved. Collect {_ghs(req.amount_minor)} from {req.agent.display_name}."), state
        return end(req.failure_reason or "The cash-out couldn't be completed."), state

    # --- 5 mini statement ---
    if step == "mini_pin":
        if (err := _pin_ok(user, entry)):
            return end(err), state
        from apps.activity.services import feed
        rows = feed(user, size=5)["results"]
        if not rows:
            return end("No transactions yet."), state
        lines = [f"{r['created_at'][5:10]} {r['amount_display'].replace(chr(8373), '')} {r['title'][:16]}" for r in rows]
        return end("Last 5:\n" + "\n".join(lines)), state

    return con(MENU), {"step": "menu"}


def _cash_out_start(user) -> tuple[Screen, dict]:
    from apps.agents import services as agents
    from apps.agents.exceptions import AgentError
    from apps.licensing.exceptions import CapabilityNotLicensed
    pending = agents.pending_cash_outs(user).first()
    if pending is not None:
        fee = f" Fee {_ghs(pending.fee_minor)}." if pending.fee_minor else ""
        return con(f"{pending.agent.display_name} wants to pay you {_ghs(pending.amount_minor)} cash.{fee}\n"
                   "Enter PIN to approve, or 0 to decline"), {"step": "co_decide", "req": str(pending.id)}
    try:
        agents.open_cash_out_window(user)
    except (AgentError, CapabilityNotLicensed):
        return end("Cash out isn't available yet."), {}
    from apps.wallet.accounts_lookup import format_wallet_number, wallet_number_for
    return end("Cash out is open for 10 minutes. Give the agent your number or wallet ID "
               f"{format_wallet_number(wallet_number_for(user))}. When the agent sends the request, "
               "dial again and choose 4 to approve."), {}


def _buy_airtime(user, state: dict) -> Screen:
    from apps.kyc.exceptions import KycError
    from apps.licensing.exceptions import CapabilityNotLicensed
    from apps.payments import services as payments
    from apps.payments.models import Biller, Payment
    network, code = NETWORKS[state["net"]]
    biller = Biller.objects.filter(code=code, is_active=True).first()
    if biller is None:
        return end("Airtime for that network isn't available right now.")
    try:
        p = payments.initiate_airtime(user=user, biller=biller, phone=state["to"], amount_minor=state["amount"],
                                      source=Payment.Source.WALLET)
    except (KycError, CapabilityNotLicensed) as exc:
        return end(str(exc) or "Airtime from the wallet isn't available yet.")
    except Exception as exc:   # noqa: BLE001 - payment errors carry a customer-safe message
        return end(str(exc) or "Airtime couldn't be bought. Try again.")
    if p.status == Payment.Status.FAILED:
        return end("Airtime couldn't be bought. You haven't been charged.")
    if p.status == Payment.Status.REFUNDED:
        return end("The network didn't deliver. Your money is back in your wallet.")
    return end(f"{_ghs(state['amount'])} airtime for {state['to']} {'sent' if p.status == 'succeeded' else 'is on its way'}."
               f" Ref {p.reference}.")
