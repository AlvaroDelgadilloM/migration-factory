"""ArtifactStore over a blob backend: local filesystem (default) or S3-compatible object storage (AWS S3, MinIO, Ceph...),
chosen by MF_ARTIFACT_BACKEND. Hash verified on write and read. Uploaded ZIPs use the same backend (key uploads/...)."""
import hashlib
import os
import shutil
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path

from .config import get_settings
from .models import Artifact
from .security import safe_join


class LocalBlobs:
    def __init__(self, base: Path):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)

    def write_file(self, key: str, src: Path):
        target = safe_join(self.base, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent)
        os.close(fd)
        shutil.copyfile(src, tmp)
        os.replace(tmp, target)

    def write(self, key: str, data: bytes):
        target = safe_join(self.base, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, target)

    def read(self, key: str) -> bytes:
        return safe_join(self.base, key).read_bytes()

    def delete(self, key: str):
        safe_join(self.base, key).unlink(missing_ok=True)


class S3Blobs:
    """Keys under MF_S3_PREFIX in MF_S3_BUCKET. MF_S3_ENDPOINT_URL for a non-AWS store (MinIO). Credentials from the settings
    (read once at start; the worker then removes them from its environment) or, when empty, the default AWS chain (IAM role)."""
    def __init__(self, s):
        import boto3
        self.bucket, self.prefix = s.s3_bucket, s.s3_prefix.strip('/')
        self.client = boto3.client('s3', endpoint_url=s.s3_endpoint_url or None, region_name=s.s3_region or None,
                                   aws_access_key_id=s.s3_access_key or None, aws_secret_access_key=s.s3_secret_key or None)

    def _key(self, key: str) -> str:
        if '..' in key.split('/') or key.startswith('/'):
            raise ValueError('clave de artefacto inválida')
        return f'{self.prefix}/{key}' if self.prefix else key

    def write_file(self, key: str, src: Path):
        self.client.upload_file(str(src), self.bucket, self._key(key))

    def write(self, key: str, data: bytes):
        self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data)

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._key(key))['Body'].read()

    def delete(self, key: str):
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))


@lru_cache
def blobs():
    s = get_settings()
    return S3Blobs(s) if s.artifact_backend == 's3' else LocalBlobs(s.artifact_dir)


class ArtifactStore:
    def __init__(self, base: Path | None = None):
        self.blobs = LocalBlobs(base) if base else blobs()

    def put(self, db, *, project_id, kind, name, data: bytes, content_type, scan_id=None, execution_id=None) -> Artifact:
        key = f'{project_id}/{uuid.uuid4().hex}/{Path(name).name}'
        digest = hashlib.sha256(data).hexdigest()
        self.blobs.write(key, data)
        if hashlib.sha256(self.blobs.read(key)).hexdigest() != digest:  # confirm before registering
            self.blobs.delete(key)
            raise IOError('artifact hash mismatch after write')
        art = Artifact(project_id=project_id, scan_id=scan_id, execution_id=execution_id, kind=kind, name=Path(name).name,
                       object_key=key, sha256=digest, size_bytes=len(data), content_type=content_type)
        db.add(art)
        db.flush()
        return art

    def read(self, art: Artifact) -> bytes:
        data = self.blobs.read(art.object_key)
        if hashlib.sha256(data).hexdigest() != art.sha256:
            raise IOError('artifact integrity check failed')
        return data

    def delete(self, art: Artifact):
        self.blobs.delete(art.object_key)
