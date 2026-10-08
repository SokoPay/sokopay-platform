"""
Bringing real people onto SokoPay from the portals (no seed data, no shell):

* staff_add_merchant  back office "Add merchant": owner + business + KYB documents,
                      straight into the review queue. The view then texts the owner an
                      invite link to set their own password (apps.portal.invites).
* signup_*            public "Register your business": the applicant proves their phone
                      with an SMS code, sets a password, and the application waits for
                      staff review (MERCHANT_SELF_SIGNUP).
* invite_member / change_role / remove_member
                      the merchant portal's Team page (owner and admins only).
* staff_add_agent     back office "Add agent" (agents are never self-registered): creates
                      the person if they're new, registers them as a pending agent, and
                      the view texts them how to get started.

A phone number belongs to at most one business on SokoPay, and staff accounts are never
added to a business. Every change is written to the audit log.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.agents import services as agents
from apps.common.audit import record as audit
from apps.merchants import documents, onboarding
from apps.merchants.models import Merchant, MerchantMember
from apps.wallet.accounts_lookup import normalise_phone

from .models import PortalInvite

User = get_user_model()
Role = MerchantMember.Role

INVITABLE_ROLES = (Role.ADMIN, Role.FINANCE, Role.CASHIER, Role.DEVELOPER)
MANAGER_ROLES = (Role.OWNER, Role.ADMIN)
SIGNUP_PURPOSE = "signup"


class EnrolmentError(Exception):
    """The request can't be done as asked (message is safe to show to the user)."""


def phone_or_error(value: str) -> str:
    phone = normalise_phone(value)
    if phone is None:
        raise EnrolmentError("Enter a valid Ghana phone number, e.g. 0244058519.")
    return phone


def _person(phone: str, full_name: str, user_type: str):
    """The existing account for `phone`, or a new one with no password and no PIN."""
    user = User.objects.filter(phone=phone).first()
    if user is not None:
        if not user.is_active or user.closed_at is not None:
            raise EnrolmentError("The SokoPay account for this number is closed or suspended.")
        if full_name and not user.full_name:
            user.full_name = full_name
            user.save(update_fields=["full_name", "updated_at"])
        return user, False
    return User.objects.create_user(phone=phone, full_name=full_name, user_type=user_type), True


def _free_for_business(user) -> None:
    if user.user_type == "staff":
        raise EnrolmentError("SokoPay staff accounts can't be added to a business.")
    if user.merchant_memberships.exists():
        raise EnrolmentError("This number is already linked to a business on SokoPay.")


def _business_fields(data: dict) -> dict:
    legal = (data.get("legal_name") or "").strip()
    if not legal:
        raise EnrolmentError("Enter the business's legal name.")
    btype = data.get("business_type") or ""
    if btype not in Merchant.BusinessType.values:
        raise EnrolmentError("Choose a business type.")
    return {
        "legal_name": legal[:200],
        "trading_name": (data.get("trading_name") or "").strip()[:200],
        "business_type": btype,
        "category": (data.get("category") or "").strip()[:64],
        "tin": (data.get("tin") or "").strip()[:32],
        "registration_no": (data.get("registration_no") or "").strip()[:64],
    }


# --- back office: add merchant ------------------------------------------------------
def staff_add_merchant(*, actor, data: dict, files) -> Merchant:
    """Create owner + merchant + documents and submit it for review. Returns the merchant."""
    phone = phone_or_error(data.get("owner_phone", ""))
    owner_name = (data.get("owner_name") or "").strip()[:150]
    if not owner_name:
        raise EnrolmentError("Enter the owner's full name.")
    fields = _business_fields(data)
    documents.validate_all(files)
    with transaction.atomic():
        owner, created = _person(phone, owner_name, "merchant")
        _free_for_business(owner)
        merchant = onboarding.create_merchant(owner=owner, **fields)
        documents.store_all(merchant, files)
        onboarding.submit_for_review(merchant)
        audit("merchant.create", actor=actor, obj=merchant, channel="back_office", new_person=created)
    return merchant


# --- public: register your business -----------------------------------------------------
def signup_check(data: dict) -> dict:
    """Validate step 1 of self-signup. Returns the cleaned application (kept in the session)."""
    phone = phone_or_error(data.get("phone", ""))
    name = (data.get("full_name") or "").strip()[:150]
    if not name:
        raise EnrolmentError("Enter your full name.")
    fields = _business_fields(data)
    existing = User.objects.filter(phone=phone).first()
    if existing is not None:
        _free_for_business(existing)
        if not existing.is_active or existing.closed_at is not None:
            raise EnrolmentError("The SokoPay account for this number is closed or suspended.")
    return {"phone": phone, "full_name": name, **fields}


def signup_finish(*, application: dict, password: str, files) -> tuple:
    """
    Create the applicant (or upgrade their customer account), set their password, create
    the merchant with documents and submit it. The caller has already verified the SMS
    code and validated the password. Returns (user, merchant).
    """
    documents.validate_all(files)
    fields = {k: application[k] for k in ("legal_name", "trading_name", "business_type", "category",
                                          "tin", "registration_no")}
    with transaction.atomic():
        user, created = _person(application["phone"], application["full_name"], "merchant")
        _free_for_business(user)
        user.set_password(password)
        user.save(update_fields=["password", "updated_at"])
        merchant = onboarding.create_merchant(owner=user, **fields)
        documents.store_all(merchant, files)
        onboarding.submit_for_review(merchant)
        audit("merchant.create", actor=user, obj=merchant, channel="self_signup", new_person=created)
    return user, merchant


# --- merchant portal: team ---------------------------------------------------------------
def can_manage_team(role: str) -> bool:
    return role in MANAGER_ROLES


def _check_manager(actor_role: str, target_role: str) -> None:
    if not can_manage_team(actor_role):
        raise EnrolmentError("Only the owner or an admin can manage the team.")
    if target_role == Role.OWNER:
        raise EnrolmentError("The owner can't be changed here. Contact SokoPay support to transfer ownership.")
    if actor_role == Role.ADMIN and target_role == Role.ADMIN:
        raise EnrolmentError("Only the owner can add, change or remove admins.")


def invite_member(*, merchant: Merchant, actor, actor_role: str, phone: str, full_name: str,
                  role: str) -> MerchantMember:
    if role not in INVITABLE_ROLES:
        raise EnrolmentError("Choose a role: admin, finance, cashier or developer.")
    _check_manager(actor_role, role)
    phone = phone_or_error(phone)
    full_name = (full_name or "").strip()[:150]
    if not full_name:
        raise EnrolmentError("Enter the person's full name.")
    with transaction.atomic():
        user, created = _person(phone, full_name, "merchant")
        if user.pk == actor.pk:
            raise EnrolmentError("You're already on the team.")
        _free_for_business(user)
        member = MerchantMember.objects.create(merchant=merchant, user=user, role=role)
        audit("merchant.member_add", actor=actor, obj=merchant, role=role, member=str(user.pk),
              new_person=created)
    return member


def change_role(*, merchant: Merchant, actor, actor_role: str, member: MerchantMember, role: str) -> None:
    if member.merchant_id != merchant.pk:
        raise EnrolmentError("That person isn't on this team.")
    if member.user_id == actor.pk:
        raise EnrolmentError("You can't change your own role.")
    if role not in INVITABLE_ROLES:
        raise EnrolmentError("Choose a role: admin, finance, cashier or developer.")
    _check_manager(actor_role, member.role)
    _check_manager(actor_role, role)
    old = member.role
    member.role = role
    member.save(update_fields=["role", "updated_at"])
    audit("merchant.member_role", actor=actor, obj=merchant, member=str(member.user_id), old=old, new=role)


def remove_member(*, merchant: Merchant, actor, actor_role: str, member: MerchantMember) -> None:
    if member.merchant_id != merchant.pk:
        raise EnrolmentError("That person isn't on this team.")
    if member.user_id == actor.pk:
        raise EnrolmentError("You can't remove yourself.")
    _check_manager(actor_role, member.role)
    with transaction.atomic():
        PortalInvite.objects.filter(user_id=member.user_id, merchant=merchant, used_at__isnull=True) \
            .update(expires_at=timezone.now())
        audit("merchant.member_remove", actor=actor, obj=merchant, member=str(member.user_id), role=member.role)
        member.delete()                     # access ends at their next request (checked every time)


# --- back office: add agent --------------------------------------------------------------
def staff_add_agent(*, actor, phone: str, full_name: str, display_name: str, location: str):
    """Register a pending agent, creating the person if they're new. Returns (agent, created)."""
    phone = phone_or_error(phone)
    full_name = (full_name or "").strip()[:150]
    with transaction.atomic():
        user = User.objects.filter(phone=phone).first()
        created = user is None
        if created:
            if not full_name:
                raise EnrolmentError("Enter the agent's full name.")
            user = User.objects.create_user(phone=phone, full_name=full_name, user_type="agent")
        else:
            if user.user_type == "staff":
                raise EnrolmentError("SokoPay staff accounts can't be agents.")
            if not user.is_active or user.closed_at is not None:
                raise EnrolmentError("The SokoPay account for this number is closed or suspended.")
            if full_name and not user.full_name:
                user.full_name = full_name
                user.save(update_fields=["full_name", "updated_at"])
        agent = agents.register_agent(user=user,
                                      display_name=(display_name or "").strip()[:150] or user.full_name or phone,
                                      location=(location or "").strip()[:150])
        audit("agent.register", actor=actor, obj=agent, channel="back_office", new_person=created)
    return agent, created
