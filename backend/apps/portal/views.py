"""
Portal views (server-rendered, HTMX-enhanced).

Two portals share this module:
  * Merchant portal  (/dashboard/…)        — for merchant users
  * Admin portal     (/dashboard/admin/…)  — for SokoPay staff

Views are thin: they read data and call the tested domain services in apps.merchants,
apps.ledger and apps.payments. No money logic is re-implemented here.
"""

from __future__ import annotations

import base64
import re
import time

import segno
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.core.exceptions import ValidationError
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django_otp import login as otp_login
from django_otp import match_token
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts import otp
from apps.common.audit import record as audit
from apps.common.money import Money, MoneyError
from apps.compliance.models import Alert as AmlAlert
from apps.merchants import api_keys, onboarding, settlement
from apps.merchants.exceptions import ApprovalError, MerchantError, SettlementError
from apps.merchants.models import ApiKey, Merchant, Settlement, SettlementAccount
from apps.payments.models import Payment
from apps.wallet.accounts_lookup import normalise_phone

from . import invites, ratelimit, security
from .decorators import merchant_required, staff_required
from .forms import LoginForm, RequestSettlementForm, SettlementAccountForm
from .models import SecurityEvent, SmsDevice


def _throttled(request, action: str) -> bool:
    """True (and an error message) if this user has done `action` too often lately."""
    if ratelimit.allow(action, str(request.user.id)):
        return False
    messages.error(request, "You've done that too many times in the last hour. Please wait and try again.")
    return True


def _ghs(minor: int) -> str:
    return Money(int(minor), "GHS").format()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
LOCKED_MESSAGE = "Too many attempts. Wait 15 minutes, then try again."


def login_view(request):
    if request.user.is_authenticated:
        return _home_redirect(request.user)
    form = LoginForm(request.POST or None, initial={"phone": request.GET.get("phone", "")})
    if request.method == "POST" and form.is_valid():
        raw = form.cleaned_data["phone"].strip()
        phone = normalise_phone(raw) or raw            # 0244 058 519 and +233244058519 both work
        ip = ratelimit.client_ip(request)
        # Check the lock BEFORE the password, so a locked account can't be used as an
        # oracle ("locked" for right guesses vs "wrong" for wrong ones).
        if ratelimit.is_blocked("login_phone", phone) or ratelimit.is_blocked("login_ip", ip):
            messages.error(request, LOCKED_MESSAGE)
            return render(request, "portal/login.html", {"form": form}, status=429)
        user = authenticate(request, username=phone, password=form.cleaned_data["password"])
        if user is None or not user.is_active:
            phone_fails = ratelimit.hit("login_phone", phone)
            ratelimit.hit("login_ip", ip)
            if phone_fails == ratelimit.rule("login_phone")[0]:
                from apps.accounts.models import User
                security.record(SecurityEvent.Kind.LOGIN_LOCKED, ip=ip,
                                user=User.objects.filter(phone=phone).first())
            # Same message whether or not the phone exists (no account enumeration).
            messages.error(request, "Wrong phone or password.")
        else:
            ratelimit.reset("login_phone", phone)
            login(request, user)
            return _home_redirect(user)
    return render(request, "portal/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("portal:login")


def _home_redirect(user):
    if getattr(user, "user_type", None) == "staff":
        # Staff must pass two-factor before reaching the back-office.
        return redirect("portal:twofa")
    # Merchant users: merchant_required sends them to 2FA if their role needs it.
    return redirect("portal:dashboard")


def _after_2fa(user):
    if getattr(user, "user_type", None) == "staff":
        return redirect("portal:admin_dashboard")
    return redirect("portal:dashboard")


# ---------------------------------------------------------------------------
# Two-factor authentication — staff and merchants. Codes come from an authenticator app
# (TOTP), a one-time backup code, or (PORTAL_2FA_SMS) an SMS to the user's phone.
# ---------------------------------------------------------------------------
SMS_RESEND_SECONDS = 30
_SMS_SENT_AT = "twofa_sms_at"


def _sms_2fa(user) -> bool:
    return bool(getattr(settings, "PORTAL_2FA_SMS", False) and getattr(user, "phone", ""))


def _phone_hint(phone: str) -> str:
    return f"{phone[:4]} ••• ••• {phone[-3:]}" if len(phone) > 7 else "your phone"


def _send_sms_code(request, user) -> bool:
    """Text a 2FA code to the user. On failure, explains why and returns False."""
    device, _ = SmsDevice.objects.get_or_create(user=user, defaults={"name": "SMS", "confirmed": True})
    try:
        sent = device.generate_challenge()
    except otp.OtpError as exc:
        messages.error(request, str(exc))
        return False
    if not sent:
        messages.error(request, "We couldn't send the SMS just now. Try again in a moment"
                                " or use your authenticator app.")
        return False
    request.session[_SMS_SENT_AT] = int(time.time())
    return True


def twofa(request):
    """
    Single entry point for 2FA, for staff and merchant users.

    With PORTAL_2FA_SMS on (the default on servers), the page texts a code to the user's
    phone on arrival and offers "Resend"; the SMS code, an authenticator code or a backup
    code all work. Users can still add an authenticator app (?setup=app).

    Otherwise it enrols a user who has no confirmed TOTP device (QR + secret, confirmed by
    the first valid code, then ten one-time backup codes shown once), and asks everyone
    else for the current code or a backup code.

    Five wrong codes in 15 minutes signs the user out and locks 2FA.
    """
    user = request.user
    if not user.is_authenticated:
        return redirect("portal:login")
    if not security.is_portal_user(user):
        raise PermissionDenied("No portal access for this account.")
    if user.is_verified():
        return _after_2fa(user)

    ident = str(user.id)
    ip = ratelimit.client_ip(request)
    if ratelimit.is_blocked("twofa_user", ident):
        logout(request)
        messages.error(request, LOCKED_MESSAGE)
        return redirect("portal:login")

    confirmed = TOTPDevice.objects.filter(user=user, confirmed=True).first()
    sms = _sms_2fa(user) and request.GET.get("setup") != "app"

    if request.method == "POST":
        if sms and request.POST.get("action") == "resend":
            wait = SMS_RESEND_SECONDS - (int(time.time()) - request.session.get(_SMS_SENT_AT, 0))
            if wait > 0:
                messages.error(request, f"Please wait {wait} seconds before asking for another code.")
            elif _send_sms_code(request, user):
                messages.success(request, "We've sent you a new code.")
            return redirect("portal:twofa")

        token = request.POST.get("token", "").strip()
        if confirmed or sms:
            # match_token tries every confirmed device: the SMS device and/or the app.
            device = match_token(user, re.sub(r"\D", "", token)) if len(token) <= 8 else None
            used_backup = False
            if device is None and confirmed and security.consume_backup_code(user, token):
                device, used_backup = confirmed, True
            if device is not None:
                otp_login(request, device)           # mark the session OTP-verified
                ratelimit.reset("twofa_user", ident)
                request.session.pop(_SMS_SENT_AT, None)
                if used_backup:
                    left = security.backup_codes_left(user)
                    security.record(SecurityEvent.Kind.BACKUP_USED, user=user, ip=ip,
                                    detail=f"{left} left")
                    messages.warning(request, f"Signed in with a backup code. {left} left — "
                                              "create new ones on the Security page if you're running low.")
                else:
                    messages.success(request, "Two-factor verified.")
                return _after_2fa(user)
        else:
            device = TOTPDevice.objects.filter(user=user, confirmed=False).first()
            if device and device.verify_token(re.sub(r"\D", "", token)):
                device.confirmed = True
                device.save()
                otp_login(request, device)
                ratelimit.reset("twofa_user", ident)
                codes = security.generate_backup_codes(user)
                security.record(SecurityEvent.Kind.TWOFA_ENROLLED, user=user, ip=ip)
                return render(request, "portal/twofa_backup_codes.html",
                              {"codes": codes, "first_time": True})

        if ratelimit.hit("twofa_user", ident) >= ratelimit.rule("twofa_user")[0]:
            security.record(SecurityEvent.Kind.TWOFA_LOCKED, user=user, ip=ip)
            logout(request)
            messages.error(request, LOCKED_MESSAGE)
            return redirect("portal:login")
        messages.error(request, "That code is not valid. Try again.")

    if sms:
        # Text a code on arrival, unless one sent in this session is still valid.
        if int(time.time()) - request.session.get(_SMS_SENT_AT, 0) > otp.ttl_seconds():
            _send_sms_code(request, user)
        return render(request, "portal/twofa_sms.html",
                      {"phone_hint": _phone_hint(user.phone), "minutes": otp.ttl_minutes(),
                       "has_app": confirmed is not None})

    if confirmed:
        return render(request, "portal/twofa_verify.html", {})

    # Enrolment: ensure exactly one pending device, then show its QR + secret.
    device = TOTPDevice.objects.filter(user=user, confirmed=False).first()
    if device is None:
        device = TOTPDevice.objects.create(user=user, confirmed=False, name="default")
    secret = base64.b32encode(device.bin_key).decode()
    qr_data_uri = segno.make(device.config_url).svg_data_uri(scale=5)
    return render(request, "portal/twofa_setup.html",
                  {"secret": secret, "qr": qr_data_uri,
                   "is_staff_user": getattr(user, "user_type", None) == "staff"})


def security_page(request):
    """2FA status and backup-code regeneration, for staff and merchant users alike."""
    if getattr(request.user, "user_type", None) == "staff":
        return staff_required(_security)(request)
    return merchant_required(_security)(request)


def _security(request):
    user = request.user
    enrolled = security.has_confirmed_device(user)
    if request.method == "POST" and request.POST.get("action") == "regenerate":
        ident = str(user.id)
        if not enrolled:
            return redirect("portal:twofa")
        if not ratelimit.allow("backup_regen", ident):
            messages.error(request, LOCKED_MESSAGE)
            return redirect("portal:security")
        # Re-prove possession of the authenticator before issuing new recovery codes.
        token = re.sub(r"\D", "", request.POST.get("token", ""))
        if match_token(user, token) is None:
            if ratelimit.hit("twofa_user", ident) >= ratelimit.rule("twofa_user")[0]:
                security.record(SecurityEvent.Kind.TWOFA_LOCKED, user=user,
                                ip=ratelimit.client_ip(request))
                logout(request)
                messages.error(request, LOCKED_MESSAGE)
                return redirect("portal:login")
            messages.error(request, "That code is not valid.")
            return redirect("portal:security")
        codes = security.generate_backup_codes(user)
        security.record(SecurityEvent.Kind.BACKUP_REGENERATED, user=user,
                        ip=ratelimit.client_ip(request))
        return render(request, "portal/twofa_backup_codes.html", {"codes": codes})

    if request.method == "POST" and request.POST.get("action") == "change_password":
        if not ratelimit.allow("password_change", str(user.id)):
            messages.error(request, LOCKED_MESSAGE)
        elif request.POST.get("new1", "") != request.POST.get("new2", ""):
            messages.error(request, "The two new passwords don't match.")
        else:
            try:
                invites.change_password(user, request.POST.get("current", ""), request.POST.get("new1", ""),
                                        ip=ratelimit.client_ip(request))
            except (invites.InviteError, ValidationError) as exc:
                messages.error(request, " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
            else:
                update_session_auth_hash(request, user)      # stay signed in here; other sessions end
                messages.success(request, "Password changed.")
        return redirect("portal:security")

    role = getattr(request, "merchant_role", None)
    return render(request, "portal/security.html", {
        "sms_2fa": _sms_2fa(user),
        "phone_hint": _phone_hint(user.phone),
        "enrolled": enrolled,
        "required": security.needs_2fa(user, role) if not enrolled else True,
        "role_requires": getattr(user, "user_type", None) == "staff"
                         or (role and security.merchant_role_requires_2fa(role)),
        "codes_left": security.backup_codes_left(user),
        "events": user.security_events.all()[:10],
    })


# ---------------------------------------------------------------------------
# Merchant portal
# ---------------------------------------------------------------------------
@merchant_required
def dashboard(request):
    merchant = request.merchant
    available = settlement.available_balance_minor(merchant)   # less disputed amounts on hold
    recent = Payment.objects.filter(merchant=merchant).order_by("-created_at")[:10]
    context = {
        "merchant": merchant,
        "available_display": _ghs(available),
        "recent": recent,
        "ghs": _ghs,
    }
    return render(request, "portal/merchant/dashboard.html", context)


@merchant_required
def payments_list(request):
    qs = Payment.objects.filter(merchant=request.merchant).order_by("-created_at")
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    # HTMX requests swap only the table body for snappy filtering.
    template = "portal/merchant/_payments_rows.html" if request.htmx else "portal/merchant/payments.html"
    return render(request, template, {"payments": qs[:100], "ghs": _ghs, "merchant": request.merchant})


@merchant_required
def api_keys_view(request):
    merchant = request.merchant
    new_secret = None
    if request.method == "POST":
        role = request.merchant_role
        mode = request.POST.get("mode", ApiKey.Mode.TEST)
        if role not in ("owner", "admin", "developer"):
            messages.error(request, "Your role cannot manage API keys.")
        elif "revoke" in request.POST:
            key = get_object_or_404(ApiKey, pk=request.POST["revoke"], merchant=merchant)
            if role == "developer" and key.mode == ApiKey.Mode.LIVE:
                messages.error(request, "Developers can only manage test keys.")
            else:
                api_keys.revoke_key(key)
                audit("apikey.revoke", actor=request.user, obj=key, summary=f"API key {key.prefix} revoked")
                messages.success(request, f"Key {key.prefix}… revoked.")
        elif mode not in ApiKey.Mode.values:
            messages.error(request, "Unknown key mode.")
        elif mode == ApiKey.Mode.LIVE and (role == "developer" or not merchant.is_live):
            # Live keys move real money: Owner/Admin only, and only once approved.
            messages.error(request, "Live keys need an approved business and an Owner or Admin.")
        elif _throttled(request, "api_key_create"):
            pass
        else:
            key, new_secret = api_keys.issue_key(merchant, mode)
            audit("apikey.issue", actor=request.user, obj=key, summary=f"{mode} API key {key.prefix} issued")
            messages.success(request, "New key created. Copy it now — it is shown only once.")
    keys = merchant.api_keys.order_by("-created_at")
    return render(request, "portal/merchant/api_keys.html",
                  {"keys": keys, "new_secret": new_secret, "merchant": merchant})


@merchant_required
def settlements_view(request):
    merchant = request.merchant
    available = settlement.available_balance_minor(merchant)   # less disputed amounts on hold
    acct_form = SettlementAccountForm()
    req_form = RequestSettlementForm()

    if request.method == "POST":
        if "add_account" in request.POST:
            # Changing where money is paid out is as sensitive as paying it out.
            if request.merchant_role not in ("owner", "finance"):
                messages.error(request, "Only Owner or Finance can add a settlement account.")
                return redirect("portal:settlements")
            if _throttled(request, "settlement_account"):
                return redirect("portal:settlements")
            acct_form = SettlementAccountForm(request.POST)
            if acct_form.is_valid():
                SettlementAccount.objects.create(merchant=merchant, **acct_form.cleaned_data)
                messages.success(request, "Settlement account added. It will be name-verified.")
                return redirect("portal:settlements")
        elif "request_settlement" in request.POST:
            if request.merchant_role not in ("owner", "finance"):
                messages.error(request, "Only Owner or Finance can request a settlement.")
                return redirect("portal:settlements")
            if _throttled(request, "settlement_request"):
                return redirect("portal:settlements")
            req_form = RequestSettlementForm(request.POST)
            if req_form.is_valid():
                _request_settlement(request, merchant, req_form.cleaned_data.get("amount"))
                return redirect("portal:settlements")

    context = {
        "merchant": merchant,
        "available_display": _ghs(available),
        "accounts": merchant.settlement_accounts.all(),
        "settlements": merchant.settlements.order_by("-created_at")[:20],
        "acct_form": acct_form,
        "req_form": req_form,
        "ghs": _ghs,
    }
    return render(request, "portal/merchant/settlements.html", context)


def _request_settlement(request, merchant, amount):
    destination = merchant.settlement_accounts.filter(is_default=True).first() \
        or merchant.settlement_accounts.first()
    if destination is None:
        messages.error(request, "Add a settlement account first.")
        return
    amount_minor = Money.from_major(amount, "GHS").minor if amount else None
    try:
        s = settlement.request_settlement(
            merchant=merchant, destination=destination,
            requested_by=request.user, amount_minor=amount_minor,
        )
    except (SettlementError, MerchantError) as exc:
        messages.error(request, str(exc))
        return
    if s.status == Settlement.Status.AWAITING_APPROVAL:
        messages.info(request, "Settlement submitted for approval (large amount or new account).")
    else:
        messages.success(request, "Settlement is processing. Funds will arrive shortly.")


@merchant_required
def qr_page(request):
    """Static QR for the counter, plus dynamic per-sale payment requests."""
    from apps.merchants import qr
    merchant = request.merchant

    if request.method == "POST":
        if request.merchant_role not in ("owner", "admin", "finance", "cashier"):
            messages.error(request, "Your role cannot create payment requests.")
            return redirect("portal:qr")
        if _throttled(request, "qr_request"):
            return redirect("portal:qr")
        raw = request.POST.get("amount", "").strip()
        amount_minor = None
        if raw:
            try:
                amount_minor = Money.from_major(raw, "GHS").minor
            except MoneyError:
                messages.error(request, "Enter the amount as a number, e.g. 25.00")
                return redirect("portal:qr")
        try:
            req = qr.create_payment_request(
                merchant=merchant, created_by=request.user, amount_minor=amount_minor,
                description=request.POST.get("description", ""),
            )
        except MerchantError as exc:
            messages.error(request, str(exc))
            return redirect("portal:qr")
        return redirect("portal:qr_request", token=req.token)

    recent = merchant.payment_requests.order_by("-created_at")[:20]
    for r in recent:
        qr.expire_if_due(r)
    context = {
        "merchant": merchant,
        "short_code": qr.ensure_short_code(merchant) if merchant.is_live else None,
        "static_qr": qr.qr_svg_data_uri(qr.static_payload(merchant)) if merchant.is_live else None,
        "recent": recent,
    }
    return render(request, "portal/merchant/qr.html", context)


@merchant_required
def qr_request(request, token):
    from apps.merchants import qr
    from apps.merchants.models import PaymentRequest
    req = get_object_or_404(PaymentRequest, token=token, merchant=request.merchant)
    qr.expire_if_due(req)
    if request.method == "POST" and request.POST.get("action") == "cancel":
        if req.status == PaymentRequest.Status.OPEN:
            req.status = PaymentRequest.Status.CANCELLED
            req.save(update_fields=["status", "updated_at"])
        return redirect("portal:qr")
    return render(request, "portal/merchant/qr_request.html", {
        "req": req,
        "qr": qr.qr_svg_data_uri(qr.request_payload(req)),
        "amount_display": _ghs(req.amount_minor) if req.amount_minor else "Customer enters amount",
    })


@merchant_required
def qr_request_status(request, token):
    """HTMX partial polled by the request page until the customer has paid."""
    from apps.merchants import qr
    from apps.merchants.models import PaymentRequest
    req = get_object_or_404(PaymentRequest, token=token, merchant=request.merchant)
    qr.expire_if_due(req)
    return render(request, "portal/merchant/_qr_status.html", {"req": req})


# ---------------------------------------------------------------------------
# Admin / back-office portal
# ---------------------------------------------------------------------------
@staff_required
def admin_dashboard(request):
    by_status = dict(
        Merchant.objects.values_list("status").annotate(n=Count("id")).values_list("status", "n")
    )
    context = {
        "merchant_counts": by_status,
        "pending_review": Merchant.objects.filter(
            status__in=[Merchant.Status.SUBMITTED, Merchant.Status.IN_REVIEW]
        ).count(),
        "settlements_awaiting": Settlement.objects.filter(
            status=Settlement.Status.AWAITING_APPROVAL
        ).count(),
        "payouts_stuck": settlement.stuck_settlements().count(),
        "aml_high_open": AmlAlert.objects.filter(
            severity="high", status__in=("open", "investigating", "escalated")).count(),
    }
    return render(request, "portal/admin/dashboard.html", context)


@staff_required
def safeguarding(request):
    """E-money vs trust funds. Staff record bank-statement balances and run the check."""
    from django.utils import timezone

    from apps.safeguarding import services as sg
    from apps.safeguarding.models import SafeguardingCheck, TrustAccount

    if request.method == "POST":
        if request.POST.get("action") == "record":
            account = get_object_or_404(TrustAccount, pk=request.POST.get("account"))
            try:
                minor = Money.from_major(request.POST.get("balance", ""), "GHS").minor
            except MoneyError:
                minor = -1
            if minor < 0:
                messages.error(request, "Enter the balance as a positive number, e.g. 125000.00")
                return redirect("portal:safeguarding")
            sg.record_trust_balance(account=account, balance_minor=minor,
                                    as_of=timezone.now(), actor=request.user)
            messages.success(request, f"Balance recorded for {account}.")
        check = sg.run_check()
        level = messages.success if check.status == SafeguardingCheck.Status.OK else messages.error
        level(request, f"Safeguarding check: {check.get_status_display()}.")
        return redirect("portal:safeguarding")

    accounts = [(a, a.balances.order_by("-as_of").first())
                for a in TrustAccount.objects.filter(is_active=True)]
    return render(request, "portal/admin/safeguarding.html", {
        "liabilities": sg.e_money_liabilities(),
        "latest": SafeguardingCheck.objects.order_by("-created_at").first(),
        "history": SafeguardingCheck.objects.order_by("-created_at")[:14],
        "accounts": accounts,
    })


@staff_required
def integrations(request):
    """Every external connector and whether it's live, a placeholder, or a dev mock."""
    from apps.connectors.registry import catalogue
    rows = catalogue()
    category = request.GET.get("category")
    if category:
        rows = [r for r in rows if r["category"] == category]
    return render(request, "portal/admin/integrations.html", {
        "rows": rows,
        "categories": ["biller", "telco", "transfer", "card", "insurance", "lending",
                       "savings", "investment", "pension", "ticketing", "food",
                       "cross_border", "identity", "bank", "remittance"],
        "active": category,
    })


@staff_required
def merchant_queue(request):
    merchants = Merchant.objects.filter(
        status__in=[Merchant.Status.SUBMITTED, Merchant.Status.IN_REVIEW]
    ).order_by("created_at")
    return render(request, "portal/admin/merchant_queue.html", {"merchants": merchants})


def _tell_owner(request, merchant, *, approved: bool) -> None:
    """SMS the owner the review outcome (best effort; the decision stands either way)."""
    from apps.notifications.sms import get_sms_provider
    name = merchant.trading_name or merchant.legal_name
    if approved:
        text = (f"SokoPay: good news, {name} is approved. Sign in at "
                f"{request.build_absolute_uri(reverse('portal:login'))} to add your settlement account "
                f"and start taking payments. Your merchant code is {merchant.short_code}.")
    else:
        text = (f"SokoPay: we couldn't approve {name} yet. Reason: {merchant.rejection_reason or 'see the portal'}. "
                f"Sign in at {request.build_absolute_uri(reverse('portal:login'))} or contact SokoPay support.")
    get_sms_provider().send(merchant.owner.phone, text)


@staff_required
def merchant_detail(request, pk):
    merchant = get_object_or_404(Merchant, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "reset_2fa":
            # Lost authenticator AND backup codes. Ops must verify the person first
            # (call-back on the registered number, ID check) — the reset is audited.
            member = get_object_or_404(merchant.members.select_related("user"),
                                       pk=request.POST.get("member"))
            security.reset_2fa(member.user, actor=request.user, ip=ratelimit.client_ip(request))
            messages.success(request, f"2FA reset for {member.user.full_name or member.user.phone}. "
                                      "They will set it up again at next sign-in.")
            return redirect("portal:merchant_detail", pk=merchant.pk)
        if action == "resend_invite":
            from .views_onboarding import send_member_invite
            member = get_object_or_404(merchant.members.select_related("user"),
                                       pk=request.POST.get("member"))
            send_member_invite(request, member.user, merchant, member.role)
            return redirect("portal:merchant_detail", pk=merchant.pk)
        try:
            if action == "begin_review":
                onboarding.begin_review(merchant)
            elif action == "approve":
                onboarding.approve(merchant, risk_tier=request.POST.get("risk_tier", "medium"))
                audit("merchant.approve", actor=request.user, obj=merchant, risk_tier=request.POST.get("risk_tier", "medium"))
                _tell_owner(request, merchant, approved=True)
                messages.success(request, f"{merchant} approved.")
            elif action == "reject":
                onboarding.reject(merchant, reason=request.POST.get("reason", ""))
                audit("merchant.reject", actor=request.user, obj=merchant, reason=request.POST.get("reason", ""))
                _tell_owner(request, merchant, approved=False)
                messages.success(request, f"{merchant} rejected.")
        except Exception as exc:  # InvalidTransition etc. — surface to the reviewer
            messages.error(request, str(exc))
        return redirect("portal:merchant_detail", pk=merchant.pk)
    members = list(merchant.members.select_related("user"))
    for mem in members:
        mem.has_2fa = security.has_confirmed_device(mem.user)
        mem.has_password = mem.user.has_usable_password()
    return render(request, "portal/admin/merchant_detail.html",
                  {"m": merchant, "documents": merchant.documents.all(), "members": members})


@staff_required
def settlement_queue(request):
    pending = Settlement.objects.filter(
        status=Settlement.Status.AWAITING_APPROVAL
    ).select_related("merchant", "destination").order_by("created_at")
    if request.method == "POST":
        s = get_object_or_404(Settlement, pk=request.POST.get("settlement"))
        if request.POST.get("action") == "recheck":
            s = settlement.refresh_settlement(s)
            messages.info(request, f"Re-checked with the payment partner: {s.get_status_display()}.")
            return redirect("portal:settlement_queue")
        try:
            if request.POST.get("action") == "approve":
                settlement.approve_settlement(s, checker=request.user)
                messages.success(request, "Settlement approved and processing.")
            else:
                settlement.reject_settlement(s, checker=request.user,
                                             reason=request.POST.get("reason", ""))
                messages.success(request, "Settlement rejected.")
        except ApprovalError as exc:
            # e.g. the maker trying to approve their own request.
            messages.error(request, str(exc))
        return redirect("portal:settlement_queue")
    return render(request, "portal/admin/settlement_queue.html",
                  {"settlements": pending, "stuck": settlement.stuck_settlements()[:50], "ghs": _ghs})
