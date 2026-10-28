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


def command(database, extra_paths):
    """OpenUpgrade for this version: 13.0 is a full fork of Odoo; 14.0+ is the official Odoo with openupgrade_framework and the
    migration scripts of OCA/OpenUpgrade (--upgrade-path)."""
    common = ['-d', database, '--db_host', DB['host'], '--db_port', str(DB['port']), '--db_user', DB['user'],
              '--db_password', DB['password'], '--update', 'all', '--stop-after-init', '--without-demo', 'all',
              '--data-dir', '/var/lib/odoo', '--log-level', 'info', '--workers', '0', '--max-cron-threads', '0']
    if VERSION == '13.0':
        paths = [OU + '/addons', OU + '/odoo/addons'] + list(extra_paths)
        return ['python3', OU + '/odoo-bin', '--addons-path', ','.join(paths)] + common
    import odoo  # the image's own Odoo
    core = os.path.join(os.path.dirname(odoo.__file__), 'addons')
    paths = [OU, core] + list(extra_paths)
    return ['odoo', '--addons-path', ','.join(paths), '--upgrade-path', OU + '/openupgrade_scripts/scripts',
            '--load', 'base,web,openupgrade_framework'] + common


def connect():
    return psycopg2.connect(dbname='mf_control', **DB)


def take(conn):
    with conn, conn.cursor() as cur:
        cur.execute("""update mf_hop_task set state = 'running', started_at = now(), runner = %s
                       where id = (select id from mf_hop_task where version = %s and state = 'queued' order by id
                                   for update skip locked limit 1)
                       returning id, database, extra_paths""", (os.uname()[1], VERSION))
        return cur.fetchone()


def cancelled(conn, task_id):
    with conn, conn.cursor() as cur:
        cur.execute('select cancel_requested from mf_hop_task where id = %s', (task_id,))
        row = cur.fetchone()
        return bool(row and row[0])


def run(conn, task_id, database, extra_paths):
    log_path = os.path.join(LOGS, 'hop-%s.log' % task_id)
    with open(log_path, 'wb') as out:
        proc = subprocess.Popen(command(database, extra_paths or []), stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        while proc.poll() is None:
            time.sleep(3)
            if cancelled(conn, task_id):
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                break
    with open(log_path, 'rb') as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - TAIL))
        tail = f.read().decode('utf-8', 'replace')
    errors = sum(1 for line in tail.splitlines() if ' ERROR ' in line or ' CRITICAL ' in line)
    state = 'cancelled' if cancelled(conn, task_id) else 'succeeded' if proc.returncode == 0 and errors == 0 else 'failed'
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
