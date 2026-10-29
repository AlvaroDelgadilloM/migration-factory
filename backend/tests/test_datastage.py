"""DataStage -> Airflow adapter (docs migration-factory-datastage-airflow 51-68). Fixture: tests/fixtures/datastage/shop_flow.dsx
(written for these tests, no client data): 2 parallel jobs + 1 sequence + 1 job with an unknown stage and a literal password.
The BIBES regression corpus runs only when MF_DATASTAGE_CORPUS points at the folder (client data never enters the repo);
the DagBag gate runs only when MF_AIRFLOW_PYTHON points at an interpreter with Apache Airflow."""
import json
import os
import zipfile
from pathlib import Path

import pytest

from mf import cli_tool
from mf.datastage import adapter
from mf.datastage.analysis import analyze, waves
from mf.datastage.expressions import Untranslatable, stage_vars_sql, translate
from mf.datastage.parser import parse_files, parse_text
from mf.datastage.registry import decide

FIX = Path(__file__).parent / 'fixtures' / 'datastage'


@pytest.fixture(scope='module')
def project():
    return parse_files(sorted(FIX.glob('*.dsx')), 'shop', FIX)


def test_parser_multi_job_sequence_and_evidence(project):
    assert [j.name for j in project.jobs] == ['JP_LOAD_ORDERS', 'JP_PUBLISH', 'JS_MASTER', 'JP_LEGACY_KEYS']
    assert [j.name for j in project.sequences] == ['JS_MASTER'] and project.metadata['dataStageVersion'] == '11.7'
    load = project.job('JP_LOAD_ORDERS')
    assert {p.name: p.is_set for p in load.parameters} == {'P_CONN': True, 'P_RUN_DATE': False}
    db = load.stage('DB_100_READ_ORDERS')
    assert db.properties['Usage/SQL/SelectStatement'].startswith('SELECT CUSTOMER_ID') and db.properties['Connection/Password'] == '#P_CONN.$P_PWD#'
    ev = db.evidence[0]
    assert (ev.source_file, ev.record_type, ev.property_name, ev.raw_value) == ('shop_flow.dsx', 'CCustomStage', 'StageType', 'OracleConnectorPX')
    tf = load.stage('TF_300_FLAG_FIRST')
    assert [v['name'] for v in tf.stage_vars] == ['PrevCustomer', 'Customer', 'Flag']
    assert tf.input_links == ['LK_SO200_TO_TF300'] and tf.output_links == ['LK_TF300_TO_DB400', 'LK_TF300_TO_DS500']  # pin order
    out = next(l for l in load.links if l.name == 'LK_TF300_TO_DB400')
    assert out.constraint == 'Flag = "F"' and out.expressions['AMOUNT'] == 'NullToZero(LK_SO200_TO_TF300.AMOUNT)'  # escaped quotes
    seq = project.job('JS_MASTER')
    cond = next(l for l in seq.links if l.name == 'LK_NC200_TO_JA300')
    assert cond.trigger == {'type': '6', 'expression': "P_MODE = 'FULL'"}


def test_unknown_payload_is_a_warning_and_malformed_dsx_is_not_a_crash(project):
    legacy = project.job('JP_LEGACY_KEYS')
    assert legacy.warnings[0] == 'tipo de registro sin interpretar: CFutureRecordType'
    assert legacy.stage('DB_100_LEGACY').properties['Connection/Password'] == '***REDACTED***'   # never in the IR
    assert any(w.startswith('SECRET_FINDING: DB_100_LEGACY.Connection/Password') for w in legacy.warnings)
    assert legacy.stage('SK_200_SURROGATE').properties['FutureProperty'] == 'kept as data'
    assert project.metadata['status'] == 'PARSED_WITH_WARNINGS'
    jobs, _, warnings = parse_text('BEGIN DSJOB\n   Identifier "X"\n   "valor sin clave"\n   BEGIN DSRECORD\n      Identifier "ROOT"\n', 'bad.dsx')
    assert [j.name for j in jobs] == ['X'] and jobs[0].warnings == ['job truncado: falta END DSJOB']
    assert any('no reconocida' in w for w in warnings) and any('truncado' in w for w in warnings)
    jobs, _, warnings = parse_text('BEGIN HEADER\nEND HEADER\n', 'empty.dsx')
    assert jobs == [] and warnings == []


def test_expressions_translate_or_refuse():
    assert translate('If isNull(A) Then setNull() Else Trim(A) : "x"', {'A': 'src.A'}) == \
        "CASE WHEN (src.A IS NULL) THEN NULL ELSE TRIM(src.A) || 'x' END"
    for bad in ('Generate(REJECT_REASON)', 'f.DecIT(x)', '@PARTITIONNUM=5'):
        with pytest.raises(Untranslatable):
            translate(bad, {'*': 'src'})
    svs = [{'name': 'Prev', 'expression': 'If isNull(Cur) Then setNull() Else Cur'}, {'name': 'Cur', 'expression': 'L.ID'},
           {'name': 'Seq', 'expression': 'If Cur = Prev Then Seq + 1 Else 1'}]
    with pytest.raises(Untranslatable, match='estado acumulado'):  # a running counter is not a LAG
        stage_vars_sql(svs, {}, 'ID')
    with pytest.raises(Untranslatable, match='orden de filas'):  # previous row without a known order
        stage_vars_sql(svs[:2], {}, None)


def test_registry_decides_from_evidence_not_type(project):
    load = project.job('JP_LOAD_ORDERS')
    d = {x.stage: x for x in (decide(load, s) for s in load.stages)}
    assert d['TF_300_FLAG_FIRST'].transformer == 'TRANSLATED_SQL' and d['TF_300_FLAG_FIRST'].confidence == 'MEDIUM'  # LAG => MEDIUM
    assert d['DB_400_INSERT_FIRST'].status == 'SUPPORTED' and 'INSERT' in d['DB_400_INSERT_FIRST'].target
    legacy = project.job('JP_LEGACY_KEYS')
    assert decide(legacy, legacy.stage('SK_200_SURROGATE')).status == 'UNSUPPORTED'
    seq = project.job('JS_MASTER')
    assert decide(seq, seq.stage('NC_200_MODE')).status == 'CONDITIONAL'  # P_MODE resolves as a sequence parameter


def test_dependency_graph_waves_complexity_and_pilot(project):
    a = analyze(project)
    edges = {(e['from'], e['to']) for e in a['graph']['edges']}
    assert ('JP_LOAD_ORDERS', 'JS_MASTER') in edges and ('JP_PUBLISH', 'JS_MASTER') in edges        # sequence -> JobActivity
    assert ('JP_LOAD_ORDERS', 'JP_PUBLISH') in edges                                                # dataset producer/consumer
    assert [w['jobs'] for w in a['graph']['waves']] == [['JP_LEGACY_KEYS', 'JP_LOAD_ORDERS'], ['JP_PUBLISH'], ['JS_MASTER']]
    jobs = {j['job']: j for j in a['jobs']}
    assert jobs['JP_LEGACY_KEYS']['migratability'] == 'ARCHITECTURAL_REDESIGN' and jobs['JP_LOAD_ORDERS']['complexity'] == 'MEDIUM'
    assert any(r.startswith('transformer') for r in jobs['JP_LOAD_ORDERS']['reasons'])
    assert a['pilot'] == {'LOW': 'JP_PUBLISH', 'MEDIUM': 'JP_LOAD_ORDERS', 'MASTER_SEQUENCE': 'JS_MASTER'}
    wv, cyc = waves(['A', 'B', 'C', 'D'], {'A': {'D'}, 'B': {'D'}})
    assert wv == [['A', 'B', 'C'], ['D']] and cyc == []                                             # doc 56 example


def test_migrate_generates_airflow_project_without_secrets(tmp_path, project):
    rep = adapter.migrate(FIX, tmp_path / 'out', 'shop')
    root = Path(rep['output']['project'])
    gates = {g['gate']: g for g in rep['gates']}
    assert rep['status'] == 'PARTIAL_SUCCESS' and rep['functionalValidation'] == 'FUNCTIONALLY_UNVERIFIED'   # never SUCCEEDED unreconciled
    assert gates['RECONCILIATION_GATE']['status'] == 'NOT_EXECUTABLE' and gates['STAGE_MAPPING_GATE']['status'] == 'FAIL'
    assert gates['SECRET_GATE']['status'] == 'WARN' and gates['SECRET_GATE']['secretFindings'] == ['JP_LEGACY_KEYS/DB_100_LEGACY']
    assert gates['PACKAGE_GATE']['status'] == 'PASS' and gates['SQL_VALIDATION_GATE']['status'] == 'PASS'
    everything = '\n'.join(p.read_text(errors='ignore') for p in root.rglob('*') if p.is_file())
    assert 'S3cretLiteral!' not in everything                                      # IR, reports, SQL and DAGs: references only
    assert '$P_PWD' not in ''.join(p.read_text() for p in root.glob('sql/**/*.sql')) + ''.join(p.read_text() for p in root.glob('dags/*.py'))
    sql = (root / 'sql/jp_load_orders/00_db_400_insert_first.sql').read_text()
    assert 'LAG(src.CUSTOMER_ID) OVER (ORDER BY CUSTOMER_ID ASC, ORDER_ID DESC)' in sql
    assert "WHERE (src.MF_SV_Flag = 'F')" in sql and "'{{ var.value.P_CONN__P_USER }}' AS LOADED_BY" in sql
    assert "DATE '{{ params.P_RUN_DATE }}'" in sql and sql.index('LAG(') < sql.index("WHERE (src.MF_SV_Flag")
    merge = (root / 'sql/jp_publish/00_db_200_update_totals.sql').read_text()
    assert 'MERGE INTO {{ var.value.P_CONN__P_SCHEMA }}.ORDER_TOTALS t' in merge and 'ON (t.ORDER_ID = s.ORDER_ID)' in merge
    dag = (root / 'dags/js_master.py').read_text()
    for piece in ("TaskGroup(group_id='ja_100_load'", "BranchPythonOperator(task_id='nc_200_mode'", 'trigger_rule="one_failed"',
                  "conn_id='oracle_p_conn'", 'nc_200_mode >> ja_300_publish', '[ja_100_load, ja_300_publish] >> ex_400_error'):
        assert piece in dag, piece
    legacy_dag = (root / 'dags/jp_legacy_keys.py').read_text()
    assert 'runtime.manual_step' in legacy_dag                                       # never half-translated SQL
    manifest = json.loads((root / '.migration/manifest.json').read_text())
    assert manifest['jobCount'] == 4 and manifest['sourceType'] == 'IBM_DATASTAGE_DSX' and 'source/shop_flow.dsx' in manifest['hashes']
    conns = json.loads((root / 'config/connection-manifest.json').read_text())['connections']
    assert all('value' not in (c['fields'].get('password') or {}) for c in conns)
    recon = (root / 'tests/reconciliation/jp_load_orders.sql').read_text()
    assert all(k in recon for k in ('row_count', 'duplicate_count', 'null_count:AMOUNT', 'checksum', 'sum:AMOUNT', 'MINUS'))
    ir = json.loads((root / 'migration-report/datastage-ir.json').read_text())
    assert [j['name'] for j in ir['jobs']] == ['JP_LOAD_ORDERS', 'JP_PUBLISH', 'JS_MASTER', 'JP_LEGACY_KEYS']   # source -> IR traceability
    with zipfile.ZipFile(rep['output']['zip']) as z:
        assert 'airflow-shop/dags/js_master.py' in z.namelist()
    assert list(FIX.iterdir()) == [FIX / 'shop_flow.dsx']                            # the source is never modified


def test_cli_analyze_and_migrate_from_zip(tmp_path, capsys):
    z = tmp_path / 'export.zip'
    with zipfile.ZipFile(z, 'w') as zf:
        zf.write(FIX / 'shop_flow.dsx', 'export/shop_flow.dsx')
    assert cli_tool.main(['datastage', 'analyze', str(z)]) == 0
    assert json.loads(capsys.readouterr().out)['jobs'] == 4
    assert cli_tool.main(['datastage', 'migrate', str(z), '--output', str(tmp_path / 'o')]) == 0
    assert (tmp_path / 'o' / 'airflow-export.zip').exists()
    bad = tmp_path / 'evil.zip'
    with zipfile.ZipFile(bad, 'w') as zf:
        zf.writestr('../escape.dsx', 'x')
    assert cli_tool.main(['datastage', 'analyze', str(bad)]) == 4


@pytest.mark.skipif(not os.environ.get('MF_AIRFLOW_PYTHON'), reason='DagBag: MF_AIRFLOW_PYTHON (intérprete con Apache Airflow) no definido')
def test_dag_parse_gate_with_airflow(tmp_path):
    rep = adapter.migrate(FIX, tmp_path / 'out', 'shop')
    g = next(g for g in rep['gates'] if g['gate'] == 'DAG_PARSE_GATE')
    assert g['status'] == 'PASS' and set(g['dags']) == {'js_master', 'jp_legacy_keys'} and g['dags']['js_master']['tasks'] >= 6


@pytest.mark.skipif(not os.environ.get('MF_DATASTAGE_CORPUS'), reason='corpus BIBES: MF_DATASTAGE_CORPUS no definido (datos del cliente, fuera del repo)')
def test_bibes_regression_corpus(tmp_path):
    """Doc 67 first goal: 100% parsed, inventoried and classified, dependency graph without cycles, manual actions explicit."""
    src = Path(os.environ['MF_DATASTAGE_CORPUS'])
    rep = adapter.migrate(src, tmp_path / 'out', 'BIBES')
    assert rep['parse']['status'] == 'PARSED' and rep['parse']['jobs'] == 55 and rep['parse']['sequences'] == 10
    assert all(j['complexity'] and j['migratability'] for j in rep['jobs'].values())
    assert not rep['graph']['cycles'] and rep['manualActions']
    assert set(rep['pilot']) == {'LOW', 'MEDIUM', 'HIGH', 'MASTER_SEQUENCE'}
    gates = {g['gate']: g['status'] for g in rep['gates']}
    assert gates['SECRET_GATE'] == 'PASS' and gates['SQL_VALIDATION_GATE'] == 'PASS' and gates['PACKAGE_GATE'] == 'PASS'
    if os.environ.get('MF_AIRFLOW_PYTHON'):
        assert gates['DAG_PARSE_GATE'] == 'PASS'


def test_platform_datastage_project_upload_job_and_artifacts(client, token, tmp_path):
    """A DATASTAGE_AIRFLOW project: DSX ZIP upload (no pom.xml), the Camel scan refused, the migration runs in the worker."""
    import io
    from helpers import run_job
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.write(FIX / 'shop_flow.dsx', 'bibes-like/shop_flow.dsx')
    hdr = token('ana.arquitecta') | {'Content-Type': 'application/zip', 'X-Filename': 'dsx.zip'}
    assert client.post('/api/v1/uploads', content=buf.getvalue(), headers=hdr).status_code == 422          # Maven upload: no pom.xml
    up = client.post('/api/v1/uploads', content=buf.getvalue(), headers=hdr | {'X-Migration-Type': 'DATASTAGE_AIRFLOW'})
    assert up.status_code == 201, up.text
    assert up.json()['structure']['dsxFiles'] == ['bibes-like/shop_flow.dsx']
    md = io.BytesIO()
    with zipfile.ZipFile(md, 'w') as zf:
        zf.writestr('docs/51.md', '# x')
        zf.writestr('docs/52.md', '# y')
    bad = client.post('/api/v1/uploads', content=md.getvalue(), headers=hdr | {'X-Migration-Type': 'DATASTAGE_AIRFLOW'})
    assert bad.status_code == 422 and 'trae: 2 .md' in bad.text                      # says what the ZIP does contain
    body = {'name': f'BIBES {tmp_path.name[-6:]}', 'sourceType': 'zip', 'uploadId': up.json()['id'], 'targetRuntime': 'spring',
            'migrationType': 'DATASTAGE_AIRFLOW'}
    assert client.post('/api/v1/projects', headers=token('ana.arquitecta'), json=body).status_code == 422  # airflow target only
    r = client.post('/api/v1/projects', headers=token('ana.arquitecta'), json=body | {'targetRuntime': 'airflow'})
    assert r.status_code == 201, r.text
    pid = r.json()['id']
    assert r.json()['migrationType'] == 'DATASTAGE_AIRFLOW'
    assert client.post(f'/api/v1/projects/{pid}/scans', headers=token('ana.arquitecta'), json={}).status_code == 409   # not a Camel scan
    start = client.post(f'/api/v1/projects/{pid}/datastage-migrations', headers=token('ana.arquitecta'), json={})
    assert start.status_code == 202, start.text
    run_job(start.json()['jobId'])
    dm = client.get(f"/api/v1/datastage-migrations/{start.json()['dataStageMigrationId']}", headers=token('ana.arquitecta')).json()
    assert dm['state'] == 'partial_success' and dm['summary']['status'] == 'PARTIAL_SUCCESS', dm
    assert dm['summary']['parse']['jobs'] == 4 and dm['summary']['functionalValidation'] == 'FUNCTIONALLY_UNVERIFIED'
    assert dm['results']['JP_LOAD_ORDERS']['state'] == 'SEMIAUTOMATIC' and dm['results']['JP_LEGACY_KEYS']['state'] == 'MANUAL_REVIEW'
    names = {k.split('.', 1)[1] for k in dm['summary']['artifacts']}
    assert {'migration-report.md', 'manual-actions.md', 'datastage-ir.json'} <= names and any(n.startswith('airflow-bibes') and n.endswith('.zip') for n in names)
    listed = client.get(f'/api/v1/projects/{pid}/datastage-migrations', headers=token('ana.arquitecta')).json()
    assert [x['id'] for x in listed] == [dm['id']]
    acts = dm['summary']['actions']
    assert acts and {a['severity'] for a in acts} == {'manual', 'review'} and all(a['job'] and a['category'] for a in acts)
    legacy = next(a for a in acts if a['job'] == 'JP_LEGACY_KEYS' and a['severity'] == 'manual')
    url = f"/api/v1/datastage-migrations/{dm['id']}/actions"
    assert client.put(f'/api/v1/projects/{pid}/members', headers=token('ana.arquitecta'),
                      json={'subjectId': 'aldo.auditor', 'role': 'auditor'}).status_code == 200
    assert client.patch(url, headers=token('aldo.auditor'), json={'key': legacy['key'], 'done': True}).status_code == 403   # read-only role
    assert client.patch(url, headers=token('ana.arquitecta'), json={'key': '0' * 16, 'done': True}).status_code == 404
    done = client.patch(url, headers=token('ana.arquitecta'), json={'key': legacy['key'], 'done': True, 'note': 'rediseñado'})
    assert done.status_code == 200 and done.json()['actionState'][legacy['key']]['done'] is True
    again = client.post(f'/api/v1/projects/{pid}/datastage-migrations', headers=token('ana.arquitecta'), json={})
    run_job(again.json()['jobId'])                                                    # a re-run keeps the worklist progress
    dm2 = client.get(f"/api/v1/datastage-migrations/{again.json()['dataStageMigrationId']}", headers=token('ana.arquitecta')).json()
    assert dm2['actionState'][legacy['key']]['done'] is True
    ds_only = client.get('/api/v1/projects?migrationType=DATASTAGE_AIRFLOW', headers=token('ana.arquitecta')).json()['items']
    assert pid in [p['id'] for p in ds_only] and all(p['migrationType'] == 'DATASTAGE_AIRFLOW' for p in ds_only)
    auto = client.post('/api/v1/uploads', content=buf.getvalue(), headers=hdr | {'X-Migration-Type': 'AUTO'})
    assert auto.status_code == 201 and auto.json()['structure']['migrationType'] == 'DATASTAGE_AIRFLOW'
    md = io.BytesIO()
    with zipfile.ZipFile(md, 'w') as zf:
        zf.writestr('docs/51.md', '# x')
    none = client.post('/api/v1/uploads', content=md.getvalue(), headers=hdr | {'X-Migration-Type': 'AUTO'})
    assert none.status_code == 422 and 'exportaciones DataStage (.dsx) ni un respaldo de Odoo' in none.text and '1 .md' in none.text


def test_cancelling_a_queued_datastage_run_frees_the_project(client, token, tmp_path):
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.write(FIX / 'shop_flow.dsx', 'x/shop_flow.dsx')
    hdr = token('ana.arquitecta') | {'Content-Type': 'application/zip', 'X-Filename': 'dsx.zip', 'X-Migration-Type': 'AUTO'}
    up = client.post('/api/v1/uploads', content=buf.getvalue(), headers=hdr).json()
    pid = client.post('/api/v1/projects', headers=token('ana.arquitecta'), json={
        'name': f'DS cancel {tmp_path.name[-6:]}', 'sourceType': 'zip', 'uploadId': up['id'], 'targetRuntime': 'airflow',
        'migrationType': 'DATASTAGE_AIRFLOW'}).json()['id']
    first = client.post(f'/api/v1/projects/{pid}/datastage-migrations', headers=token('ana.arquitecta'), json={}).json()
    assert client.post(f'/api/v1/projects/{pid}/datastage-migrations', headers=token('ana.arquitecta'), json={}).status_code == 409
    assert client.post(f"/api/v1/jobs/{first['jobId']}/cancel", headers=token('ana.arquitecta')).status_code == 202
    dm = client.get(f"/api/v1/datastage-migrations/{first['dataStageMigrationId']}", headers=token('ana.arquitecta')).json()
    assert dm['state'] == 'cancelled'
    assert client.post(f'/api/v1/projects/{pid}/datastage-migrations', headers=token('ana.arquitecta'), json={}).status_code == 202


def test_dag_schedule_is_saved_validated_and_applied_by_the_next_run(client, token, tmp_path):
    """The DSX has no schedule: a cron per DAG is saved on the run (validated, audited) and the next migration writes it."""
    import io
    from helpers import run_job
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.write(FIX / 'shop_flow.dsx', 'x/shop_flow.dsx')
    hdr = token('ana.arquitecta') | {'Content-Type': 'application/zip', 'X-Filename': 'dsx.zip', 'X-Migration-Type': 'AUTO'}
    up = client.post('/api/v1/uploads', content=buf.getvalue(), headers=hdr).json()
    pid = client.post('/api/v1/projects', headers=token('ana.arquitecta'), json={
        'name': f'DS cron {tmp_path.name[-6:]}', 'sourceType': 'zip', 'uploadId': up['id'], 'targetRuntime': 'airflow',
        'migrationType': 'DATASTAGE_AIRFLOW'}).json()['id']
    first = client.post(f'/api/v1/projects/{pid}/datastage-migrations', headers=token('ana.arquitecta'), json={}).json()
    run_job(first['jobId'])
    url = f"/api/v1/datastage-migrations/{first['dataStageMigrationId']}"
    dm = client.get(url, headers=token('ana.arquitecta')).json()
    own = sorted(n for n, r in dm['results'].items() if r['ownDag'])
    called = next(n for n, r in dm['results'].items() if not r['ownDag'])
    assert own and dm['summary']['schedules'] == {}
    put = lambda s: client.put(url + '/schedules', headers=token('ana.arquitecta'), json={'schedules': s})
    assert put({own[0]: 'rm -rf /'}).status_code == 422 and put({own[0]: '0 2 * *'}).status_code == 422
    assert put({called: '0 2 * * *'}).status_code == 422                        # runs inside its sequence's DAG
    assert put({own[0]: '30 2 * * MON-FRI'}).status_code == 200
    again = client.post(f'/api/v1/projects/{pid}/datastage-migrations', headers=token('ana.arquitecta'), json={}).json()
    run_job(again['jobId'])
    dm2 = client.get(f"/api/v1/datastage-migrations/{again['dataStageMigrationId']}", headers=token('ana.arquitecta')).json()
    assert dm2['summary']['schedules'] == {own[0]: '30 2 * * MON-FRI'}             # carried over and applied


def test_generated_dag_carries_its_schedule(tmp_path):
    rep = adapter.migrate(FIX, tmp_path / 'out', 'shop', schedules={'JS_MASTER': '0 2 * * *'})
    proj = Path(rep['output']['project'])
    dags = {p.stem: p.read_text(encoding='utf-8') for p in (proj / 'dags').glob('*.py')}
    sched = [n for n, t in dags.items() if "schedule='0 2 * * *'" in t]
    assert len(sched) == 1 and all('schedule=None' in t for n, t in dags.items() if n not in sched)
    assert f'{sched[0]}: "0 2 * * *"' in (proj / 'config/schedules.yaml').read_text(encoding='utf-8')
