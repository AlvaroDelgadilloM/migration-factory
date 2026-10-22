"""MF_EXECUTION_MODE=k8s: one ephemeral pod per job. The manifest carries the isolation; the dispatcher creates, waits, removes."""
import json

import httpx

from mf.config import get_settings
from mf.worker import k8s_runner, mavenops, runner


def test_pod_isolation_in_the_manifest(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, 'k8s_image', 'ghcr.io/acme/mf-worker:abc123')
    m = k8s_runner.job_manifest('0123456789abcdef-0000-4000-8000-00000000cafe', s, 2100)
    spec, pod = m['spec'], m['spec']['template']['spec']
    c = pod['containers'][0]
    assert spec['backoffLimit'] == 0 and spec['activeDeadlineSeconds'] == 2100 and spec['ttlSecondsAfterFinished'] == 600
    assert pod['runtimeClassName'] == 'gvisor' and pod['automountServiceAccountToken'] is False and pod['restartPolicy'] == 'Never'
    assert pod['securityContext']['runAsNonRoot'] and pod['securityContext']['seccompProfile'] == {'type': 'RuntimeDefault'}
    assert c['securityContext'] == {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'privileged': False,
                                    'capabilities': {'drop': ['ALL']}}
    assert c['command'] == ['python', '-m', 'mf.worker.run_one', '0123456789abcdef-0000-4000-8000-00000000cafe']
    assert {'name': 'MF_EXECUTION_MODE', 'value': 'process'} in c['env']        # the pod runs the job itself, never re-dispatches
    assert c['envFrom'] == [{'secretRef': {'name': 'mf-runner-env'}}] and 'password' not in json.dumps(m).lower()
    assert {v['name'] for v in pod['volumes']} == {'work', 'tmp', 'm2', 'home'} and all('emptyDir' in v for v in pod['volumes'])
    assert c['resources']['limits']['ephemeral-storage'] == '20Gi'
    assert m['spec']['template']['metadata']['labels']['app.kubernetes.io/component'] == 'build-runner'   # NetworkPolicy selector
    monkeypatch.setattr(s, 'k8s_runtime_class', '')
    assert 'runtimeClassName' not in k8s_runner.job_manifest('x' * 36, s, 60)['spec']['template']['spec']


def test_dispatcher_creates_waits_and_removes_the_job(monkeypatch):
    seen, polls = [], iter([{}, {'active': 1}, {'succeeded': 1}])

    def handler(req):
        seen.append((req.method, req.url.path))
        if req.method == 'POST':
            body = json.loads(req.content)
            assert body['kind'] == 'Job' and req.headers['authorization'] == 'Bearer sa-token'
            return httpx.Response(201, json=body)
        if req.method == 'GET':
            return httpx.Response(200, json={'status': next(polls)})
        return httpx.Response(200, json={})
    monkeypatch.setattr(get_settings(), 'k8s_image', 'img')
    api = k8s_runner.Api('https://k8s.test', token='sa-token', verify=False, transport=httpx.MockTransport(handler))
    jid = '11111111-2222-4333-8444-555555555555'
    assert k8s_runner.run_in_pod(jid, api, poll=0) == 'succeeded'
    name = k8s_runner.job_name(jid)
    assert seen[0] == ('POST', '/apis/batch/v1/namespaces/migration-factory/jobs')
    assert seen[-1] == ('DELETE', f'/apis/batch/v1/namespaces/migration-factory/jobs/{name}')   # nothing left behind


def test_maven_goes_through_the_egress_proxy(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), 'maven_proxy', 'mf-egress-proxy:3128')
    xml = mavenops.settings_file(runner.Context('j', tmp_path, deadline=1e12)).read_text()
    assert '<protocol>https</protocol><host>mf-egress-proxy</host><port>3128</port>' in xml
    monkeypatch.setattr(get_settings(), 'maven_proxy', 'evil"><x')
    assert '<proxies>' not in mavenops.settings_file(runner.Context('j', tmp_path, deadline=1e12)).read_text()
