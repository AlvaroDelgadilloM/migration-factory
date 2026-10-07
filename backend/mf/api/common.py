import base64

from fastapi import Query
from sqlalchemy import func, select

from ..errors import ApiError

MAX_LIMIT = 200


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        v = int(base64.urlsafe_b64decode(cursor.encode()).decode())
        if v < 0:
            raise ValueError
        return v
    except ValueError:
        raise ApiError(422, 'VALIDATION_ERROR', 'Cursor inválido') from None


def paginate(db, stmt, limit: int, cursor: str | None, mapper):
    """Opaque cursor over a stable ORDER BY. ponytail: offset-based; switch to keyset if tables grow to millions."""
    offset = decode_cursor(cursor)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = list(db.scalars(stmt.offset(offset).limit(limit)))
    nxt = offset + len(rows)
    return {'items': [mapper(r) for r in rows], 'total': total,
            'nextCursor': base64.urlsafe_b64encode(str(nxt).encode()).decode() if nxt < total else None}


def limit_param(default=50):
    return Query(default, ge=1, le=MAX_LIMIT)
