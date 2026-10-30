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


def ported_addons(ctx, run, src: Path, om_id, log, start: str = '12.0', ext=None) -> dict:
    """The uploaded custom modules ported version by version (mf.odoo.code.port_steps), each version's code on the shared
    read-only volume of the runners: {'paths': {version: addons path}, 'final': ported dir of 19.0, 'notes': [...]}."""
    from ..config import get_settings
    from . import code as oc
    bad = oc.safe_tree(src)
    if bad:
        raise OdooJobError('UNSAFE_NAMES', f"Nombres de archivo no admitidos (solo letras, números, _ . - /): {', '.join(bad[:5])}")
    if not oc.modules(src):
        raise OdooJobError('NO_MODULES', 'El ZIP no trae módulos de Odoo (carpetas con __manifest__.py)')
    res = oc.port_steps(src, ctx.workdir / 'ported', run, ctx, log, start=start, ext=ext)
    root = Path(get_settings().odoo_addons_dir) / 'custom' / str(om_id).replace('-', '')[:12]
    shutil.rmtree(root, ignore_errors=True)
    paths = {}
    for v, d in res['snapshots'].items():
        for name, m in oc.modules(d).items():
            shutil.copytree(m['path'], root / v / name)
        paths[v] = str(root / v)
    return {'paths': paths, 'final': res['snapshots'][VERSIONS[-1]], 'notes': res['notes']}


def external_sql(src: Path, sql_files, database: str | None, log) -> list[dict]:
    """The project's external SQL (mf.odoo.extsql): objects classified against the modules' code and, with database (a new Odoo 19
    with the standard dependencies), each statement run there one by one to see what Odoo 19 accepts."""
    from . import extsql
    texts = {}
    for f in sql_files or []:
        p = (src / f).resolve()
        if p.is_file() and p.is_relative_to(src.resolve()):
            texts[f] = p.read_text(encoding='utf-8', errors='replace')
    if not texts:
        return []
    objs = extsql.classify(extsql.parse(texts), extsql.module_code(src))
    if database:
        at = {(o['file'], o['line']): o for o in objs}
        at |= {tuple(a.rsplit(':', 1)[:1]) + (int(a.rsplit(':', 1)[1]),): o for o in objs for a in o.get('alsoIn') or []}
        failed = 0
        for f, text in texts.items():
            for line, st in extsql.statements(text):
                if st.lower().rstrip(';').strip() in ('begin', 'commit', 'end', 'rollback') or extsql.SKIP.match(st):
                    continue
                o = at.get((f, line))
                try:
                    chain.execute(database)(st)
                    if o:
                        o['in19'] = {'state': 'ok'}
                except Exception as e:  # noqa: BLE001 — the evidence is the error itself
                    msg = str(e).splitlines()[0][:300]
                    if 'already exists' in msg and o and (o.get('in19') or {}).get('state') == 'ok':
                        continue   # its other definition (another file) already created it
                    failed += 1
                    target = o or next((x for x in objs if x['kind'] == 'table' and f"{x['schema']}.{x['name']}" in st.lower()[:300]), None)
                    if target:
                        target['in19'] = {'state': 'error', 'error': msg, 'line': line}
        log(f"SQL externo en Odoo 19: {len(objs)} objeto(s), {failed} sentencia(s) con error")
    for o in objs:
        o.pop('body', None)
    return objs


def port_code(ctx, run, src: Path, om_id, log, cancelled, timeout: int, sql_files=()) -> dict:
    """Custom module code: inventory, automatic port to 19 (OCA odoo-module-migrator) and an install check of every ported module
    on a new Odoo 19 database (odoo-hop-19 runner, the code on the shared read-only volume). Nothing of the uploaded code runs in
    the worker: it is only read and rewritten; it runs inside the isolated runner."""
    import json
    from . import code as oc
    p = ported_addons(ctx, run, src, om_id, log, ext=external_sql(src, sql_files, None, lambda m: None))
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
    # the external SQL goes into the base before the modules (they read its tables); what Odoo 19 rejects is recorded per object
    ext = external_sql(src, sql_files, base if res['state'] == 'succeeded' else None, log)
    # the check runs on a copy of the ported code with a generated functional test per module (not in the code you download)
    check = Path(addons).parent / 'check19'
    shutil.rmtree(check, ignore_errors=True)
    for name, m in oc.modules(Path(addons)).items():
        shutil.copytree(m['path'], check / name)
        oc.add_smoke_test(check / name, name)
    for i, name in enumerate(oc.install_order(rows)):
        if res['state'] != 'succeeded' or cancelled():
            installs[name] = {'state': 'skipped', 'errors': 0, 'logTail': ''}
            continue
        db = f'mf_{tag}_i{i}'
        chain.copy_db(base, db)
        r = chain.wait(chain.enqueue('19.0', db, [str(check)], install=[name], tests=[name]), timeout, cancelled)
        if r['state'] == 'succeeded' and missing_modules(db, [name]):   # not found: Odoo only warns
            r = r | {'state': 'failed', 'logTail': f'{name} no quedó instalado (Odoo no lo encontró o no es instalable)\n' + r['logTail']}
        chain.drop_db(db)
        tests = oc.test_results(r['logTail'])
        installs[name] = {'state': r['state'], 'errors': r['errors'], 'logTail': r['logTail'][-6000:], 'tests': tests}
        log(f"instalar {name} en Odoo 19: {r['state']}" + (f" · pruebas: {tests['total'] - tests['failed'] - tests['errors']}/{tests['total']} bien"
                                                           if tests else ''))
    chain.drop_db(base)
    for r in rows:
        r['install'] = installs.get(r['name'])
    return {'rows': rows, 'portWarnings': warnings, 'base': installs['base'], 'ported': ported, 'diff': oc.diff(src, ported),
            'externalSql': ext, 'addons19': addons,
            'report': json.dumps({'modules': rows, 'portWarnings': warnings, 'externalSql': ext}, ensure_ascii=False, indent=2)}


def missing_modules(database: str, names: list[str]) -> list[str]:
    """Odoo asked to install a module it cannot find only warns and ends well: check they are installed."""
    installed = {m['name'] for m in chain.installed_modules(database)}
    return sorted(set(names) - installed)


def run_flows(ctx, src: Path, addons19: str, rows: list[dict], sql_files, content: str, om_id, log, cancelled, timeout: int) -> dict:
    """The project's business flows (mf.odoo.flows) on a new Odoo 19 with demo data: the standard dependencies, the project's
    external SQL, the ported modules (the code port's own copy), then the script with odoo shell. The database is dropped after."""
    from . import flows
    names = sorted(r['name'] for r in rows)
    core = sorted({d for r in rows for d in r['depends'] if d not in names}) or ['base']
    db = f"mf_{str(om_id).replace('-', '')[:12]}_flows"
    chain.ensure_control()
    chain.drop_db(db)
    out = {'database': db, 'checks': None}
    try:
        log(f"Odoo 19 nuevo con datos demo: {', '.join(core)}")
        r = chain.wait(chain.enqueue('19.0', db, install=core), timeout, cancelled)
        if r['state'] != 'succeeded':
            return out | {'stage': 'base', 'state': r['state'], 'logTail': r['logTail'][-6000:]}
        external_sql(src, sql_files, db, log)
        log(f"instalar {len(names)} módulo(s) portados")
        r = chain.wait(chain.enqueue('19.0', db, [addons19], install=names), timeout, cancelled)
        missing = missing_modules(db, names) if r['state'] == 'succeeded' else names
        if missing:
            return out | {'stage': 'install', 'state': 'failed',
                          'logTail': f"No quedaron instalados: {', '.join(missing)}\n" + r['logTail'][-6000:]}
        log('flujos de negocio con odoo shell')
        r = chain.wait(chain.enqueue('19.0', db, [addons19], shell=flows.script(content, names)), timeout, cancelled)
        checks = flows.results(r['logTail'])
        state = 'succeeded' if checks is not None and all(c['ok'] for c in checks) else 'failed'
        if checks is not None:
            log(f"{sum(c['ok'] for c in checks)}/{len(checks)} flujos bien")
        return out | {'stage': 'flows', 'state': state, 'checks': checks,
                      'logTail': '' if checks is not None else r['logTail'][-6000:]}
    finally:
        chain.drop_db(db)


def preview(ctx, run, project_id, om_id, src: Path | None, *, backup: Path | None, addons19: str | None, rows: list[dict],
            sql_files, base_url: str, log, cancelled, timeout: int) -> dict:
    """An Odoo 19 people can use (pantalla Pruebas → «Levantar Odoo 19»), in one of the preview slots (mf_preview, served by
    odoo-preview-<slot>). With backup (the migration's Odoo 19 backup ZIP) the migrated database itself; without, a new database
    with demo data, the project's external SQL and its ported modules. The admin user gets a new password; outgoing mail servers
    are turned off. The slot is released by the team, or by its supervisor after the idle time."""
    import secrets
    import zipfile
    from ..config import get_settings
    s = get_settings()
    ports = [p.strip() for p in s.odoo_preview_ports.split(',') if p.strip()]
    chain.ensure_control()
    slot = chain.preview_claim(str(project_id), str(om_id), len(ports))
    if slot is None:
        busy = ', '.join(f"{p['slot']}: {p['state']}" for p in chain.previews())
        raise OdooJobError('NO_PREVIEW_SLOT', f'No hay espacios libres para levantar Odoo 19 ({busy}); apague uno en la pantalla Pruebas')
    db = f'mf_preview_{slot}'
    try:
        # the slot serves its own copy of the code, so a new port of the project does not change what is running
        addons = None
        if addons19 and Path(addons19).is_dir():
            dest = Path(s.odoo_addons_dir) / 'preview' / str(slot)
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(addons19, dest)
            addons = str(dest)
        if backup:
            log('base migrada a Odoo 19 (respaldo de la migración)')
            with zipfile.ZipFile(backup) as z, z.open('dump.sql') as fin, open(ctx.workdir / 'dump.sql', 'wb') as fout:
                shutil.copyfileobj(fin, fout, 1 << 20)
            restore(ctx, run, ctx.workdir / 'dump.sql', db)
        else:
            names = sorted(r['name'] for r in rows)
            core = sorted({d for r in rows for d in r['depends'] if d not in names}) or ['base']
            log(f"Odoo 19 nuevo con datos demo, SQL externo y {len(names)} módulo(s) portados")
            chain.drop_db(db)
            r = chain.wait(chain.enqueue('19.0', db, install=core), timeout, cancelled)
            if r['state'] != 'succeeded':
                raise OdooJobError('PREVIEW_BASE_FAILED', 'No se pudo crear el Odoo 19 base; ver el log del trabajo')
            if src is not None:
                external_sql(src, sql_files, db, log)
            if names:
                r = chain.wait(chain.enqueue('19.0', db, [addons] if addons else [], install=names), timeout, cancelled)
                missing = missing_modules(db, names) if r['state'] == 'succeeded' else names
                if missing:
                    raise OdooJobError('PREVIEW_INSTALL_FAILED', f"No quedaron instalados: {', '.join(missing)}. " + r['logTail'][-800:])
        password = secrets.token_urlsafe(12)
        script = (f"admin = env.ref('base.user_admin')\nadmin.write({{'password': {password!r}, 'active': True}})\n"
                  "env['ir.mail_server'].sudo().search([]).write({'active': False})\n"
                  f"env['ir.config_parameter'].sudo().set_param('web.base.url', {base_url.rstrip('/') + ':' + ports[slot - 1]!r})\n"
                  "env.cr.commit()\nprint('MF_PREVIEW ' + admin.login)\n")
        r = chain.wait(chain.enqueue('19.0', db, [addons] if addons else [], shell=script), timeout, cancelled)
        login = next((line.split(' ', 1)[1].strip() for line in r['logTail'].splitlines() if line.startswith('MF_PREVIEW ')), None)
        if not login:
            raise OdooJobError('PREVIEW_ADMIN_FAILED', 'No se pudo preparar el usuario administrador: ' + r['logTail'][-800:])
        chain.preview_serve(slot, db, addons)
        log(f'Odoo 19 en el espacio {slot} (puerto {ports[slot - 1]})')
        return {'slot': slot, 'port': ports[slot - 1], 'login': login, 'password': password, 'database': db,
                'source': 'migrated' if backup else 'code', 'modules': sorted(r['name'] for r in rows)}
    except Exception:
        chain.preview_release(slot)
        raise
