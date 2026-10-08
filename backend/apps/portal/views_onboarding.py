"""
Portal pages for bringing people on board and managing passwords (see enrolment.py and
invites.py for the rules):

  Back office   admin/merchants/new/                     Add merchant (operations, compliance)
                admin/merchants/<pk>/documents/<doc>/    download a KYB document (same roles)
  Public        invite/<token>/                          set your password from an SMS invite
                password/forgot/ → password/reset/       reset a forgotten password by SMS code
                register/ → register/verify/             register your business (self-signup)
  Merchant      team/                                    team members, roles and invites

Agents are added on the back office Agents page (views_ops.agents_list).
"""

from __future__ import annotations

import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django_otp import login as otp_login

from apps.accounts import otp
from apps.common.audit import record as audit
from apps.merchants import documents
from apps.merchants.exceptions import MerchantError
from apps.merchants.models import Merchant, MerchantDocument, MerchantMember
from apps.notifications.sms import get_sms_provider
from apps.wallet.accounts_lookup import normalise_phone

from . import enrolment, invites, ratelimit
from .decorators import merchant_required, staff_role_required
from .models import SmsDevice

User = get_user_model()
RESEND_SECONDS = 30
DOC_KINDS = MerchantDocument.Kind.choices
BUSINESS_TYPES = Merchant.BusinessType.choices


def _errors(exc) -> str:
    if isinstance(exc, ValidationError):
        return " ".join(exc.messages)
    return str(exc)


def invite_link(request):
    """Builds the absolute set-password URL for a raw invite token (for invites.send_invite)."""
    return lambda raw: request.build_absolute_uri(reverse("portal:invite_accept", args=[raw]))


def send_member_invite(request, user, merchant, role) -> None:
    sent = invites.send_invite(user, link_for=invite_link(request), created_by=request.user,
                               merchant=merchant, role=role, ip=ratelimit.client_ip(request))
    if sent:
        messages.success(request, f"Invite sent by SMS to {user.phone}. The link works for "
                                  f"{invites.ttl_text()}.")
    else:
        messages.warning(request, f"Saved, but the SMS to {user.phone} couldn't be sent. Use “Resend invite”.")


# --- back office: add merchant, documents -------------------------------------------------
@staff_role_required("operations", "compliance")
def merchant_new(request):
    data = request.POST if request.method == "POST" else {}
    if request.method == "POST":
        try:
            merchant = enrolment.staff_add_merchant(actor=request.user, data=request.POST, files=request.FILES)
        except (enrolment.EnrolmentError, MerchantError) as exc:
            messages.error(request, str(exc))
        else:
            send_member_invite(request, merchant.owner, merchant, MerchantMember.Role.OWNER)
            return redirect("portal:merchant_detail", pk=merchant.pk)
    return render(request, "portal/admin/merchant_new.html",
                  {"data": data, "doc_kinds": DOC_KINDS, "business_types": BUSINESS_TYPES})


@staff_role_required("operations", "compliance")
def merchant_document(request, pk, doc):
    document = get_object_or_404(MerchantDocument.objects.select_related("merchant"), pk=doc, merchant_id=pk)
    try:
        data, content_type, name = documents.read(document)
    except (MerchantError, FileNotFoundError):
        raise Http404("This document's file isn't available.") from None
    audit("merchant.document_view", actor=request.user, obj=document.merchant, kind=document.kind)
    resp = HttpResponse(data, content_type=content_type)
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    resp["Cache-Control"] = "no-store"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


# --- public: invite ---------------------------------------------------------------------------
def invite_accept(request, token):
    if request.user.is_authenticated:
        logout(request)                       # a shared computer mustn't mix two people's sessions
    ip = ratelimit.client_ip(request)
    if ratelimit.is_blocked("invite_ip", ip):
        return render(request, "portal/invite_invalid.html", {"blocked": True}, status=429)
    invite = invites.open_invite(token)
    if invite is None:
        ratelimit.hit("invite_ip", ip)
        return render(request, "portal/invite_invalid.html", {}, status=404)
    if request.method == "POST":
        p1, p2 = request.POST.get("password1", ""), request.POST.get("password2", "")
        if p1 != p2:
            messages.error(request, "The two passwords don't match.")
        else:
            try:
                user = invites.redeem_invite(token, p1, ip=ip)
            except (invites.InviteError, ValidationError) as exc:
                messages.error(request, _errors(exc))
            else:
                messages.success(request, "Password set. Sign in with your phone number and new password.")
                return redirect(f"{reverse('portal:login')}?phone={user.phone}")
    business = (invite.merchant.trading_name or invite.merchant.legal_name) if invite.merchant else "SokoPay"
    return render(request, "portal/invite_accept.html",
                  {"invite": invite, "business": business, "phone": invite.user.phone})


# --- public: forgot password ------------------------------------------------------------------
_RESET_PHONE = "pw_reset_phone"


def password_forgot(request):
    if request.method == "POST":
        ip = ratelimit.client_ip(request)
        phone = normalise_phone(request.POST.get("phone", ""))
        if phone is None:
            messages.error(request, "Enter a valid Ghana phone number, e.g. 0244058519.")
        elif not ratelimit.allow("pw_reset_request_ip", ip):
            messages.error(request, "Too many requests from here. Please wait and try again.")
        else:
            invites.request_password_reset(phone)
            request.session[_RESET_PHONE] = phone
            return redirect("portal:password_reset")
    return render(request, "portal/password_forgot.html", {})


def password_reset(request):
    phone = request.session.get(_RESET_PHONE)
    if not phone:
        return redirect("portal:password_forgot")
    if request.method == "POST":
        if ratelimit.is_blocked("pw_reset_phone", phone):
            messages.error(request, "Too many attempts. Wait 15 minutes, then ask for a new code.")
            return redirect("portal:password_forgot")
        p1, p2 = request.POST.get("password1", ""), request.POST.get("password2", "")
        if p1 != p2:
            messages.error(request, "The two passwords don't match.")
        else:
            try:
                invites.reset_password(phone, request.POST.get("code", ""), p1, ip=ratelimit.client_ip(request))
            except ValidationError as exc:
                messages.error(request, _errors(exc))
            except invites.InviteError as exc:
                ratelimit.hit("pw_reset_phone", phone)
                messages.error(request, str(exc))
            else:
                request.session.pop(_RESET_PHONE, None)
                ratelimit.reset("pw_reset_phone", phone)
                messages.success(request, "Password changed. Sign in with your new password.")
                return redirect(f"{reverse('portal:login')}?phone={phone}")
    return render(request, "portal/password_reset.html",
                  {"phone": phone, "minutes": otp.ttl_minutes()})


# --- public: register your business -----------------------------------------------------------
_SIGNUP = "signup_application"
_SIGNUP_SENT = "signup_sms_at"


def _signup_enabled():
    if not getattr(settings, "MERCHANT_SELF_SIGNUP", True):
        raise Http404("Self sign-up is switched off.")


def _send_signup_code(request, phone) -> bool:
    try:
        code = otp.request_otp(phone, purpose=enrolment.SIGNUP_PURPOSE)
    except otp.OtpError as exc:
        messages.error(request, str(exc))
        return False
    result = get_sms_provider().send(phone, f"Your SokoPay business sign-up code is {code}. It expires in "
                                            f"{otp.ttl_minutes()} minutes. Never share it.")
    if not result.success:
        messages.error(request, "We couldn't send the SMS just now. Please try again in a moment.")
        return False
    request.session[_SIGNUP_SENT] = int(time.time())
    return True


def register(request):
    _signup_enabled()
    if request.user.is_authenticated:
        return redirect("portal:dashboard")
    data = request.POST if request.method == "POST" else request.session.get(_SIGNUP, {})
    if request.method == "POST":
        if not ratelimit.allow("register_ip", ratelimit.client_ip(request)):
            messages.error(request, "Too many sign-ups from here. Please wait and try again.")
        else:
            try:
                application = enrolment.signup_check(request.POST)
            except enrolment.EnrolmentError as exc:
                messages.error(request, str(exc))
            else:
                request.session[_SIGNUP] = application
                if _send_signup_code(request, application["phone"]):
                    return redirect("portal:register_verify")
    return render(request, "portal/register.html", {"data": data, "business_types": BUSINESS_TYPES})


def register_verify(request):
    _signup_enabled()
    application = request.session.get(_SIGNUP)
    if not application:
        return redirect("portal:register")
    phone = application["phone"]
    if request.method == "POST" and request.POST.get("action") == "resend":
        wait = RESEND_SECONDS - (int(time.time()) - request.session.get(_SIGNUP_SENT, 0))
        if wait > 0:
            messages.error(request, f"Please wait {wait} seconds before asking for another code.")
        elif _send_signup_code(request, phone):
            messages.success(request, "We've sent you a new code.")
        return redirect("portal:register_verify")
    if request.method == "POST":
        p1, p2 = request.POST.get("password1", ""), request.POST.get("password2", "")
        try:
            if p1 != p2:
                raise ValidationError("The two passwords don't match.")
            validate_password(p1, user=User(phone=phone, full_name=application["full_name"]))
            documents.validate_all(request.FILES)
            try:
                ok = otp.verify_otp(phone, request.POST.get("code", "").strip(), purpose=enrolment.SIGNUP_PURPOSE)
            except otp.OtpError as exc:
                raise enrolment.EnrolmentError(str(exc)) from exc
            if not ok:
                raise enrolment.EnrolmentError("That code is not correct.")
            user, merchant = enrolment.signup_finish(application=application, password=p1, files=request.FILES)
        except (ValidationError, enrolment.EnrolmentError, MerchantError) as exc:
            messages.error(request, _errors(exc))
        else:
            for key in (_SIGNUP, _SIGNUP_SENT):
                request.session.pop(key, None)
            login(request, user)
            # They just proved they hold the phone with an SMS code: that is this session's 2FA.
            device, _ = SmsDevice.objects.get_or_create(user=user, defaults={"name": "SMS", "confirmed": True})
            otp_login(request, device)
            messages.success(request, f"Application for {merchant.trading_name or merchant.legal_name} submitted. "
                                      "We'll review it and text you the result.")
            return redirect("portal:dashboard")
    return render(request, "portal/register_verify.html",
                  {"application": application, "minutes": otp.ttl_minutes(), "doc_kinds": DOC_KINDS})


# --- merchant portal: team -----------------------------------------------------------------------
@merchant_required
def team(request):
    merchant, role = request.merchant, request.merchant_role
    manager = enrolment.can_manage_team(role)
    if request.method == "POST":
        if not manager:
            messages.error(request, "Only the owner or an admin can manage the team.")
            return redirect("portal:team")
        action = request.POST.get("action")
        member = None
        if action in ("role", "remove", "resend"):
            member = get_object_or_404(merchant.members.select_related("user"), pk=request.POST.get("member"))
        try:
            if action == "invite":
                if not ratelimit.allow("team_invite", str(request.user.id)):
                    raise enrolment.EnrolmentError("You've sent a lot of invites. Please wait and try again.")
                new = enrolment.invite_member(merchant=merchant, actor=request.user, actor_role=role,
                                              phone=request.POST.get("phone", ""),
                                              full_name=request.POST.get("full_name", ""),
                                              role=request.POST.get("role", ""))
                send_member_invite(request, new.user, merchant, new.role)
            elif action == "role":
                enrolment.change_role(merchant=merchant, actor=request.user, actor_role=role,
                                      member=member, role=request.POST.get("role", ""))
                messages.success(request, f"{member.user.full_name or member.user.phone} is now "
                                          f"{member.get_role_display().lower()}.")
            elif action == "remove":
                enrolment.remove_member(merchant=merchant, actor=request.user, actor_role=role, member=member)
                messages.success(request, f"{member.user.full_name or member.user.phone} was removed from the team.")
            elif action == "resend":
                if member.role == MerchantMember.Role.OWNER and role != MerchantMember.Role.OWNER:
                    raise enrolment.EnrolmentError("Only the owner can do that.")
                if not ratelimit.allow("team_invite", str(request.user.id)):
                    raise enrolment.EnrolmentError("You've sent a lot of invites. Please wait and try again.")
                send_member_invite(request, member.user, merchant, member.role)
        except enrolment.EnrolmentError as exc:
            messages.error(request, str(exc))
        return redirect("portal:team")
    members = list(merchant.members.select_related("user").order_by("created_at"))
    for m in members:
        m.has_password = m.user.has_usable_password()
        m.editable = (manager and m.user_id != request.user.id and m.role != MerchantMember.Role.OWNER
                      and not (role == MerchantMember.Role.ADMIN and m.role == MerchantMember.Role.ADMIN))
    roles = [(r, MerchantMember.Role(r).label) for r in enrolment.INVITABLE_ROLES
             if role == MerchantMember.Role.OWNER or r != MerchantMember.Role.ADMIN]
    return render(request, "portal/merchant/team.html",
                  {"members": members, "manager": manager, "roles": roles, "merchant": merchant})
