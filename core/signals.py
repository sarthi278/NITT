import json

from django.apps import apps
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


def source_document(instance):
    if hasattr(instance, "source_doc_type") and hasattr(instance, "source_doc_id"):
        return instance.source_doc_type, instance.source_doc_id
    if instance.__class__.__name__ == "Order":
        return "ORDER", instance.pk
    if hasattr(instance, "order_id"):
        return "ORDER", instance.order_id
    if hasattr(instance, "invoice_id"):
        return "INVOICE", instance.invoice_id
    source_types = {
        "StockInward": "STOCK_INWARD",
        "Purchase": "PURCHASE",
        "ProductionRun": "PRODUCTION_RUN",
    }
    if instance.__class__.__name__ in source_types:
        return source_types[instance.__class__.__name__], instance.pk
    return "", None


def audit_actor(instance):
    user = get_current_user()
    if user:
        return user
    for field_name in (
        "user",
        "created_by",
        "posted_by",
        "approved_by",
        "received_by",
        "dispatched_by",
    ):
        user = getattr(instance, field_name, None)
        if user:
            return user
    return None


def should_track(sender):
    return (
        sender._meta.apps is apps
        and sender._meta.app_label == "core"
        and sender._meta.label_lower != AuditLog._meta.label_lower
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

    user = audit_actor(instance)
    current_values = get_model_fields(instance)
    source_doc_type, source_doc_id = source_document(instance)

    if created:
        AuditLog.objects.create(
            user=user,
            action="CREATE",
            entity_type=sender.__name__,
            entity_id=instance.pk,
            new_value=serialize_values(current_values),
            source_doc_type=source_doc_type,
            source_doc_id=source_doc_id,
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
                source_doc_type=source_doc_type,
                source_doc_id=source_doc_id,
            )


@receiver(pre_delete)
def log_delete(sender, instance, **kwargs):
    if not should_track(sender):
        return

    source_doc_type, source_doc_id = source_document(instance)
    AuditLog.objects.create(
        user=audit_actor(instance),
        action="DELETE",
        entity_type=sender.__name__,
        entity_id=instance.pk,
        old_value=serialize_values(get_model_fields(instance)),
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
    )