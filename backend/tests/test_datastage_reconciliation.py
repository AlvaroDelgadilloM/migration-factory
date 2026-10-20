"""DataStage -> Airflow reconciliation (doc 60): what the generated project compares, how an uploaded result is evaluated and
kept as evidence. The run against real Oracle and Airflow is the workflow datastage-e2e.yml (tests/e2e/datastage_oracle.py)."""
import io
import json
import zipfile
from pathlib import Path

import pytest

from mf.datastage import adapter, reconcile
from mf.datastage.expressions import Untranslatable, translate
from mf.datastage.generation import RECON_FORMAT
from mf.datastage.parser import parse_files
from mf.datastage.registry import param_resolver

FIX = Path(__file__).parent / 'fixtures' / 'datastage'


@pytest.fixture(scope='module')
def generated(tmp_path_factory):
    rep = adapter.migrate(FIX, tmp_path_factory.mktemp('ds') / 'out', 'shop')
    return Path(rep['output']['project'])


def test_reconciliation_spec_is_runnable_sql_on_both_schemas(generated):
    spec = json.loads((generated / 'tests/reconciliation/reconciliation.json').read_text(encoding='utf-8'))
    assert spec['format'] == RECON_FORMAT
    tables = {t['table']: t for t in spec['tables']}
    first = tables['FIRST_ORDERS']                                   # the schema is the side being compared, never doubled
    assert first['excluded'] == ['LOADED_AT'] and 'LOADED_AT' not in first['columns']   # filled with the run time
    checks = dict(first['checks'])
    assert checks['checksum'] == "SELECT SUM(ORA_HASH(CUSTOMER_ID || '|' || AMOUNT || '|' || LOADED_BY)) FROM @SCHEMA@.FIRST_ORDERS"
    assert all('@SCHEMA@.FIRST_ORDERS' in q and '{{' not in q for q in checks.values())
    assert first['rowHash'].startswith('SELECT CUSTOMER_ID, ORA_HASH(')
    assert tables['ORDER_TOTALS']['keys'] == ['ORDER_ID']
    sql = (generated / 'tests/reconciliation/jp_load_orders.sql').read_text(encoding='utf-8')
    assert 'LEGACY_SCHEMA.FIRST_ORDERS MINUS' in sql and 'TARGET_SCHEMA.FIRST_ORDERS MINUS' in sql   # both directions
    assert 'ORA_HASH( ||' not in sql
    runner = (generated / 'tests/reconciliation/run_reconciliation.py').read_text(encoding='utf-8')
    compile(runner, 'run_reconciliation.py', 'exec')
    assert RECON_FORMAT in runner and 'password' not in runner.lower()


def test_variables_read_by_expressions_are_declared_and_credentials_are_not_translated(generated):
    envs = json.loads((generated / 'config/variables.json').read_text(encoding='utf-8'))['environments']
    assert set(envs['qa']) == {'P_CONN__P_SCHEMA', 'P_CONN__P_USER'}   # LOADED_BY = #P_CONN.$P_USER# renders in Airflow
    job = next(j for j in parse_files([FIX / 'shop_flow.dsx'], 'shop', FIX).jobs if j.name == 'JP_LOAD_ORDERS')
    used = set()
    params = param_resolver(job, used)
    assert translate('P_CONN.$P_USER', {}, params) == "'{{ var.value.P_CONN__P_USER }}'" and used == {'P_CONN__P_USER'}
    with pytest.raises(Untranslatable, match='Connection'):
        translate('P_CONN.$P_PWD', {}, params)


def _result(**changes):
    table = {'job': 'JP_PUBLISH', 'stage': 'DB_200_UPDATE_TOTALS', 'table': 'ORDER_TOTALS', 'excluded': [], 'verdict': 'EQUAL',
             'checks': [{'name': 'row_count', 'legacy': '6', 'target': '6'}, {'name': 'checksum', 'legacy': '91', 'target': '91'}],
             'diff': {'onlyLegacy': [], 'onlyTarget': [], 'changed': [], 'truncated': False}} | changes
    return {'format': RECON_FORMAT, 'project': 'shop', 'executedAt': '2026-10-20T10:00:00+00:00',
            'legacy': {'conn': 'oracle_qa', 'schema': 'DS_LEGADO'}, 'target': {'conn': 'oracle_qa', 'schema': 'AF_MIGRADO'}, 'tables': [table]}


def test_evaluation_recomputes_verdicts_and_refuses_foreign_results():
    jobs = {'JP_LOAD_ORDERS', 'JP_PUBLISH', 'JS_MASTER'}
    ok = reconcile.evaluate(_result(), jobs)
    assert ok['status'] == 'VERIFIED' and ok['jobs'] == {'JP_PUBLISH': 'VERIFIED'} and ok['counts']['NOT_RECONCILED'] == 2
    lie = reconcile.evaluate(_result(checks=[{'name': 'checksum', 'legacy': '91', 'target': '92'}]), jobs)   # file said EQUAL
    assert lie['status'] == 'DIFFERENT' and lie['tables'][0]['checks'][0]['equal'] is False
    rows = reconcile.evaluate(_result(diff={'changed': ['103'], 'onlyLegacy': [], 'onlyTarget': []}), jobs)
    assert rows['tables'][0]['verdict'] == 'DIFFERENT' and rows['tables'][0]['differences'] == 1
    err = reconcile.evaluate(_result(error='DatabaseError: ORA-00942: table or view does not exist'), jobs)
    assert err['status'] == 'ERROR' and err['jobs']['JP_PUBLISH'] == 'ERROR'
    for bad, msg in ((_result() | {'format': 'otro'}, 'reconciliation-result'), (_result(job='OTRO_PROYECTO'), 'no pertenece'),
                     (_result(checks=[]), 'sin chequeos'), (_result(checks=[{'name': 'x', 'legacy': {'a': 1}, 'target': '1'}]), 'no válido')):
        with pytest.raises(reconcile.InvalidResult, match=msg):
            reconcile.evaluate(bad, jobs)


def test_platform_keeps_the_reconciliation_evidence(client, token, tmp_path):
    from helpers import run_job
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.write(FIX / 'shop_flow.dsx', 'shop/shop_flow.dsx')
    hdr = token('ana.arquitecta') | {'Content-Type': 'application/zip', 'X-Filename': 'dsx.zip', 'X-Migration-Type': 'DATASTAGE_AIRFLOW'}
    up = client.post('/api/v1/uploads', content=buf.getvalue(), headers=hdr)
    pid = client.post('/api/v1/projects', headers=token('ana.arquitecta'), json={
        'name': f'Recon {tmp_path.name[-6:]}', 'sourceType': 'zip', 'uploadId': up.json()['id'], 'targetRuntime': 'airflow',
        'migrationType': 'DATASTAGE_AIRFLOW'}).json()['id']
    start = client.post(f'/api/v1/projects/{pid}/datastage-migrations', headers=token('ana.arquitecta'), json={}).json()
    url = f"/api/v1/datastage-migrations/{start['dataStageMigrationId']}/reconciliations"
    assert client.post(url, headers=token('ana.arquitecta'), json=_result()).status_code == 409        # nothing migrated yet
    run_job(start['jobId'])
    assert client.put(f'/api/v1/projects/{pid}/members', headers=token('ana.arquitecta'),
                      json={'subjectId': 'aldo.auditor', 'role': 'auditor'}).status_code == 200
    assert client.post(url, headers=token('aldo.auditor'), json=_result()).status_code == 403          # read-only role
    bad = client.post(url, headers=token('ana.arquitecta'), json=_result(job='OTRO'))
    assert bad.status_code == 422 and bad.json()['code'] == 'RECONCILIATION_INVALID'
    first = client.post(url, headers=token('ana.arquitecta'), json=_result(diff={'changed': ['103']}))
    assert first.status_code == 200, first.text
    second = client.post(url, headers=token('ana.arquitecta'), json=_result()).json()['reconciliation']
    assert second['latest']['status'] == 'VERIFIED' and second['latest']['uploadedBy'] == 'ana.arquitecta'
    assert [h['status'] for h in second['history']] == ['VERIFIED', 'DIFFERENT']                       # newest first
    dm = client.get(f"/api/v1/datastage-migrations/{start['dataStageMigrationId']}", headers=token('aldo.auditor')).json()
    assert dm['reconciliation']['latest']['jobs'] == {'JP_PUBLISH': 'VERIFIED'}
