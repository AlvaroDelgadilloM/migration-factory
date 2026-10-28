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

    def analyze(ctx, run, dump, om_id, log):
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

    def migrate(ctx, analysis, decisions, om_id, log, progress, fetch, cancelled, timeout):
        got['uninstall'] = sorted(decisions)
        hops = [{'version': v, 'state': 'succeeded', 'exitCode': 0, 'errors': 0, 'baseVersion': f'{v}.1', 'logTail': ''} for v in analysis['hops']]
        return {'state': 'succeeded', 'hops': hops, 'database': 'mf_x_mig', 'reconciliationStatus': 'EQUAL',
                'reconciliation': [{'check': 'partners', 'label': 'Contactos', 'before': ['42'], 'after': ['42'], 'state': 'EQUAL'}]}
    monkeypatch.setattr(oj, 'migrate', migrate)
    dump = tmp_path / 'mig.dump'
    dump.write_bytes(b'PGDMP-19')
    monkeypatch.setattr(oj, 'dump', lambda ctx, run, database: dump)
    m = client.post(f'{API}/projects/{pid}/odoo-migrations', headers=token('ana.arquitecta')).json()
    run_job(m['jobId'])
    mig = client.get(f"{API}/odoo-migrations/{m['odooMigrationId']}", headers=token('ana.arquitecta')).json()
    assert mig['state'] == 'succeeded' and got['uninstall'] == ['fix:company_partner_company', 'x_acme_custom'] and [h['version'] for h in mig['hops']][-1] == '19.0'
    assert mig['summary']['reconciliationStatus'] == 'EQUAL' and mig['summary']['uninstalled'] == ['x_acme_custom'] and mig['summary']['fixesApplied'] == ['company_partner_company']
    dl = client.get(f"{API}/artifacts/{mig['summary']['artifacts']['dump']}/download", headers=token('ana.arquitecta'))
    assert dl.status_code == 200 and dl.content == b'PGDMP-19'
