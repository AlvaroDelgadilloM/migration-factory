"""Worker side of the Odoo 12 -> 19 migration: restore the uploaded dump on the isolated odoo-db, classify modules (analyze), run
the hops through the odoo-hop-<version> runners, reconcile and package the migrated dump (migrate). Called by
mf.worker.tasks.do_odoo_analyze / do_odoo_migrate inside the job context (timeouts, cancellation, live log)."""
from __future__ import annotations

import os
import shutil
import tarfile
import zipfile
from pathlib import Path

from . import chain, fixes, modules, reconcile

VERSIONS = modules.VERSIONS


class OdooJobError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def db_names(om_id) -> tuple[str, str]:
    tag = str(om_id).replace('-', '')[:12]
    return f'mf_{tag}_src', f'mf_{tag}_mig'


def extract_dump(zip_path: Path, member: str, work: Path) -> Path:
    """Only the dump member is written (stream; the name is checked, never trusted as a path)."""
    target = work / ('dump.sql' if member.lower().endswith('.sql') else 'dump.custom')
    with zipfile.ZipFile(zip_path) as z, z.open(member) as src, open(target, 'wb') as out:
        shutil.copyfileobj(src, out, 1 << 20)
    return target


def restore(ctx, run, dump: Path, database: str):
    """Plain SQL with psql (Odoo backups), custom format with pg_restore; owner/ACL statements dropped (they name roles of the
    origin server). PGPASSWORD reaches only the client process."""
    from ..config import get_settings
    s = get_settings()
    chain.drop_db(database)
    with chain.connect() as c:
        c.execute(f'create database "{database}"')
    env = {'PGPASSWORD': s.odoo_db_password}
    base = ['-h', s.odoo_db_host, '-p', str(s.odoo_db_port), '-U', s.odoo_db_user, '-d', database]
    if dump.name == 'dump.sql':
        cmd = ['psql', *base, '-q', '-v', 'ON_ERROR_STOP=0', '-f', str(dump)]
    else:
        cmd = ['pg_restore', *base, '--no-owner', '--no-acl', '--jobs', '2', str(dump)]
    res = run(cmd, ctx.workdir, ctx, name='odoo-restore', timeout=4 * 3600, env=env)
    try:
        version = chain.base_version(database)
    except Exception:  # noqa: BLE001 — not an Odoo database at all
        version = None
    if not version:
        raise OdooJobError('NOT_ODOO_DB', 'El respaldo no es una base de Odoo (no tiene ir_module_module/base); ver el log de restauración')
    return res, version


def start_version(base_version: str) -> str:
    major = base_version.split('.')[0] + '.0'
    if major not in VERSIONS[:-1]:
        raise OdooJobError('UNSUPPORTED_VERSION', f'La base es Odoo {base_version}: se migra de 12.0 (o una versión intermedia) a 19.0')
    return major


def analyze(ctx, run, dump: Path, om_id, log) -> dict:
    src, _ = db_names(om_id)
    chain.ensure_control()
    _, version = restore(ctx, run, dump, src)
    first = start_version(version)
    log(f'base Odoo {version} restaurada como {src}')
    installed = chain.installed_modules(src)
    rows = modules.classify(installed)
    before = [{'check': k, 'label': lbl, 'value': chain.query(src)(q12 if first == '12.0' else q19)}
              for k, (lbl, q12, q19) in reconcile.CHECKS.items()]
    found = fixes.detect(chain.query_all(src))
    if found:
        log('correcciones de datos sugeridas: ' + ', '.join(f"{f['title']} ({f['count']})" for f in found))
    return {'sourceDb': src, 'sourceVersion': version, 'from': first, 'to': VERSIONS[-1], 'fixes': found,
            'hops': [v for v in VERSIONS if v > first], 'counts': modules.summary(rows), 'before': before, 'rows': rows}


def oca_addons(rows: list[dict], decisions: dict, version: str, root: Path, fetch) -> list[str]:
    """Code of the installed OCA modules for one version: each repository's branch, once (fetch(url) -> bytes). Paths for the
    runner's --addons-path (the same shared volume, read-only there)."""
    repos = sorted({r['repository'] for r in rows if r.get('origin') == 'OCA' and r.get('repository')
                    and (decisions.get(r['name']) or {}).get('action') != 'uninstall'})
    paths = []
    for repo in repos:
        target = root / version / repo
        if not target.is_dir():
            data = fetch(f'https://codeload.github.com/OCA/{repo}/tar.gz/refs/heads/{version}')
            tmp = root / version / f'.{repo}.tar.gz'
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            with tarfile.open(tmp) as t:
                members = [m for m in t.getmembers() if m.isfile() or m.isdir()]  # no links, devices or absolute paths
                for m in members:
                    parts = Path(m.name).parts[1:]
                    if not parts or '..' in parts or Path(m.name).is_absolute():
                        continue
                    dest = target.joinpath(*parts)
                    if m.isdir():
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with t.extractfile(m) as f, open(dest, 'wb') as out:
                            shutil.copyfileobj(f, out)
            tmp.unlink()
        paths.append(str(target))
    return paths


def migrate(ctx, analysis: dict, decisions: dict, om_id, log, progress, fetch, cancelled, timeout: int) -> dict:
    """The hops on a copy of the analysed database. Stops at the first failed hop (its log tail is kept)."""
    from ..config import get_settings
    src = analysis['sourceDb']
    _, mig = db_names(om_id)
    chain.copy_db(src, mig)
    approved = [k.split(':', 1)[1] for k, v in decisions.items() if k.startswith('fix:') and v.get('action') == 'apply']
    applied = fixes.apply(chain.execute(mig), approved)
    if applied:
        log(f"correcciones de datos aplicadas a la copia (aprobadas): {', '.join(applied)}")
    drop = sorted(k for k, v in decisions.items() if v.get('action') == 'uninstall')
    chain.mark_uninstall(mig, drop)
    if drop:
        log(f"se desinstalan antes del primer salto (decisión del arquitecto): {', '.join(drop)}")
    hops, root = [], Path(get_settings().odoo_addons_dir)
    for i, version in enumerate(analysis['hops']):
        extra = oca_addons(analysis['rows'], decisions, version, root, fetch)
        log(f"salto a {version}" + (f" con {len(extra)} repositorio(s) OCA" if extra else ''))
        task = chain.enqueue(version, mig, extra)
        res = chain.wait(task, timeout, cancelled, on_progress=lambda row: None)
        res |= {'version': version, 'baseVersion': chain.base_version(mig) if res['state'] == 'succeeded' else None}
        hops.append({k: res[k] for k in ('version', 'state', 'exitCode', 'errors', 'baseVersion')} | {'logTail': res['logTail'][-6000:]})
        progress(i + 1, len(analysis['hops']))
        if res['state'] != 'succeeded':
            return {'state': 'failed', 'hops': hops, 'database': mig}
    rows = reconcile.run(chain.query(src), chain.query(mig))
    return {'state': 'succeeded', 'hops': hops, 'database': mig, 'reconciliation': rows, 'reconciliationStatus': reconcile.status(rows)}


def dump(ctx, run, database: str) -> Path:
    from ..config import get_settings
    s = get_settings()
    out = ctx.workdir / f'{database}.dump'
    res = run(['pg_dump', '-h', s.odoo_db_host, '-p', str(s.odoo_db_port), '-U', s.odoo_db_user, '-Fc', '--no-owner', '-f', str(out), database],
              ctx.workdir, ctx, name='odoo-dump', timeout=4 * 3600, env={'PGPASSWORD': s.odoo_db_password})
    if res.exit_code != 0 or not out.exists():
        raise OdooJobError('DUMP_FAILED', 'No se pudo generar el respaldo de la base migrada')
    return out
