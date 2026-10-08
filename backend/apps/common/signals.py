"""Audit staff-role and privilege changes, wherever they are made (portal, Django admin, shell)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models.signals import m2m_changed, pre_save
from django.dispatch import receiver

from .audit import record

User = get_user_model()
PRIVILEGE_FIELDS = ("is_superuser", "is_staff", "is_active", "user_type")


@receiver(m2m_changed, sender=User.groups.through)
def role_change(sender, instance, action, pk_set, model, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear") or not isinstance(instance, User):
        return
    names = sorted(model.objects.filter(pk__in=pk_set or []).values_list("name", flat=True)) if pk_set else []
    record(f"role.{action.split('_')[1]}", obj=instance,
           summary=f"Roles {action.split('_')[1]}: {', '.join(names) or 'all'} for {instance.phone}",
           roles=",".join(names))


@receiver(pre_save, sender=User)
def privilege_change(sender, instance, **kwargs):
    if not instance.pk:
        return
    old = User.objects.filter(pk=instance.pk).values(*PRIVILEGE_FIELDS).first()
    if not old:
        return
    changed = {f: f"{old[f]} -> {getattr(instance, f)}" for f in PRIVILEGE_FIELDS if old[f] != getattr(instance, f)}
    if changed:
        record("user.privilege_change", obj=instance, summary=f"{instance.phone}: {changed}", **changed)
