"""Odoo 19 preview slot: one container per slot (odoo-preview-1, odoo-preview-2), on the odoo-hop-19 image. It serves the database
and the ported addons the platform assigns to its slot in mf_preview (database mf_control on the isolated odoo-db), so a team can
use the migrated project in a browser. Crons off, no database manager, outgoing mail to a closed port. After MF_PREVIEW_IDLE
seconds with no request it stops Odoo, drops the preview database and frees the slot.

Python 3 stdlib + psycopg2 (both in the Odoo image)."""
import os
import re
import signal
import subprocess
import time
import urllib.request

import psycopg2

SLOT = int(os.environ['MF_PREVIEW_SLOT'])
IDLE = int(os.environ.get('MF_PREVIEW_IDLE', str(4 * 3600)))
DB = {'host': os.environ.get('MF_ODOO_DB_HOST', 'odoo-db'), 'user': os.environ.get('MF_ODOO_DB_USER', 'odoo'),
      'password': os.environ['MF_ODOO_DB_PASSWORD'], 'port': int(os.environ.get('MF_ODOO_DB_PORT', '5432'))}
LOG = '/hop-logs/preview.log'


def core_addons():
    import odoo
    return os.path.join(list(odoo.__path__)[0], 'addons')


def command(database, addons):
    return ['odoo', '--addons-path', ','.join([core_addons()] + [a for a in [addons] if a]), '-d', database,
            '--db-filter', '^%s$' % database, '--no-database-list', '--http-port', '8069', '--proxy-mode',
            '--db_host', DB['host'], '--db_port', str(DB['port']), '--db_user', DB['user'], '--db_password', DB['password'],
            '--data-dir', '/var/lib/odoo', '--workers', '0', '--max-cron-threads', '0',
            '--smtp', '127.0.0.1', '--smtp-port', '9', '--log-level', 'info']


def warm_up():
    """The first request makes Odoo build its asset bundles (several MB): done here, before people get the link, so their first
    page is complete. True when the login page and its assets answered."""
    try:
        html = urllib.request.urlopen('http://127.0.0.1:8069/web/login', timeout=60).read().decode('utf-8', 'replace')
        for url in re.findall(r'(?:href|src)="(/web/assets/[^"]+)"', html):
            urllib.request.urlopen('http://127.0.0.1:8069' + url, timeout=300).read()
        return 'name="login"' in html
    except Exception:  # noqa: BLE001 — not up yet
        return False


def connect(dbname='mf_control'):
    c = psycopg2.connect(dbname=dbname, **DB)
    c.autocommit = True
    return c


def row(conn):
    with conn.cursor() as cur:
        cur.execute('select generation, state, database, addons, stop from mf_preview where slot = %s', (SLOT,))
        return cur.fetchone()


def set_state(conn, state, touch=False, **cols):
    sets = ', '.join(['state = %s'] + ['%s = %%s' % k for k in cols] + (['last_activity = now()'] if touch else []))
    with conn.cursor() as cur:
        cur.execute('update mf_preview set %s where slot = %%s' % sets, [state] + list(cols.values()) + [SLOT])


def drop(database):
    # not `with connect(...)`: since psycopg2 2.9 that opens a transaction even in autocommit, and DROP DATABASE cannot run in one
    c = connect('postgres')
    try:
        with c.cursor() as cur:
            cur.execute('drop database if exists "%s" with (force)' % database.replace('"', ''))
    finally:
        c.close()


def main():
    proc, serving, last_size, last_activity, warming = None, None, 0, time.time(), False
    while True:
        try:
            conn = connect()
            while True:
                r = row(conn)
                gen, state, database, addons, stop = r if r else (None, 'free', None, None, False)
                if proc and (stop or state not in ('serving', 'starting') or gen != serving):   # released, stopped or reassigned
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    proc, serving = None, None
                if stop:   # stop: the database goes away and the slot is free again
                    if database:
                        drop(database)
                    set_state(conn, 'free', stop=False, database=None, addons=None, project=None)
                    continue
                if not proc and state in ('serving', 'starting') and not stop:
                    out = open(LOG, 'ab')
                    proc = subprocess.Popen(command(database, addons), stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
                    serving, last_activity, last_size = gen, time.time(), os.path.getsize(LOG)
                    set_state(conn, 'starting', touch=True)   # 'serving' once warmed up (below)
                    warming = True
                if proc and warming and proc.poll() is None and warm_up():
                    warming = False
                    set_state(conn, 'serving', touch=True)
                if proc:
                    size = os.path.getsize(LOG)   # crons are off: a new log line is a request
                    if size != last_size:
                        last_size, last_activity = size, time.time()
                        with conn.cursor() as cur:
                            cur.execute('update mf_preview set last_activity = now() where slot = %s', (SLOT,))
                    if proc.poll() is not None:   # Odoo exited on its own: record it, keep the slot for the team to see
                        set_state(conn, 'failed')
                        proc, serving = None, None
                    elif time.time() - last_activity > IDLE:
                        with conn.cursor() as cur:
                            cur.execute('update mf_preview set stop = true where slot = %s', (SLOT,))
                time.sleep(3)
        except psycopg2.Error as e:   # odoo-db not ready, or the control table not created yet (the worker creates it)
            print('preview slot %s: %s' % (SLOT, str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__), flush=True)
            time.sleep(5)


if __name__ == '__main__':
    main()
