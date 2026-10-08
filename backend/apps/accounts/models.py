"""
Custom user model.

Defined from day one — even though it is minimal now — because Django makes it very
painful to switch AUTH_USER_MODEL after the first migration. We identify users by
phone number (the primary identifier in the Ghanaian market), not username.

Roles here cover STAFF and MERCHANT users of the web portals and the mobile apps.
Fine-grained merchant team roles (Owner/Admin/Finance/Cashier/Developer) and staff
roles (Operations/Compliance/Finance/Support) are added with those apps; this model
carries the top-level user_type plus the flags Django's admin needs.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models

from apps.common.models import TimeStampedModel

# E.164 phone format, e.g. +233244058519. Stored canonically; displayed locally.
PHONE_VALIDATOR = RegexValidator(
    regex=r"^\+233\d{9}$",
    message="Phone must be a Ghana number in E.164 format, e.g. +233244058519.",
)


class UserType(models.TextChoices):
    CONSUMER = "consumer", "Consumer"
    MERCHANT = "merchant", "Merchant user"
    AGENT = "agent", "Agent"          # DEMI phase
    STAFF = "staff", "SokoPay staff"


class UserManager(BaseUserManager):
    """Creates users identified by phone number."""

    use_in_migrations = True

    def _create(self, phone: str, password: str | None, **extra):
        if not phone:
            raise ValueError("A phone number is required.")
        user = self.model(phone=phone, **extra)
        # set_password hashes with Argon2 (see PASSWORD_HASHERS). Consumers who
        # authenticate by OTP + PIN instead of a password get an unusable password.
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, phone: str, password: str | None = None, **extra):
        extra.setdefault("user_type", UserType.CONSUMER)
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(phone, password, **extra)

    def create_superuser(self, phone: str, password: str, **extra):
        extra.update(user_type=UserType.STAFF, is_staff=True, is_superuser=True)
        return self._create(phone, password, **extra)


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    phone = models.CharField(
        max_length=16, unique=True, validators=[PHONE_VALIDATOR],
        help_text="Canonical E.164, e.g. +233244058519",
    )
    email = models.EmailField(blank=True)
    full_name = models.CharField(max_length=150, blank=True)
    user_type = models.CharField(max_length=16, choices=UserType.choices, default=UserType.CONSUMER)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # may access the Django/admin site

    # Consumers approve payments in-app with a 6-digit PIN (distinct from their MoMo
    # PIN). Stored as an Argon2 hash, never in the clear.
    pin_hash = models.CharField(max_length=255, blank=True)

    # Account-safety fields. Lockout is enforced by the auth flow, persisted here
    # so it survives restarts (the old repo kept this only loosely).
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    # "Sign out everywhere": every JWT carries the generation it was issued under;
    # bumping this number refuses all of them at once. Bumped on PIN change, suspected
    # compromise, or when the user asks. (A counter, not a timestamp, so there is no
    # same-second ambiguity between the tokens being killed and the ones replacing them.)
    token_generation = models.PositiveIntegerField(default=0)

    # Forgot-PIN reset time: sends are capped for 24 h afterwards (SIM-swap protection).
    pin_reset_at = models.DateTimeField(null=True, blank=True)

    # Account closure. Records are kept for the legal retention period (AML Act), then
    # anonymised by accounts.tasks.anonymise_closed_accounts.
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_reason = models.CharField(max_length=255, blank=True)
    anonymised_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []  # phone + password is enough for createsuperuser

    class Meta:
        db_table = "accounts_user"
        indexes = [models.Index(fields=["user_type"])]

    def __str__(self) -> str:
        return f"{self.full_name or self.phone} ({self.get_user_type_display()})"

    # --- PIN (hashed with Argon2 via Django's password hashers) -------------
    def set_pin(self, pin: str) -> None:
        from django.contrib.auth.hashers import make_password
        self.pin_hash = make_password(pin)

    def check_pin(self, pin: str) -> bool:
        from django.contrib.auth.hashers import check_password
        return bool(self.pin_hash) and check_password(pin, self.pin_hash)

    @property
    def has_pin(self) -> bool:
        return bool(self.pin_hash)


class RevokedToken(models.Model):
    """
    A JWT (by its `jti`) that must no longer be accepted, even though its signature
    and expiry are fine: the user signed out, or a refresh token was rotated. Rows are
    purged once the token would have expired anyway (apps.accounts.tasks).
    """

    jti = models.CharField(max_length=32, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="revoked_tokens")
    token_type = models.CharField(max_length=8)          # access | refresh
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_revoked_token"

    def __str__(self) -> str:
        return f"{self.token_type} {self.jti[:8]}… revoked"
