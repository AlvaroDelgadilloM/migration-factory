"""Odoo hop runner: one container per target version (odoo-hop-13 ... odoo-hop-19), built on the official Odoo image of that
version plus OpenUpgrade at a pinned commit. It takes upgrade tasks for its version from the control table mf_hop_task (database
mf_control on the isolated odoo-db server), runs OpenUpgrade on the given database and records the outcome and the log.

No internet, no access to the platform database: only odoo-db. Python 3.7+ and psycopg2 (both in the Odoo images); stdlib only."""
import os
import signal
import subprocess
import time

import psycopg2

VERSION = os.environ['MF_ODOO_VERSION']                     # e.g. "14.0"
LOGS = os.environ.get('MF_HOP_LOGS', '/hop-logs')
DB = {'host': os.environ.get('MF_ODOO_DB_HOST', 'odoo-db'), 'user': os.environ.get('MF_ODOO_DB_USER', 'odoo'),
      'password': os.environ['MF_ODOO_DB_PASSWORD'], 'port': int(os.environ.get('MF_ODOO_DB_PORT', '5432'))}
OU = '/opt/openupgrade'
TAIL = 20000
# not 'Traceback': Odoo logs some at INFO (an attachment file missing from the filestore) and goes on
FATAL = (' CRITICAL ', 'Failed to load registry')


def command(database, extra_paths, install=None):
    """OpenUpgrade for this version: 13.0 is a full fork of Odoo; 14.0+ is the official Odoo with openupgrade_framework and the
    migration scripts of OCA/OpenUpgrade (--upgrade-path). install: the plain Odoo of the image installing those modules on a
    new database (checks that ported module code installs on this version)."""
    common = ['-d', database, '--db_host', DB['host'], '--db_port', str(DB['port']), '--db_user', DB['user'],
              '--db_password', DB['password'], '--stop-after-init', '--without-demo', 'all',
              '--data-dir', '/var/lib/odoo', '--log-level', 'info', '--workers', '0', '--max-cron-threads', '0']
    if install:
        core = core_addons()
        return ['odoo', '--addons-path', ','.join([core] + list(extra_paths)), '--init', install] + common
    common += ['--update', 'all']
    if VERSION == '13.0':
        paths = [OU + '/addons', OU + '/odoo/addons'] + list(extra_paths)
        return ['python3', OU + '/odoo-bin', '--addons-path', ','.join(paths)] + common
    paths = [OU, core_addons()] + list(extra_paths)
    return ['odoo', '--addons-path', ','.join(paths), '--upgrade-path', OU + '/openupgrade_scripts/scripts',
            '--load', 'base,web,openupgrade_framework'] + common


def core_addons():
    """The image's own Odoo addons. odoo.__path__, not __file__: since 19.0 odoo is a namespace package (no __init__.py)."""
    import odoo
    return os.path.join(list(odoo.__path__)[0], 'addons')


def connect():
    return psycopg2.connect(dbname='mf_control', **DB)


def take(conn):
    with conn, conn.cursor() as cur:
        cur.execute("""update mf_hop_task set state = 'running', started_at = now(), runner = %s
                       where id = (select id from mf_hop_task where version = %s and state = 'queued' order by id
                                   for update skip locked limit 1)
                       returning id, database, extra_paths, install""", (os.uname()[1], VERSION))
        return cur.fetchone()


def cancelled(conn, task_id):
    with conn, conn.cursor() as cur:
        cur.execute('select cancel_requested from mf_hop_task where id = %s', (task_id,))
        row = cur.fetchone()
        return bool(row and row[0])


def run(conn, task_id, database, extra_paths, install=None):
    """Any failure of the runner itself ends the task as failed with its traceback (it never stays 'running')."""
    try:
        execute(conn, task_id, database, extra_paths, install)
    except Exception:  # noqa: BLE001
        import traceback
        with conn, conn.cursor() as cur:
            cur.execute("update mf_hop_task set state = 'failed', errors = 1, log_tail = %s, finished_at = now() where id = %s",
                        ('El ejecutor del salto falló antes de terminar:\n' + traceback.format_exc()[-TAIL:], task_id))


def execute(conn, task_id, database, extra_paths, install=None):
    log_path = os.path.join(LOGS, 'hop-%s.log' % task_id)
    with open(log_path, 'wb') as out:
        proc = subprocess.Popen(command(database, extra_paths or [], install), stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        while proc.poll() is None:
            time.sleep(3)
            if cancelled(conn, task_id):
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                break
    errors, fatal, error_lines = 0, False, []
    with open(log_path, 'rb') as f:   # the whole log: an error early in a long hop is not in the tail
        for raw in f:
            line = raw.decode('utf-8', 'replace')
            if ' ERROR ' in line or ' CRITICAL ' in line:
                errors += 1
                if len(error_lines) < 30:
                    error_lines.append(line.rstrip()[:500])
            if any(m in line for m in FATAL):
                fatal = True
        f.seek(0, 2)
        f.seek(max(0, f.tell() - TAIL))
        tail = f.read().decode('utf-8', 'replace')
    if error_lines and error_lines[0] not in tail:
        tail = 'Líneas ERROR del log (%d):\n%s\n...\n%s' % (errors, '\n'.join(error_lines), tail)
    # Odoo logs some recoverable problems as ERROR and goes on (OpenUpgrade keeping an obsolete record still referenced, a module
    # left uninstalled): those are counted and shown; the hop fails on a non-zero exit, a CRITICAL line or a registry that did not load
    state = 'cancelled' if cancelled(conn, task_id) else 'succeeded' if proc.returncode == 0 and not fatal else 'failed'
    with conn, conn.cursor() as cur:
        cur.execute("""update mf_hop_task set state = %s, exit_code = %s, errors = %s, log_tail = %s, finished_at = now()
                       where id = %s""", (state, proc.returncode, errors, tail, task_id))


def main():
    while True:
        try:
            conn = connect()
            while True:
                task = take(conn)
                if task:
                    run(conn, *task)
                else:
                    time.sleep(3)
        except psycopg2.Error:
            time.sleep(5)  # odoo-db not ready yet, or the control table not created yet (the worker creates it)


if __name__ == '__main__':
    main()
