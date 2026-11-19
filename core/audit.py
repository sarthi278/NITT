import json

from core.audit_context import get_current_user
from core.models import AuditLog


def record_business_event(
    *,
    action,
    entity_type,
    entity_id,
    user=None,
    source_doc_type="",
    source_doc_id=None,
    details=None,
):
    AuditLog.objects.create(
        user=user or get_current_user(),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
        new_value=json.dumps(details, default=str) if details is not None else "",
    )
