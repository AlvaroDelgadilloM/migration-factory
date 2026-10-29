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
    if dump.suffix.lower() == '.sql':
        cmd = ['psql', *base, '-q', '-v', 'ON_ERROR_STOP=0', '-f', str(dump)]
    else:
        cmd = ['pg_restore', *base, '--no-owner', '--no-acl', '--jobs', '2', str(dump)]
    res = run(cmd, ctx.workdir, ctx, name='odoo-restore', timeout=4 * 3600, env=env)
    try:
        version = chain.base_version(database)
    except Exception:  # noqa: BLE001 — not an Odoo database at all
        version = None
    if not version:
        tail = res.log_path.read_text(encoding='utf-8', errors='replace')[-1500:] if res.log_path.exists() else ''
        raise OdooJobError('NOT_ODOO_DB', 'El respaldo no es una base de Odoo (no tiene ir_module_module/base). '
                                          f'Final del log de restauración:\n{tail}')
    return res, version


def start_version(base_version: str) -> str:
    major = base_version.split('.')[0] + '.0'
    if major not in VERSIONS[:-1]:
        raise OdooJobError('UNSUPPORTED_VERSION', f'La base es Odoo {base_version}: se migra de 12.0 (o una versión intermedia) a 19.0')
    return major


def analyze(ctx, run, dump: Path, om_id, log, custom: frozenset = frozenset()) -> dict:
    src, _ = db_names(om_id)
    chain.ensure_control()
    _, version = restore(ctx, run, dump, src)
    first = start_version(version)
    log(f'base Odoo {version} restaurada como {src}')
    installed = chain.installed_modules(src)
    rows = modules.classify(installed, first, custom)
    before = [{'check': k, 'label': lbl, 'value': chain.query(src)((q12 if first == '12.0' else q19).replace('{dropped}', ''))}
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


def migrate(ctx, analysis: dict, decisions: dict, om_id, log, progress, fetch, cancelled, timeout: int, custom: dict | None = None) -> dict:
    """The hops on a copy of the analysed database. Stops at the first failed hop (its log tail is kept). custom: {version: addons
    path} of the uploaded custom modules ported to each version (ported_addons): each hop updates them with that code."""
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
    hops, root, technical = [], Path(get_settings().odoo_addons_dir), []
    for i, version in enumerate(analysis['hops']):
        extra = oca_addons(analysis['rows'], decisions, version, root, fetch)
        log(f"salto a {version}" + (f" con {len(extra)} repositorio(s) OCA" if extra else '') + (' y los módulos a la medida portados'
                                                                                              if custom and version in custom else ''))
        extra += [custom[version]] if custom and version in custom else []
        for note in fixes.before_hop(version, chain.query_all(mig), chain.execute(mig)):
            log(f'corrección técnica antes del salto a {version}: {note}')
            technical.append(f'{version}: {note}')
        task = chain.enqueue(version, mig, extra)
        res = chain.wait(task, timeout, cancelled, on_progress=lambda row: None)
        base = chain.base_version(mig) if res['state'] == 'succeeded' else None
        if base is not None and not base.startswith(version):  # Odoo ended without error but the database did not move
            res['state'] = 'failed'
            res['logTail'] += f'\nLa base quedó en {base}, no en {version}.'
        res |= {'version': version, 'baseVersion': base}
        t = res['logTail']   # the runner puts the ERROR lines first: keep them and the end
        hops.append({k: res[k] for k in ('version', 'state', 'exitCode', 'errors', 'baseVersion')}
                    | {'logTail': t if len(t) <= 9000 else t[:3000] + '\n[…]\n' + t[-6000:]})
        progress(i + 1, len(analysis['hops']))
        if res['state'] != 'succeeded':
            return {'state': 'failed', 'hops': hops, 'database': mig, 'technicalFixes': technical}
    rows = reconcile.run(chain.query(src), chain.query(mig), drop, analysis['from'] == '12.0')
    return {'state': 'succeeded', 'hops': hops, 'database': mig, 'reconciliation': rows, 'reconciliationStatus': reconcile.status(rows),
            'technicalFixes': technical}


def backup(ctx, run, database: str, source_zip: Path | None = None) -> Path:
    """The migrated database as an Odoo backup ZIP (dump.sql + manifest.json + filestore/), the format Odoo's database manager
    restores. The filestore is the uploaded one, copied as is: attachments are stored by content hash and a migration does not
    rewrite them (the files Odoo regenerates, like web assets, are rebuilt on first use)."""
    import json
    import zipfile
    from ..config import get_settings
    s = get_settings()
    sql = ctx.workdir / f'{database}.sql'
    res = run(['pg_dump', '-h', s.odoo_db_host, '-p', str(s.odoo_db_port), '-U', s.odoo_db_user, '--no-owner', '-f', str(sql), database],
              ctx.workdir, ctx, name='odoo-dump', timeout=4 * 3600, env={'PGPASSWORD': s.odoo_db_password})
    if res.exit_code != 0 or not sql.exists():
        raise OdooJobError('DUMP_FAILED', 'No se pudo generar el respaldo de la base migrada')
    version = chain.base_version(database) or VERSIONS[-1]
    manifest = {'odoo_dump': '1', 'db_name': database, 'version': VERSIONS[-1], 'major_version': VERSIONS[-1],
                'version_info': [int(VERSIONS[-1].split('.')[0]), 0, 0, 'final', 0, ''], 'pg_version': chain.server_version(),
                'modules': {m['name']: m['version'] for m in chain.installed_modules(database)}, 'base_version': version}
    out = ctx.workdir / f'{database}.zip'
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        z.write(sql, 'dump.sql')
        z.writestr('manifest.json', json.dumps(manifest, indent=2))
        if source_zip is not None:
            with zipfile.ZipFile(source_zip) as src:
                for i in src.infolist():
                    if i.filename.startswith('filestore/') and not i.is_dir() and '..' not in i.filename.split('/'):
                        with src.open(i) as fin, z.open(i.filename, 'w', force_zip64=True) as fout:
                            shutil.copyfileobj(fin, fout, 1 << 20)
    sql.unlink()
    return out


def ported_addons(ctx, run, src: Path, om_id, log, start: str = '12.0') -> dict:
    """The uploaded custom modules ported version by version (mf.odoo.code.port_steps), each version's code on the shared
    read-only volume of the runners: {'paths': {version: addons path}, 'final': ported dir of 19.0, 'notes': [...]}."""
    from ..config import get_settings
    from . import code as oc
    bad = oc.safe_tree(src)
    if bad:
        raise OdooJobError('UNSAFE_NAMES', f"Nombres de archivo no admitidos (solo letras, números, _ . - /): {', '.join(bad[:5])}")
    if not oc.modules(src):
        raise OdooJobError('NO_MODULES', 'El ZIP no trae módulos de Odoo (carpetas con __manifest__.py)')
    res = oc.port_steps(src, ctx.workdir / 'ported', run, ctx, log, start=start)
    root = Path(get_settings().odoo_addons_dir) / 'custom' / str(om_id).replace('-', '')[:12]
    shutil.rmtree(root, ignore_errors=True)
    paths = {}
    for v, d in res['snapshots'].items():
        for name, m in oc.modules(d).items():
            shutil.copytree(m['path'], root / v / name)
        paths[v] = str(root / v)
    return {'paths': paths, 'final': res['snapshots'][VERSIONS[-1]], 'notes': res['notes']}


def port_code(ctx, run, src: Path, om_id, log, cancelled, timeout: int) -> dict:
    """Custom module code: inventory, automatic port to 19 (OCA odoo-module-migrator) and an install check of every ported module
    on a new Odoo 19 database (odoo-hop-19 runner, the code on the shared read-only volume). Nothing of the uploaded code runs in
    the worker: it is only read and rewritten; it runs inside the isolated runner."""
    import json
    from . import code as oc
    p = ported_addons(ctx, run, src, om_id, log)
    ported, warnings, addons = p['final'], p['notes'], p['paths']['19.0']
    rows = oc.inventory(src, ported)
    log(f"{len(rows)} módulo(s): " + ', '.join(f"{r['name']} ({r['counts']['AUTO']} automáticos, {r['counts']['MANUAL']} manuales, "
                                              f"{r['counts']['REVIEW']} a revisar)" for r in rows))
    tag = str(om_id).replace('-', '')[:12]
    names = {r['name'] for r in rows}
    core = sorted({d for r in rows for d in r['depends'] if d not in names}) or ['base']
    base = f'mf_{tag}_base'
    chain.ensure_control()
    chain.drop_db(base)
    log(f"Odoo 19 nuevo con las dependencias estándar: {', '.join(core)}")
    res = chain.wait(chain.enqueue('19.0', base, install=core), timeout, cancelled)
    installs = {'base': {'modules': core, 'state': res['state'], 'errors': res['errors'], 'logTail': res['logTail'][-6000:]}}
    for i, name in enumerate(oc.install_order(rows)):
        if res['state'] != 'succeeded' or cancelled():
            installs[name] = {'state': 'skipped', 'errors': 0, 'logTail': ''}
            continue
        db = f'mf_{tag}_i{i}'
        chain.copy_db(base, db)
        r = chain.wait(chain.enqueue('19.0', db, [addons], install=[name]), timeout, cancelled)
        chain.drop_db(db)
        installs[name] = {'state': r['state'], 'errors': r['errors'], 'logTail': r['logTail'][-6000:]}
        log(f"instalar {name} en Odoo 19: {r['state']}")
    chain.drop_db(base)
    for r in rows:
        r['install'] = installs.get(r['name'])
    return {'rows': rows, 'portWarnings': warnings, 'base': installs['base'], 'ported': ported, 'diff': oc.diff(src, ported),
            'report': json.dumps({'modules': rows, 'portWarnings': warnings}, ensure_ascii=False, indent=2)}
