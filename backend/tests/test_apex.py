"""Oracle APEX between versions, phase 1: the analysis of the exports (mf.apex.analysis), without a database."""
import io
import json
import re
import zipfile

import pytest

from conftest import ROOT
from helpers import run_job
from mf.apex import analysis

API = '/api/v1'
DEMO = ROOT / 'examples' / 'apex-demo'


def result():
    return analysis.analyze(DEMO)


def test_every_rule_compiles_and_cites_a_source_or_says_it_has_none():
    cat = analysis.catalog()
    assert cat['versions'] == sorted(cat['versions'], key=analysis.version_key) and set(cat['yearly']) <= set(cat['versions'])
    for r in cat['rules']:
        re.compile(r['match'])
        assert r['severity'] in analysis.SEVERITIES and (r['since'] is None or r['since'] in cat['versions'])
        assert r['source'] in cat['sources'] or (r['source'] is None and r['status'] in ('UNCONFIRMED', 'INTERNAL'))


def test_an_export_is_read_without_its_binary_files_and_counts_each_page():
    res = result()
    (a,) = res['apps']
    assert (a['appId'], a['name'], a['owner'], a['version']) == ('100', 'Tienda demo', 'XXDEMO', '19.1')
    assert a['stats']['Pages'] == 2 and a['stats']['Items'] == 1204         # with thousands; the first Items, not the shared one
    assert a['compatibilityMode'] == '5.1' and a['themeId'] == 42 and a['authentication'] == ['NATIVE_CUSTOM']
    assert a['plugins'] == [{'name': 'BE.CTB.SELECT2', 'displayName': 'Select2', 'type': 'ITEM TYPE', 'version': '3.0', 'uses': 1}]
    found = {f['rule']: f for f in a['findings']}
    assert found['APEX_LANG_MESSAGE']['count'] == 2 and found['APEX_LANG_MESSAGE']['where'] == ['página 1 (2)']
    assert found['INIT_PAGE_ITEM_JS']['severity'] == 'BLOCK' and found['INIT_PAGE_ITEM_JS']['source'].startswith('https://docs.oracle.com/')
    assert {'JQUERY_DATE_PICKER', 'LEGACY_MOBILE_REPORT', 'OLD_DATA_UPLOAD', 'INTERNAL_API', 'HTP_OUTPUT', 'AUTHORIZATION_CHECK'} <= set(found)
    assert a['status'] == 'BLOCK' and a['counts']['BLOCK'] == 1 and res['summary']['blocked'] == 1


def test_the_schema_is_covered_only_by_the_ddl_in_the_zip():
    (a,) = result()['apps']
    assert a['schema'] == {'owner': 'XXDEMO', 'referenced': 2, 'defined': 1, 'missing': 1, 'missingSample': ['XXDEMO_MISSING'], 'schemas': ['XXDEMO']}


def test_a_dump_of_another_engine_does_not_define_the_oracle_schema(tmp_path):
    (tmp_path / 'f100.sql').write_bytes((DEMO / 'f100.sql').read_bytes())
    (tmp_path / 'maria.sql').write_text('/*!40101 SET NAMES utf8 */;\nCREATE TABLE IF NOT EXISTS `xxdemo_missing` (id int) ENGINE=InnoDB;\n', encoding='utf-8')
    (tmp_path / 'users.sql').write_text("INSERT INTO usuarios VALUES ('alguien@example.org', 'hash-secreto');\n", encoding='utf-8')
    res = analysis.analyze(tmp_path)
    assert res['apps'][0]['schema']['missing'] == 2 and res['summary']['foreignDumps'] == ['maria.sql']
    assert 'alguien@example.org' not in json.dumps(res) and 'hash-secreto' not in json.dumps(res)


@pytest.mark.parametrize('source, target, mode, route', [
    ('19.1', '26.1', 'yearly', ['20.2', '21.2', '22.2', '23.2', '24.2', '26.1']),
    ('19.1', '20.1', 'all', ['19.2', '20.1']),
    ('19.1', '22.1', 'yearly', ['20.2', '21.2', '22.1']),     # the target always closes the route
    ('21.2', '22.2', 'yearly', ['22.2']),
    ('24.2', '24.2', 'yearly', []),
])
def test_the_route_of_jumps(source, target, mode, route):
    assert analysis.hops(source, target, mode) == route


def test_each_change_belongs_to_the_jump_that_reaches_its_version():
    p = analysis.plan(result()['apps'], '26.1')
    by = {h['version']: {c['rule'] for c in h['changes']} for h in p['hops']}
    assert by['21.2'] == {'OLD_DATA_UPLOAD'} and by['22.2'] == {'JQUERY_DATE_PICKER'} and by['23.2'] == {'INIT_PAGE_ITEM_JS'}
    assert by['24.2'] == {'APEX_LANG_MESSAGE', 'LEGACY_MOBILE_REPORT'} and by['26.1'] == {'AUTHORIZATION_CHECK'}
    assert {c['rule'] for c in p['baseline']} == {'INTERNAL_API', 'HTP_OUTPUT'}
    assert p['hops'][3]['from'] == '22.2' and p['hops'][3]['counts']['BLOCK'] == 1
    assert '22.2 → 23.2' in analysis.markdown(result(), p)


def demo_zip(extra=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for f in sorted(DEMO.glob('*.sql')):
            z.writestr(f'apps/{f.name}', f.read_bytes())
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def test_apex_project_upload_analysis_and_plan(client, token, tmp_path):
    hdr = token('ana.arquitecta')
    up = client.post(f'{API}/uploads', content=demo_zip(), headers=hdr | {'Content-Type': 'application/zip', 'X-Filename': 'apex.zip', 'X-Migration-Type': 'AUTO'})
    assert up.status_code == 201, up.text
    st = up.json()['structure']
    assert st['migrationType'] == 'APEX' and st['apexApps'][0]['appId'] == '100' and 'files' not in st
    body = {'name': f'APEX {tmp_path.name[-6:]}', 'sourceType': 'zip', 'uploadId': up.json()['id'], 'migrationType': 'APEX', 'targetRuntime': 'apex'}
    assert client.post(f'{API}/projects', headers=hdr, json=body | {'targetRuntime': 'spring'}).status_code == 422
    pid = client.post(f'{API}/projects', headers=hdr, json=body).json()['id']
    assert client.post(f'{API}/projects/{pid}/scans', headers=hdr, json={}).json()['code'] == 'WRONG_MIGRATION_TYPE'
    phase = lambda: client.get(f'{API}/projects/{pid}', headers=hdr).json()['progress']
    assert phase()['phase'] == 'pending'
    assert client.post(f'{API}/projects/{pid}/apex-analyses', headers=hdr, json={'target': '99.9'}).status_code == 422
    r = client.post(f'{API}/projects/{pid}/apex-analyses', headers=hdr, json={'target': '24.2'}).json()
    assert client.post(f'{API}/projects/{pid}/apex-analyses', headers=hdr, json={}).status_code == 409    # one at a time
    run_job(r['jobId'])
    (a,) = client.get(f'{API}/projects/{pid}/apex-migrations', headers=hdr).json()
    assert a['state'] == 'partial_success' and a['summary']['apps'] == 1 and a['apps'][0]['status'] == 'BLOCK'
    assert a['plan']['target'] == '24.2' and a['plan']['hops'][-1]['version'] == '24.2'
    assert phase()['phase'] == 'analyzed' and phase()['step'] == 'Análisis de aplicaciones'
    p = client.get(f"{API}/apex-migrations/{a['id']}/plan", headers=hdr, params={'target': '26.1', 'mode': 'all'}).json()
    assert p['hops'][0]['version'] == '19.2' and p['hops'][-1]['version'] == '26.1'
    report = client.get(f"{API}/artifacts/{a['summary']['artifacts']['report']}/download", headers=hdr)
    assert report.status_code == 200 and 'Tienda demo' in report.text
    assert client.get(f'{API}/apex/versions', headers=hdr).json()['versions'][-1] == '26.1'


def test_a_zip_without_exports_is_refused(client, token):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('nota.txt', 'nada')
    r = client.post(f'{API}/uploads', content=buf.getvalue(), headers=token('ana.arquitecta') | {'Content-Type': 'application/zip', 'X-Migration-Type': 'APEX'})
    assert r.status_code == 422 and r.json()['code'] == 'NO_APEX_EXPORT'
