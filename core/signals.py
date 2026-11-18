import json

from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

from core.audit_context import get_current_user
from core.models import AuditLog


def get_model_fields(instance):
    return {
        field.name: field.value_from_object(instance)
        for field in instance._meta.concrete_fields
        if not field.primary_key
    }


def serialize_values(values):
    return json.dumps(values, default=str)


def should_track(sender):
    return (
        sender._meta.app_label == "core"
        and sender is not AuditLog
        and not sender._meta.proxy
    )


@receiver(pre_save)
def capture_old_values(sender, instance, raw=False, **kwargs):
    if raw or not should_track(sender):
        return

    if instance.pk:
        instance._audit_old_values = (
            sender._base_manager.filter(pk=instance.pk)
            .first()
        )

        if instance._audit_old_values:
            instance._audit_old_values = get_model_fields(
                instance._audit_old_values
            )


@receiver(post_save)
def log_save(sender, instance, created, raw=False, update_fields=None, **kwargs):
    if raw or not should_track(sender):
        return

    user = get_current_user()
    current_values = get_model_fields(instance)

    if created:
        AuditLog.objects.create(
            user=user,
            action="CREATE",
            entity_type=sender.__name__,
            entity_id=instance.pk,
            new_value=serialize_values(current_values),
        )
        return

    old_values = getattr(instance, "_audit_old_values", None)

    if old_values is None:
        return

    for field_name, new_value in current_values.items():
        if update_fields is not None:
            field = sender._meta.get_field(field_name)
            if field.name not in update_fields and field.attname not in update_fields:
                continue

        old_value = old_values.get(field_name)

        if old_value != new_value:
            AuditLog.objects.create(
                user=user,
                action="UPDATE",
                entity_type=sender.__name__,
                entity_id=instance.pk,
                field_name=field_name,
                old_value=str(old_value),
                new_value=str(new_value),
            )


@receiver(pre_delete)
def log_delete(sender, instance, **kwargs):
    if not should_track(sender):
        return

    AuditLog.objects.create(
        user=get_current_user(),
        action="DELETE",
        entity_type=sender.__name__,
        entity_id=instance.pk,
        old_value=serialize_values(get_model_fields(instance)),
    )