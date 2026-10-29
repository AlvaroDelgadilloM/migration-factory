"""Odoo Community 12 -> 19 in the platform. The real chain (Odoo 12 demo database through the seven OpenUpgrade hops, then
reconciliation) runs in .github/workflows/odoo-e2e.yml; here the API and worker flow with the database server simulated."""
import io
import zipfile

from helpers import run_job
from mf.odoo import modules

API = '/api/v1'


def backup_zip(files=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, data in (files or {'dump.sql': '-- PostgreSQL database dump\n', 'manifest.json': '{"version": "12.0"}',
                                     'filestore/ab/abcdef': 'x'}).items():
            z.writestr(name, data)
    return buf.getvalue()


def upload(client, token, data):
    hdr = token('ana.arquitecta') | {'Content-Type': 'application/zip', 'X-Filename': 'odoo.zip', 'X-Migration-Type': 'AUTO'}
    return client.post(f'{API}/uploads', content=data, headers=hdr)


def test_classifier_reports_third_party_and_oca_availability():
    rows = {r['name']: r for r in modules.classify([
        {'name': 'sale', 'author': 'Odoo S.A.'}, {'name': 'partner_firstname', 'author': 'Camptocamp, Odoo Community Association (OCA)'},
        {'name': 'x_acme_custom', 'author': 'ACME'}])}
    assert rows['sale']['origin'] == 'CORE' and rows['sale']['outcome'] in ('OK', 'WARN')
    assert rows['partner_firstname']['availability']['19.0'] and rows['partner_firstname']['repository'] == 'partner-contact'
    assert rows['x_acme_custom']['outcome'] == 'BLOCK' and 'terceros' in rows['x_acme_custom']['reason']


def test_odoo_project_analysis_decisions_and_migration(client, token, monkeypatch, tmp_path):
    from mf.odoo import chain, jobs as oj
    up = upload(client, token, backup_zip())
    assert up.status_code == 201, up.text
    st = up.json()['structure']
    assert st['migrationType'] == 'ODOO' and st['odooDump'] == 'dump.sql' and st['filestore']
    plain = upload(client, token, backup_zip({'prod.dump': 'PGDMP'}))
    assert plain.json()['structure']['odooDump'] == 'prod.dump'
    body = {'name': f'Odoo {tmp_path.name[-6:]}', 'sourceType': 'zip', 'uploadId': up.json()['id'], 'migrationType': 'ODOO', 'targetRuntime': 'odoo19'}
    assert client.post(f'{API}/projects', headers=token('ana.arquitecta'), json=body | {'targetRuntime': 'spring'}).status_code == 422
    pid = client.post(f'{API}/projects', headers=token('ana.arquitecta'), json=body).json()['id']
    assert client.post(f'{API}/projects/{pid}/scans', headers=token('ana.arquitecta'), json={}).json()['code'] == 'WRONG_MIGRATION_TYPE'
    assert client.post(f'{API}/projects/{pid}/odoo-migrations', headers=token('ana.arquitecta')).json()['code'] == 'ANALYSIS_REQUIRED'

    rows = modules.classify([{'name': 'sale', 'author': 'Odoo S.A.'}, {'name': 'x_acme_custom', 'author': 'ACME'}])
    seen = {}

    def analyze(ctx, run, dump, om_id, log, custom=frozenset()):
        seen['dump'] = dump.read_text()
        return {'sourceDb': 'mf_x_src', 'sourceVersion': '12.0.1.3', 'from': '12.0', 'to': '19.0', 'hops': list(modules.VERSIONS[1:]),
                'counts': modules.summary(rows), 'before': [], 'rows': rows,
                'fixes': [{'id': 'company_partner_company', 'title': 't', 'why': 'w', 'fix': 'f', 'count': 1, 'records': []}]}
    monkeypatch.setattr(oj, 'analyze', analyze)
    r = client.post(f'{API}/projects/{pid}/odoo-analyses', headers=token('ana.arquitecta')).json()
    run_job(r['jobId'])
    a = client.get(f"{API}/odoo-migrations/{r['odooMigrationId']}", headers=token('ana.arquitecta')).json()
    assert a['state'] == 'succeeded' and seen['dump'].startswith('-- PostgreSQL') and a['summary']['counts']['block'] == 1
    blocked = client.post(f'{API}/projects/{pid}/odoo-migrations', headers=token('ana.arquitecta'))
    assert blocked.status_code == 409 and blocked.json()['details']['modules'] == ['x_acme_custom', 'fix:company_partner_company']   # shown, and they block

    url = f"{API}/odoo-migrations/{a['id']}/decisions"
    assert client.put(f'{API}/projects/{pid}/members', headers=token('ana.arquitecta'), json={'subjectId': 'diego.dev', 'role': 'developer'}).status_code == 200
    assert client.patch(url, headers=token('diego.dev'), json={'module': 'x_acme_custom', 'action': 'uninstall', 'reason': 'ya no se usa en la empresa'}).status_code == 403
    assert client.patch(url, headers=token('ana.arquitecta'), json={'module': 'x_acme_custom', 'action': 'uninstall', 'reason': 'corto'}).status_code == 422
    ok = client.patch(url, headers=token('ana.arquitecta'), json={'module': 'x_acme_custom', 'action': 'uninstall', 'reason': 'ya no se usa en la empresa'})
    assert ok.status_code == 200 and ok.json()['decisions']['x_acme_custom']['by'] == 'ana.arquitecta'
    fix = {'module': 'fix:company_partner_company', 'reason': 'dato inconsistente de la demo'}
    assert client.patch(url, headers=token('ana.arquitecta'), json=fix | {'action': 'uninstall'}).status_code == 422
    assert client.patch(url, headers=token('ana.arquitecta'), json=fix | {'module': 'fix:nope', 'action': 'apply'}).status_code == 404
    assert client.patch(url, headers=token('ana.arquitecta'), json={'module': 'x_acme_custom', 'action': 'apply', 'reason': 'no aplica a módulos'}).status_code == 422
    assert client.patch(url, headers=token('ana.arquitecta'), json=fix | {'action': 'apply'}).json()['decisions']['fix:company_partner_company']['action'] == 'apply'

    monkeypatch.setattr(chain, 'base_version', lambda db: '12.0.1.3')
    monkeypatch.setattr(chain, 'drop_db', lambda db: None)
    got = {}

    def migrate(ctx, analysis, decisions, om_id, log, progress, fetch, cancelled, timeout, custom=None):
        got['uninstall'] = sorted(decisions)
        hops = [{'version': v, 'state': 'succeeded', 'exitCode': 0, 'errors': 0, 'baseVersion': f'{v}.1', 'logTail': ''} for v in analysis['hops']]
        return {'state': 'succeeded', 'hops': hops, 'database': 'mf_x_mig', 'reconciliationStatus': 'EQUAL',
                'reconciliation': [{'check': 'partners', 'label': 'Contactos', 'before': ['42'], 'after': ['42'], 'state': 'EQUAL'}]}
    monkeypatch.setattr(oj, 'migrate', migrate)
    dump = tmp_path / 'mig.dump'
    dump.write_bytes(b'PGDMP-19')
    monkeypatch.setattr(oj, 'backup', lambda ctx, run, database, source_zip=None: (seen.__setitem__('filestore', source_zip is not None), dump)[1])
    m = client.post(f'{API}/projects/{pid}/odoo-migrations', headers=token('ana.arquitecta')).json()
    run_job(m['jobId'])
    mig = client.get(f"{API}/odoo-migrations/{m['odooMigrationId']}", headers=token('ana.arquitecta')).json()
    assert mig['state'] == 'succeeded' and got['uninstall'] == ['fix:company_partner_company', 'x_acme_custom'] and [h['version'] for h in mig['hops']][-1] == '19.0'
    assert seen['filestore'] and mig['summary']['filestore']
    assert mig['summary']['reconciliationStatus'] == 'EQUAL' and mig['summary']['uninstalled'] == ['x_acme_custom'] and mig['summary']['fixesApplied'] == ['company_partner_company']
    dl = client.get(f"{API}/artifacts/{mig['summary']['artifacts']['dump']}/download", headers=token('ana.arquitecta'))
    assert dl.status_code == 200 and dl.content == b'PGDMP-19'


def test_odoo_module_code_inventory_port_and_install_check(client, token, monkeypatch, tmp_path):
    from pathlib import Path
    from mf.odoo import jobs as oj
    code = upload(client, token, backup_zip({'addons/my_sale/__manifest__.py': "{'version': '12.0.1.0.0', 'depends': ['sale']}",
                                             'addons/my_sale/models/sale.py': "@api.multi\ndef x(self):\n    pass\n",
                                             'backups/custom.sql': 'CREATE SCHEMA legacy_ext;',
                                             'addons/my_sale/data/init.sql': 'select 1;'}))
    assert code.status_code == 201, code.text
    st = code.json()['structure']
    assert st['migrationType'] == 'ODOO' and st['odooModules'] == ['my_sale'] and not st['odooDump']
    assert st['odooSql'] == ['backups/custom.sql']          # external SQL, not a database; a module's own .sql is not counted
    full = upload(client, token, backup_zip({'lab/backups/full.sql': '-- dump\nCREATE TABLE public.ir_module_module (id int);'}))
    assert full.json()['structure']['odooDump'] == 'lab/backups/full.sql'   # a full Odoo database at any depth, by its content
    body = {'name': f'Odoo code {tmp_path.name[-6:]}', 'sourceType': 'zip', 'uploadId': code.json()['id'], 'migrationType': 'ODOO', 'targetRuntime': 'odoo19'}
    pid = client.post(f'{API}/projects', headers=token('ana.arquitecta'), json=body).json()['id']
    assert client.post(f'{API}/projects/{pid}/odoo-analyses', headers=token('ana.arquitecta')).json()['code'] == 'NO_DUMP'
    seen = {}

    def port_code(ctx, run, src, om_id, log, cancelled, timeout, sql_files=()):
        seen['sql'] = list(sql_files)
        seen['src'] = sorted(str(p.relative_to(src)) for p in Path(src).rglob('*.py'))
        ported = ctx.workdir / 'ported'
        (ported / 'my_sale').mkdir(parents=True)
        (ported / 'my_sale' / '__manifest__.py').write_text("{'version': '19.0.1.0.0'}")
        rows = [{'name': 'my_sale', 'version': '12.0.1.0.0', 'depends': ['sale'], 'counts': {'AUTO': 1, 'MANUAL': 0, 'REVIEW': 0},
                 'findings': [], 'install': {'state': 'failed', 'errors': 1, 'logTail': 'ERROR x'}}]
        return {'rows': rows, 'portWarnings': [], 'base': {'state': 'succeeded'}, 'ported': ported, 'diff': '--- a\n+++ b\n', 'report': '{}'}
    monkeypatch.setattr(oj, 'port_code', port_code)
    r = client.post(f'{API}/projects/{pid}/odoo-code', headers=token('ana.arquitecta')).json()
    run_job(r['jobId'])
    c = client.get(f"{API}/odoo-migrations/{r['odooMigrationId']}", headers=token('ana.arquitecta')).json()
    assert seen['src'] == ['addons/my_sale/__manifest__.py', 'addons/my_sale/models/sale.py'], seen
    assert seen['sql'] == ['backups/custom.sql']
    assert c['mode'] == 'code' and c['state'] == 'partial_success' and c['summary']['counts'] == {'AUTO': 1, 'MANUAL': 0, 'REVIEW': 0, 'modules': 1, 'installed': 0, 'testsPassed': 0, 'tested': 0}
    dl = client.get(f"{API}/artifacts/{c['summary']['artifacts']['ported']}/download", headers=token('ana.arquitecta'))
    assert dl.status_code == 200 and dl.content[:2] == b'PK'


def test_odoo_jobs_get_their_own_time_limit():
    from mf.jobs import timeout_for   # a whole chain outlives the 30 min of short jobs (RQ killed a real migration at 32 min)
    assert timeout_for('odoo_migrate') == timeout_for('odoo_code') == 12 * 3600 and timeout_for('scan') == 1800


def test_odoo_backup_zip_has_dump_manifest_and_the_uploaded_filestore(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    from mf.odoo import chain, jobs as oj
    monkeypatch.setattr(chain, 'base_version', lambda db: '19.0.1.3')
    monkeypatch.setattr(chain, 'server_version', lambda: '13.23')
    monkeypatch.setattr(chain, 'installed_modules', lambda db: [{'name': 'base', 'author': '', 'version': '19.0.1.3'}])

    def run(cmd, cwd, ctx, **kw):
        open(cmd[cmd.index('-f') + 1], 'w').write('-- PostgreSQL database dump\n')
        return SimpleNamespace(exit_code=0)
    src = tmp_path / 'up.zip'
    src.write_bytes(backup_zip())
    out = oj.backup(SimpleNamespace(workdir=tmp_path), run, 'mf_x_mig', src)
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ['dump.sql', 'filestore/ab/abcdef', 'manifest.json']
        assert json.loads(z.read('manifest.json'))['version'] == '19.0' and z.read('filestore/ab/abcdef') == b'x'
