"""Doc 58 AI fallback: proposals for expressions the deterministic translator refuses, gated by deterministic validators and
never applied. The model is replaced by a fake: what is tested is what the platform does with any answer."""
import json
from pathlib import Path

from mf.datastage import adapter, ai_fallback

FIX = Path(__file__).parent / 'fixtures' / 'datastage'


def refused_source(tmp_path):
    src = tmp_path / 'src'
    src.mkdir(exist_ok=True)
    text = (FIX / 'shop_flow.dsx').read_text(encoding='utf-8')
    (src / 'x.dsx').write_text(text.replace('NullToZero(LK_SO200_TO_TF300.AMOUNT)', 'Ereplace(LK_SO200_TO_TF300.AMOUNT, ".", ",")'), encoding='utf-8')
    return src


def test_validators_accept_one_expression_over_known_columns_only():
    item = {'inputs': ['AMOUNT'], 'stageVars': ['MF_SV_Flag']}
    assert ai_fallback.validate("REPLACE(TO_CHAR(src.AMOUNT), '.', ',')", item) == []
    assert ai_fallback.validate("CASE WHEN src.MF_SV_Flag = 'Y' THEN 1 ELSE 0 END", item) == []
    assert 'columnas inexistentes: SALARY' in ai_fallback.validate('src.SALARY * 2', item)
    assert any('SELECT' in p for p in ai_fallback.validate('(SELECT MAX(x) FROM t)', item))
    assert any(';' in p for p in ai_fallback.validate("src.AMOUNT; DROP TABLE t", item))
    assert 'paréntesis desbalanceados' in ai_fallback.validate('UPPER(src.AMOUNT', item)
    assert ai_fallback.validate("'SELECT; --'", item) == []                       # inside a string literal it is data
    assert ai_fallback.validate(None, item) == ['sin expresión']


def test_proposals_are_reported_validated_and_never_applied(tmp_path):
    sent = []

    def fake(prompt):
        sent.append(json.loads(prompt))
        return 'Claro: {"sql": "REPLACE(TO_CHAR(src.AMOUNT), \'.\', \',\')", "notes": "Ereplace equivale a REPLACE"}'
    rep = adapter.migrate(refused_source(tmp_path), tmp_path / 'out', 'shop', ai_assist=True, ai_call=fake)
    assert sent == [{'derivation': 'Ereplace(LK_SO200_TO_TF300.AMOUNT, ".", ",")', 'targetColumn': 'AMOUNT',
                     'inputColumns': ['AMOUNT', 'CUSTOMER_ID', 'ORDER_ID'], 'stageVariables': ['MF_SV_Customer', 'MF_SV_Flag', 'MF_SV_PrevCustomer'],
                     'whyRefused': 'función sin equivalente verificado: Ereplace()'}]    # no data, no parameter values
    [p] = rep['aiProposals']
    assert p['status'] == 'AI_PROPOSED_VALID' and p['job'] == 'JP_LOAD_ORDERS' and p['column'] == 'AMOUNT'
    proj = Path(rep['output']['project'])
    assert 'Ninguna se aplicó' in (proj / 'migration-report/ai-proposals.md').read_text(encoding='utf-8')
    assert not any('REPLACE(TO_CHAR' in f.read_text(encoding='utf-8') for f in (proj / 'sql').rglob('*.sql'))
    assert rep['jobs']['JP_LOAD_ORDERS']['migration']['state'] != 'AUTO_MIGRATED'     # a proposal does not make it automatic


def test_a_bad_or_failing_model_never_breaks_the_run(tmp_path):
    answers = iter(['{"sql": "(SELECT 1 FROM dual)"}'])
    rep = adapter.migrate(refused_source(tmp_path), tmp_path / 'a', 'shop', ai_assist=True, ai_call=lambda p: next(answers))
    assert rep['aiProposals'][0]['status'] == 'AI_PROPOSED_REJECTED'
    def boom(p):
        raise TimeoutError
    rep = adapter.migrate(refused_source(tmp_path), tmp_path / 'b', 'shop', ai_assist=True, ai_call=boom)
    assert rep['aiProposals'][0]['status'] == 'AI_ERROR' and rep['status'] != 'FAILED'


def test_ai_must_be_configured_to_be_requested(client, token, tmp_path, monkeypatch):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.write(FIX / 'shop_flow.dsx', 'x/shop_flow.dsx')
    hdr = token('ana.arquitecta') | {'Content-Type': 'application/zip', 'X-Filename': 'dsx.zip', 'X-Migration-Type': 'AUTO'}
    up = client.post('/api/v1/uploads', content=buf.getvalue(), headers=hdr).json()
    pid = client.post('/api/v1/projects', headers=token('ana.arquitecta'), json={
        'name': f'DS ai {tmp_path.name[-6:]}', 'sourceType': 'zip', 'uploadId': up['id'], 'targetRuntime': 'airflow',
        'migrationType': 'DATASTAGE_AIRFLOW'}).json()['id']
    monkeypatch.delenv('MF_AI_ENABLED', raising=False)
    assert client.get('/api/v1/capabilities', headers=token('ana.arquitecta')).json()['datastageAi'] is False
    r = client.post(f'/api/v1/projects/{pid}/datastage-migrations', headers=token('ana.arquitecta'), json={'aiAssist': True})
    assert r.status_code == 409 and r.json()['code'] == 'AI_NOT_CONFIGURED'
    monkeypatch.setenv('MF_AI_ENABLED', 'true')
    assert client.get('/api/v1/capabilities', headers=token('ana.arquitecta')).json()['datastageAi'] is True
