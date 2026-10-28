"""The 12.0 -> 19.0 chain on the isolated odoo-db server: control table, one task per hop for the odoo-hop-<version> runner
(docker/odoo-hop/runner.py), waiting with cancellation, and the database operations around it (copy, uninstall decisions,
module list). Only this module talks to odoo-db; it never touches the platform database."""
from __future__ import annotations

import json
import os
import re
import time
from functools import lru_cache
from pathlib import Path

CONTROL = 'mf_control'
TABLE = """create table if not exists mf_hop_task (
    id bigserial primary key, version text not null, database text not null, extra_paths text[] not null default '{}',
    state text not null default 'queued', cancel_requested boolean not null default false, runner text,
    exit_code int, errors int, log_tail text, created_at timestamptz default now(), started_at timestamptz, finished_at timestamptz)"""
NAME = re.compile(r'^[a-z][a-z0-9_]{2,62}$')


@lru_cache
def hops() -> list[dict]:
    base = Path(os.environ.get('MF_RULES_CATALOG', Path(__file__).resolve().parents[3] / 'rules' / 'catalog.json')).parent / 'odoo'
    return json.loads((base / 'hops.json').read_text(encoding='utf-8'))['hops']


def dsn(database: str = 'postgres') -> dict:
    from ..config import get_settings
    s = get_settings()
    return {'host': s.odoo_db_host, 'port': s.odoo_db_port, 'user': s.odoo_db_user, 'password': s.odoo_db_password, 'dbname': database}


def connect(database: str = 'postgres'):
    import psycopg
    return psycopg.connect(**dsn(database), autocommit=True)


def _ident(name: str) -> str:
    if not NAME.match(name):
        raise ValueError(f'nombre de base inválido: {name}')
    return f'"{name}"'


def ensure_control():
    with connect() as c:
        if not c.execute('select 1 from pg_database where datname = %s', (CONTROL,)).fetchone():
            c.execute(f'create database {_ident(CONTROL)}')
    with connect(CONTROL) as c:
        c.execute(TABLE)


def copy_db(source: str, target: str):
    with connect() as c:
        c.execute(f'drop database if exists {_ident(target)}')
        c.execute(f'create database {_ident(target)} template {_ident(source)}')


def drop_db(name: str):
    with connect() as c:
        c.execute(f'drop database if exists {_ident(name)} with (force)')


def installed_modules(database: str) -> list[dict]:
    with connect(database) as c:
        rows = c.execute("select name, coalesce(author, ''), coalesce(latest_version, '') from ir_module_module "
                         "where state in ('installed', 'to upgrade') order by name").fetchall()
    return [{'name': n, 'author': a, 'version': v} for n, a, v in rows]


def base_version(database: str) -> str | None:
    with connect(database) as c:
        row = c.execute("select latest_version from ir_module_module where name = 'base'").fetchone()
    return row[0] if row else None


def mark_uninstall(database: str, modules: list[str]):
    """Modules the architect decided to drop: Odoo removes them (and their data) at the start of the first hop."""
    if modules:
        with connect(database) as c:
            c.execute("update ir_module_module set state = 'to remove' where name = any(%s) and state = 'installed'", (modules,))


def enqueue(version: str, database: str, extra_paths=()) -> int:
    with connect(CONTROL) as c:
        return c.execute('insert into mf_hop_task (version, database, extra_paths) values (%s, %s, %s) returning id',
                         (version, database, list(extra_paths))).fetchone()[0]


def wait(task_id: int, timeout: int, cancelled=lambda: False, poll: float = 5, on_progress=lambda row: None) -> dict:
    """Until the runner finishes the task; cancelled() asks the runner to stop it. Returns the task row."""
    end = time.monotonic() + timeout
    with connect(CONTROL) as c:
        while True:
            cur = c.execute('select state, exit_code, errors, log_tail, runner from mf_hop_task where id = %s', (task_id,))
            state, code, errors, tail, runner = cur.fetchone()
            on_progress({'state': state, 'runner': runner})
            if state in ('succeeded', 'failed', 'cancelled'):
                return {'state': state, 'exitCode': code, 'errors': errors, 'logTail': tail or ''}
            if cancelled() or time.monotonic() > end:
                c.execute('update mf_hop_task set cancel_requested = true where id = %s', (task_id,))
                if state == 'queued':  # nobody took it: no runner for this version is up
                    c.execute("update mf_hop_task set state = 'cancelled', finished_at = now() where id = %s", (task_id,))
                    return {'state': 'cancelled' if cancelled() else 'timed_out', 'exitCode': None, 'errors': None,
                            'logTail': 'Ningún ejecutor odoo-hop tomó el salto: ¿está levantado el servicio de esa versión?'}
            time.sleep(poll)


def query(database: str):
    """sql -> first row as a tuple, or None when the statement fails (table absent in that version)."""
    def run(sql):
        try:
            with connect(database) as c:
                row = c.execute(sql).fetchone()
                return tuple(str(x) for x in row) if row else None
        except Exception:  # noqa: BLE001 — a missing table means the check does not apply to this database
            return None
    return run
