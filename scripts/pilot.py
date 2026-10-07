#!/usr/bin/env python3
"""Synthetic end-to-end pilot against a running stack (docker compose up).

Drives the real API: register project -> scan (real git + mvn help:effective-pom) -> review findings ->
plan (Spring profile) -> approve -> preview (OpenRewrite dryRun) -> apply (OpenRewrite run per step,
baseline + candidate compile/test) -> diff review -> gates/PR blockers -> report download.

This is a PRODUCT test of Migration Factory on a synthetic fixture. It is NOT evidence that any real
application was migrated equivalently. Stdlib only.

Usage: python3 scripts/pilot.py [--source local|zip|git] [--runtime spring|quarkus] [--base http://127.0.0.1:8000]
  local: folder /sources/pilot-orders as seen by the worker (default); zip: uploads examples/pilot-orders zipped;
  git: --repo https://host/org/repo.git (an allowed host).
"""
import argparse
import io
import zipfile
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

TERMINAL = {'succeeded', 'partial_success', 'failed', 'blocked', 'cancelled', 'timed_out'}
OK = ('succeeded', 'partial_success')


class Api:
    def __init__(self, base):
        self.base, self.tokens = base.rstrip('/') + '/api/v1', {}

    def call(self, method, path, user=None, body=None, headers=None, raw=False, ok=(200, 201, 202, 204), data=None):
        h = {'Content-Type': 'application/json'} | (headers or {})
        if user:
            h['Authorization'] = f'Bearer {self.token(user)}'
        req = urllib.request.Request(self.base + path, method=method, headers=h,
                                     data=data if data is not None else (json.dumps(body).encode() if body is not None else None))
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                status, hdrs = r.status, r.headers
        except urllib.error.HTTPError as e:
            data, status, hdrs = e.read(), e.code, e.headers
        if status not in ok:
            raise SystemExit(f'{method} {path} -> {status}: {data[:800]!r}')
        if raw:
            return data, hdrs
        return (json.loads(data) if data else None), hdrs

    def token(self, user):
        if user not in self.tokens:
            req = urllib.request.Request(self.base + '/auth/dev-login', method='POST', headers={'Content-Type': 'application/json'},
                                         data=json.dumps({'username': user}).encode())
            with urllib.request.urlopen(req, timeout=30) as r:
                self.tokens[user] = json.loads(r.read())['accessToken']
        return self.tokens[user]

    def get(self, path, user='aldo.auditor', **kw):
        return self.call('GET', path, user, **kw)[0]


def step(msg):
    print(f'\n== {msg}', flush=True)


def wait_job(api, job_id, user, limit=3600):
    last, start = 0, time.time()
    while time.time() - start < limit:
        for e in api.get(f'/jobs/{job_id}/logs?after={last}', user):
            last = e['id']
            if e['level'] != 'info' or e['message'].startswith(('Fase', 'Commit', 'Paso', 'baseline/', 'candidate/', 'Trabajo')):
                print(f"   [{e['phase']}] {e['message'][:160]}", flush=True)
        job = api.get(f'/jobs/{job_id}', user)
        if job['state'] in TERMINAL:
            return job
        time.sleep(3)
    raise SystemExit(f'job {job_id} did not finish in {limit}s')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://127.0.0.1:8000')
    ap.add_argument('--out', default='pilot-output')
    ap.add_argument('--runtime', default='spring', choices=['spring', 'quarkus'])
    ap.add_argument('--source', default='local', choices=['local', 'zip', 'git'])
    ap.add_argument('--path', default='/sources/pilot-orders')
    ap.add_argument('--repo', default=None)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    api = Api(a.base)
    result = {'runtime': a.runtime}

    step('1. Registrar proyecto y membresías')
    body = {'name': f'Piloto {a.source} {a.runtime} {uuid.uuid4().hex[:6]}', 'sourceType': a.source, 'targetRuntime': a.runtime}
    if a.source == 'local':
        body['localPath'] = a.path
    elif a.source == 'zip':
        buf = io.BytesIO()
        src = Path(__file__).resolve().parents[1] / 'examples' / 'pilot-orders'
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for f in sorted(src.rglob('*')):
                if f.is_file() and 'target' not in f.parts:
                    z.write(f, 'pilot-orders/' + f.relative_to(src).as_posix())
        up, _ = api.call('POST', '/uploads', 'ana.arquitecta', raw=False, body=None, headers={'Content-Type': 'application/zip', 'X-Filename': 'pilot-orders.zip'},
                         data=buf.getvalue())
        print(f"   ZIP subido: raíz {up['structure']['root']} · módulos {up['structure']['modules']}")
        body['uploadId'] = up['id']
    else:
        if not a.repo:
            raise SystemExit('--source git requiere --repo https://...')
        body |= {'repositoryUrl': a.repo, 'defaultBranch': 'main'}
    project, _ = api.call('POST', '/projects', 'ana.arquitecta', body)
    pid = project['id']
    for user, role in (('diego.dev', 'developer'), ('rosa.revisora', 'reviewer'), ('aldo.auditor', 'auditor')):
        api.call('PUT', f'/projects/{pid}/members', 'ana.arquitecta', {'subjectId': user, 'role': role})
    print(f'   proyecto {pid}')

    step('2. Analizar (checkout por commit + inventario + POM efectivo en sandbox)')
    key = f'pilot-scan-{pid}'
    scan_body = {'target': a.runtime} | ({'ref': 'main'} if a.source == 'git' else {})
    sc, _ = api.call('POST', f'/projects/{pid}/scans', 'diego.dev', scan_body, headers={'Idempotency-Key': key})
    again, _ = api.call('POST', f'/projects/{pid}/scans', 'diego.dev', scan_body, headers={'Idempotency-Key': key})
    assert again == sc, 'idempotency replay must return the same job'
    job = wait_job(api, sc['jobId'], 'diego.dev')
    assert job['state'] == 'succeeded', job
    scan = api.get(f"/scans/{sc['scanId']}")
    print(f"   snapshot {scan['snapshotHash'][:16]}… · commit {scan['commitSha']} · raíz {scan['projectRoot']} · Maven: {scan['mavenResolution']}")
    print(f"   resumen {json.dumps(scan['summary'])}")
    result['scan'] = {k: scan[k] for k in ('snapshotHash', 'commitSha', 'projectRoot', 'mavenResolution', 'summary')}
    findings = api.get(f"/scans/{scan['id']}/findings?limit=200")['items']
    for f in findings:
        print(f"   {f['severity']:6} {f['classification']:9} {f['ruleId']:18} {f['file']}:{f['line']}")

    step('3. Revisar hallazgos (motivo + auditoría)')
    camel2 = next(f for f in findings if f['ruleId'] == 'CAMEL2_VERSION')
    api.call('PATCH', f"/findings/{camel2['id']}/review", 'ana.arquitecta',
             {'decision': 'resolved', 'reason': 'Piloto sintético: rutas con endpoints string, sin APIs retiradas en 3.x; revisado contra guía 2→3.'},
             headers={'If-Match': str(camel2['version'])})
    jndi = next(f for f in findings if f['ruleId'] == 'JNDI')
    api.call('PATCH', f"/findings/{jndi['id']}/review", 'diego.dev',
             {'decision': 'proposed', 'reason': 'Propuesta: DataSource gestionado por el runtime con el mismo nombre de bean.'},
             headers={'If-Match': str(jndi['version'])})

    step('4. Plan con perfil versionado')
    prof = next(p for p in api.get('/profiles', 'ana.arquitecta') if p['runtime'] == a.runtime and p['status'] == 'validated')
    plan, _ = api.call('POST', f"/scans/{scan['id']}/plans", 'ana.arquitecta', {'profileId': prof['id']})
    for s in plan['steps']:
        print(f"   {s['ordinal']:2}. {s['key']:22} {s['kind']:10} {s['state']:8} {'; '.join(s['blockedReasons'])[:90]}")
    plan, _ = api.call('POST', f"/plans/{plan['id']}/approve", 'ana.arquitecta', headers={'If-Match': str(plan['rowVersion'])})
    print(f"   perfil {prof['name']} · plan v{plan['version']} {plan['status']} digest {plan['digest'][:12]}")

    step('5. Preview (OpenRewrite dryRun en copia aislada)')
    pv, _ = api.call('POST', f"/plans/{plan['id']}/preview", 'diego.dev')
    job = wait_job(api, pv['jobId'], 'diego.dev')
    preview = api.get(f"/executions/{pv['executionId']}")
    files = api.get(f"/executions/{pv['executionId']}/diff")['items']
    print(f"   estado {job['state']} · archivos en el diff: {[f['path'] for f in files]}")
    result['preview'] = {'state': job['state'], 'files': [f['path'] for f in files], 'error': job['errorMessage']}
    assert job['state'] in OK, job

    step('6. Ejecutar (un commit por paso, línea base y validaciones)')
    ex, _ = api.call('POST', f"/plans/{plan['id']}/executions", 'diego.dev', {'mode': 'apply'})
    job = wait_job(api, ex['jobId'], 'diego.dev')
    execution = api.get(f"/executions/{ex['executionId']}")
    summ = execution['summary']
    print(f"   Migration status: {summ.get('status')} · motivo: {summ.get('primaryReason')} · códigos: {summ.get('reasonCodes')}")
    assert job['state'] in OK, job
    for s in execution['steps']:
        print(f"   paso {s['key']:20} {s['state']:20} {(s.get('commit') or '')[:10]}")
    vals = api.get(f"/executions/{ex['executionId']}/validations")
    for v in vals:
        print(f"   validación {v['target']:9} {v['suite']:12} {v['status']:7} {v['tests'] or ''} {v['detail'] or ''}")
    files = api.get(f"/executions/{ex['executionId']}/diff")['items']
    print(f"   diff: {[(f['path'], f['changeType'], f['recipes']) for f in files]}")

    step('7. Revisión del diff por un revisor independiente')
    for f in files:
        api.call('POST', f"/diff-files/{f['id']}/review", 'rosa.revisora', {'decision': 'approved', 'reason': 'Revisado en piloto'},
                 headers={'If-Match': str(f['version'])})
    execution = api.get(f"/executions/{ex['executionId']}")
    for g in execution['gates']:
        print(f"   {g['gate']} {g['status']:8} {'(obligatorio)' if g['required'] else ''} {g['title']} — {g['detail'][:80]}")
    print('   PR borrador bloqueado por:' if execution['prBlockers'] else '   PR borrador permitido')
    for b in execution['prBlockers']:
        print(f'     - {b}')
    result['execution'] = {'steps': execution['steps'], 'summary': execution['summary'],
                           'validations': [{k: v[k] for k in ('target', 'suite', 'status', 'tests')} for v in vals],
                           'gates': execution['gates'], 'prBlockers': execution['prBlockers']}

    step('8. Artefactos y verificación de origen intacto')
    arts = api.get(f"/projects/{pid}/artifacts?executionId={ex['executionId']}")['items']
    for art in arts:
        if art['kind'] in ('report-html', 'report-json', 'patch', 'candidate-zip'):
            data, _ = api.call('GET', f"/artifacts/{art['id']}/download", 'aldo.auditor', raw=True)
            (out / art['name']).write_bytes(data)
    assert (out / 'candidate.zip').exists(), 'candidate.zip no generado'
    with zipfile.ZipFile(out / 'candidate.zip') as z:
        print(f"   candidate.zip: {len(z.namelist())} archivos (sin .git ni target)")
    sc2, _ = api.call('POST', f'/projects/{pid}/scans', 'diego.dev', {'ref': 'main'} if a.source == 'git' else {})
    wait_job(api, sc2['jobId'], 'diego.dev')
    rescan = api.get(f"/scans/{sc2['scanId']}")
    assert rescan['snapshotHash'] == scan['snapshotHash'], 'el origen cambió'
    print(f"   re-análisis del origen: mismo snapshot {rescan['snapshotHash'][:16]}… (origen sin modificar)")
    result['sourceUnchanged'] = True
    (out / 'pilot-result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f'\nResultado en {out.resolve()}/pilot-result.json. Recordatorio: compilar y pasar pruebas unitarias '
          'no demuestra equivalencia funcional (gate G4).')


if __name__ == '__main__':
    sys.exit(main())
