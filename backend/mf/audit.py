import hashlib
import json

from .models import AuditEvent


def digest(obj) -> str | None:
    if obj is None:
        return None
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def record(db, *, actor, action, entity_type, entity_id, project_id=None, before=None, after=None, details=None, request=None):
    db.add(AuditEvent(actor=actor, action=action, entity_type=entity_type, entity_id=str(entity_id), project_id=project_id,
                      before_hash=digest(before), after_hash=digest(after), details=details or {},
                      request_id=getattr(getattr(request, 'state', None), 'request_id', None)))
