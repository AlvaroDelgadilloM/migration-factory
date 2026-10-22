"""MF_EXECUTION_MODE=k8s: every platform job runs in its own ephemeral Kubernetes pod (a batch/v1 Job), not in the long-lived
worker. The worker only dispatches: it creates the Job, waits for it and removes it; the pod claims the job with the same
lease/heartbeat/cancel logic as always (mf.worker.run_one), so retries and cancellation do not change.

Isolation of the pod (built from the settings, see job_manifest): runtimeClassName (gVisor by default: a user-space kernel
between untrusted Maven plugins and the node), non-root uid 10001, read-only root filesystem, no privilege escalation, all
capabilities dropped, RuntimeDefault seccomp, no service-account token, CPU/memory/ephemeral-storage limits, per-job emptyDir
for /work, /tmp and the Maven cache (nothing survives the job), deadline and TTL. Egress is restricted by the NetworkPolicy in
deploy/k8s (DNS, PostgreSQL, Redis and an egress proxy that only allows the approved repositories). Artifacts go to the S3
backend (MF_ARTIFACT_BACKEND=s3): there is no volume shared between pods. Secrets come from a Secret (envFrom), never from
the manifest."""
from __future__ import annotations

import time
from pathlib import Path

SA = Path('/var/run/secrets/kubernetes.io/serviceaccount')
LABEL = 'migration-factory/build-runner'


def job_name(job_id: str) -> str:
    return f'mf-job-{job_id[:8]}-{job_id[-8:]}'


def job_manifest(job_id: str, s, deadline: int) -> dict:
    tmp = lambda name, mount, size: ({'name': name, 'emptyDir': {'sizeLimit': size}}, {'name': name, 'mountPath': mount})  # noqa: E731
    vols = [tmp('work', '/work', s.k8s_work_size), tmp('tmp', '/tmp', '1Gi'), tmp('m2', '/m2', s.k8s_m2_size),
            tmp('home', '/home/mf', '256Mi')]
    pod = {
        'restartPolicy': 'Never',
        'automountServiceAccountToken': False,
        'enableServiceLinks': False,
        'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001, 'fsGroup': 10001,
                            'seccompProfile': {'type': 'RuntimeDefault'}},
        'containers': [{
            'name': 'runner', 'image': s.k8s_image, 'imagePullPolicy': 'IfNotPresent',
            'command': ['python', '-m', 'mf.worker.run_one', job_id],
            'env': [{'name': 'MF_EXECUTION_MODE', 'value': 'process'}, {'name': 'MF_WORK_DIR', 'value': '/work'},
                    {'name': 'MF_MAVEN_REPO_LOCAL', 'value': '/m2'}, {'name': 'HOME', 'value': '/home/mf'}],
            'envFrom': [{'secretRef': {'name': s.k8s_env_secret}}],
            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'privileged': False,
                                'capabilities': {'drop': ['ALL']}},
            'resources': {'requests': {'cpu': '500m', 'memory': '1Gi'},
                          'limits': {'cpu': s.k8s_cpu, 'memory': s.k8s_memory, 'ephemeral-storage': s.k8s_work_size}},
            'volumeMounts': [m for _, m in vols],
        }],
        'volumes': [v for v, _ in vols],
    }
    if s.k8s_runtime_class:
        pod['runtimeClassName'] = s.k8s_runtime_class
    labels = {'app.kubernetes.io/name': 'migration-factory', 'app.kubernetes.io/component': 'build-runner', LABEL: 'true'}
    return {'apiVersion': 'batch/v1', 'kind': 'Job',
            'metadata': {'name': job_name(job_id), 'namespace': s.k8s_namespace, 'labels': labels | {'migration-factory/job-id': job_id}},
            'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': deadline, 'ttlSecondsAfterFinished': 600,
                     'template': {'metadata': {'labels': labels}, 'spec': pod}}}


class Api:
    """In-cluster Kubernetes API with the dispatcher's service account (Role: create/get/delete Jobs in its namespace)."""
    def __init__(self, base='https://kubernetes.default.svc', token=None, verify=None, transport=None):
        import httpx
        self.client = httpx.Client(base_url=base, timeout=30, transport=transport,
                                   verify=verify if verify is not None else str(SA / 'ca.crt'),
                                   headers={'Authorization': f"Bearer {token or (SA / 'token').read_text().strip()}"})

    def create(self, manifest):
        ns = manifest['metadata']['namespace']
        r = self.client.post(f'/apis/batch/v1/namespaces/{ns}/jobs', json=manifest)
        if r.status_code not in (201, 409):  # 409: already created by a previous dispatch of the same job
            raise RuntimeError(f'Kubernetes rechazó el Job ({r.status_code}): {r.text[:300]}')

    def status(self, ns, name) -> dict:
        r = self.client.get(f'/apis/batch/v1/namespaces/{ns}/jobs/{name}')
        r.raise_for_status()
        return r.json().get('status') or {}

    def delete(self, ns, name):
        self.client.delete(f'/apis/batch/v1/namespaces/{ns}/jobs/{name}', params={'propagationPolicy': 'Background'})


def run_in_pod(job_id: str, api: Api | None = None, poll: float = 5) -> str:
    """Create the pod Job for job_id and wait until it finishes; the platform state is written by the pod itself.
    Returns 'succeeded' | 'failed' (pod-level outcome, for the dispatcher log)."""
    from ..config import get_settings
    s = get_settings()
    deadline = (s.workspace_timeout_seconds if s.k8s_long_jobs else s.job_timeout_seconds) + 300
    api = api or Api()
    m = job_manifest(job_id, s, deadline)
    ns, name = m['metadata']['namespace'], m['metadata']['name']
    api.create(m)
    end = time.monotonic() + deadline + 120
    try:
        while time.monotonic() < end:
            st = api.status(ns, name)
            if st.get('succeeded'):
                return 'succeeded'
            if st.get('failed'):
                return 'failed'
            time.sleep(poll)
        return 'failed'
    finally:
        api.delete(ns, name)
