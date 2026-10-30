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
    exit_code int, errors int, log_tail text, created_at timestamptz default now(), started_at timestamptz, finished_at timestamptz);
alter table mf_hop_task add column if not exists install text;
alter table mf_hop_task add column if not exists test_tags text;
alter table mf_hop_task add column if not exists shell text;
create table if not exists mf_preview (slot int primary key, generation int not null default 0, state text not null default 'free',
    project text, om_id text, database text, addons text, stop boolean not null default false, started_at timestamptz,
    last_activity timestamptz)"""   # Odoo 19 previews: what each odoo-preview-<slot> serves (docker/odoo-hop/preview.py)   # install: modules to install on a new database (no OpenUpgrade); test_tags: run those tests
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


def preview_claim(project: str, om_id: str, slots: int) -> int | None:
    """A preview slot for the project: its own if it has one (stopped first, the slot is prepared again), else a free one."""
    with connect(CONTROL) as c:
        for s in range(1, slots + 1):
            c.execute('insert into mf_preview (slot) values (%s) on conflict do nothing', (s,))
        mine = c.execute('select slot from mf_preview where project = %s', (project,)).fetchone()
        if mine:
            c.execute('update mf_preview set stop = true where slot = %s', (mine[0],))
            for _ in range(60):   # the slot's supervisor stops Odoo and drops the database
                if c.execute("select state from mf_preview where slot = %s", (mine[0],)).fetchone()[0] == 'free':
                    break
                time.sleep(2)
            else:   # no supervisor answering (slot down): take it anyway
                c.execute("update mf_preview set state = 'free', stop = false, project = null, database = null where slot = %s", (mine[0],))
        row = c.execute("update mf_preview set state = 'preparing', project = %s, om_id = %s, started_at = now(), stop = false "
                        "where slot = (select slot from mf_preview where state in ('free', 'failed') and slot <= %s "
                        "order by slot limit 1 for update skip locked) returning slot", (project, om_id, slots)).fetchone()
        return row[0] if row else None


def preview_serve(slot: int, database: str, addons: str | None):
    with connect(CONTROL) as c:
        c.execute("update mf_preview set generation = generation + 1, state = 'serving', database = %s, addons = %s, stop = false, "
                  'last_activity = now() where slot = %s', (database, addons, slot))


def preview_release(slot: int, project: str | None = None):
    """Stop the slot (its supervisor stops Odoo, drops the database and frees it). With project, only if it is that project's."""
    with connect(CONTROL) as c:
        c.execute('update mf_preview set stop = true where slot = %s' + (' and project = %s' if project else ''),
                  (slot, project) if project else (slot,))


def previews() -> list[dict]:
    with connect(CONTROL) as c:
        rows = c.execute('select slot, state, project, om_id, last_activity, started_at from mf_preview order by slot').fetchall()
    return [{'slot': s, 'state': st, 'project': p, 'omId': o, 'lastActivity': a.isoformat() if a else None,
             'startedAt': b.isoformat() if b else None} for s, st, p, o, a, b in rows]


def server_version() -> str:
    with connect() as c:
        return c.execute('show server_version').fetchone()[0].split()[0]


def mark_uninstall(database: str, modules: list[str]):
    """Modules the architect decided to drop: Odoo removes them (and their data) at the start of the first hop."""
    if modules:
        with connect(database) as c:
            c.execute("update ir_module_module set state = 'to remove' where name = any(%s) and state = 'installed'", (modules,))


def enqueue(version: str, database: str, extra_paths=(), install: list[str] | None = None, tests: list[str] | None = None,
            shell: str | None = None) -> int:
    """An OpenUpgrade hop on database, or (install) a plain Odoo installing those modules on database (created if missing, with demo
    data), running the tests of the modules in tests."""
    with connect(CONTROL) as c:
        return c.execute('insert into mf_hop_task (version, database, extra_paths, install, test_tags, shell) '
                         'values (%s, %s, %s, %s, %s, %s) returning id',
                         (version, database, list(extra_paths), ','.join(install) if install else None,
                          ','.join('/' + t for t in tests) if tests else None, shell)).fetchone()[0]


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


def query_all(database: str):
    """sql -> list of rows (tuples of str), or None when the statement fails (table absent in that version)."""
    import psycopg

    def run(sql):
        try:
            with connect(database) as c:
                return [tuple(str(x) if x is not None else None for x in row) for row in c.execute(sql).fetchall()]
        except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
            return None
    return run


def execute(database: str):
    def run(sql):
        with connect(database) as c:
            c.execute(sql)
    return run


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
