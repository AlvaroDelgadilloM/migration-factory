from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_engine = None
_factory = None


def engine():
    global _engine, _factory
    if _engine is None:
        url = get_settings().database_url
        kwargs = {'pool_pre_ping': True}
        if url.startswith('sqlite'):
            kwargs['connect_args'] = {'check_same_thread': False}
        _engine = create_engine(url, **kwargs)
        if url.startswith('sqlite'):
            def _pragmas(conn, _):  # WAL: readers do not block the job-event writer (tests / local only)
                for p in ('PRAGMA foreign_keys=ON', 'PRAGMA journal_mode=WAL', 'PRAGMA busy_timeout=10000'):
                    conn.execute(p)
            event.listen(_engine, 'connect', _pragmas)
        _factory = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def reset_engine():
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = _factory = None


def get_db():
    """FastAPI dependency."""
    engine()
    with _factory() as s:
        yield s


@contextmanager
def session() -> Session:
    engine()
    with _factory() as s:
        yield s
