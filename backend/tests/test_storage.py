"""Artifact store backends: local volume (default) and S3-compatible object storage (MinIO, AWS S3)."""
import io
import os
import uuid
import zipfile
from pathlib import Path

import pytest

from mf import storage
from mf.config import get_settings

API = '/api/v1'


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = Body

    def upload_file(self, src, bucket, key):
        self.objects[(bucket, key)] = Path(src).read_bytes()

    def get_object(self, Bucket, Key):
        return {'Body': io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


@pytest.fixture(autouse=True)
def fresh_backend():
    storage.blobs.cache_clear()
    yield
    storage.blobs.cache_clear()  # the next tests get the local backend again


def s3_backend(monkeypatch, client=None, **settings):
    """The process-wide backend (every module calls the same cached blobs()) switched to S3; `client` replaces boto3's."""
    s = get_settings()
    for k, v in ({'artifact_backend': 's3', 's3_bucket': 'mf-test', 's3_prefix': 'mf', 's3_endpoint_url': 'http://127.0.0.1:9'} | settings).items():
        monkeypatch.setattr(s, k, v)
    storage.blobs.cache_clear()
    b = storage.blobs()
    if client:
        b.client = client
    return b


def test_s3_keys_are_prefixed_and_cannot_escape(monkeypatch):
    fake = FakeS3()
    b = s3_backend(monkeypatch, fake)
    b.write('p/1/report.json', b'{}')
    assert fake.objects == {('mf-test', 'mf/p/1/report.json'): b'{}'} and b.read('p/1/report.json') == b'{}'
    with pytest.raises(ValueError):
        b.read('../other-tenant/x')
    b.delete('p/1/report.json')
    assert not fake.objects


def test_upload_artifacts_and_project_deletion_use_the_s3_backend(client, token, monkeypatch, tmp_path):
    """Everything the api and the workers share goes through the backend: no shared volume needed with S3."""
    endpoint = os.environ.get('MF_S3_TEST_ENDPOINT')
    if endpoint:  # real MinIO / S3: MF_S3_TEST_ENDPOINT, MF_S3_TEST_ACCESS_KEY, MF_S3_TEST_SECRET_KEY, bucket mf-test created here
        b = s3_backend(monkeypatch, s3_endpoint_url=endpoint, s3_region='us-east-1', s3_access_key=os.environ['MF_S3_TEST_ACCESS_KEY'],
                       s3_secret_key=os.environ['MF_S3_TEST_SECRET_KEY'], s3_prefix=f'run-{uuid.uuid4().hex[:8]}')
        try:
            b.client.create_bucket(Bucket='mf-test')
        except b.client.exceptions.BucketAlreadyOwnedByYou:
            pass
        listed = lambda: [o['Key'] for o in b.client.list_objects_v2(Bucket='mf-test', Prefix=b.prefix).get('Contents', [])]  # noqa: E731
    else:
        fake = FakeS3()
        b = s3_backend(monkeypatch, fake)
        listed = lambda: [k for _, k in fake.objects]  # noqa: E731
    fix = Path(__file__).parent / 'fixtures' / 'datastage' / 'shop_flow.dsx'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.write(fix, 'x/shop_flow.dsx')
    hdr = token('ana.arquitecta') | {'Content-Type': 'application/zip', 'X-Filename': 'dsx.zip', 'X-Migration-Type': 'AUTO'}
    up = client.post(f'{API}/uploads', content=buf.getvalue(), headers=hdr)
    assert up.status_code == 201, up.text
    assert [k for k in listed() if '/uploads/' in k or k.startswith('uploads/')], listed()
    assert not list((Path(get_settings().artifact_dir) / 'uploads').glob(f"{up.json()['id'].replace('-', '')}*"))  # nothing on the volume
    name = f'S3 {tmp_path.name[-6:]}'
    pid = client.post(f'{API}/projects', headers=token('ana.arquitecta'), json={
        'name': name, 'sourceType': 'zip', 'uploadId': up.json()['id'], 'targetRuntime': 'airflow',
        'migrationType': 'DATASTAGE_AIRFLOW'}).json()['id']
    from helpers import run_job
    start = client.post(f'{API}/projects/{pid}/datastage-migrations', headers=token('ana.arquitecta'), json={}).json()
    run_job(start['jobId'])                                    # the worker reads the ZIP and stores the artifacts in S3
    dm = client.get(f"{API}/datastage-migrations/{start['dataStageMigrationId']}", headers=token('ana.arquitecta')).json()
    assert dm['state'] == 'partial_success', dm.get('job')
    art_id = next(iter(dm['summary']['artifacts'].values()))
    got = client.get(f'{API}/artifacts/{art_id}/download', headers=token('ana.arquitecta'))
    assert got.status_code == 200 and got.content
    assert len(listed()) > 3
    assert client.delete(f'{API}/projects/{pid}', headers=token('ana.arquitecta'), params={'confirm': name}).status_code == 204
    assert listed() == []                                      # project deletion removes its objects and its upload
