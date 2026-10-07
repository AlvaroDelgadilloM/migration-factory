"""ArtifactStore: local filesystem implementation (S3/Blob adapter pending). Hash verified on write and read."""
import hashlib
import os
import tempfile
import uuid
from pathlib import Path

from .config import get_settings
from .models import Artifact
from .security import safe_join


class ArtifactStore:
    def __init__(self, base: Path | None = None):
        self.base = Path(base or get_settings().artifact_dir)
        self.base.mkdir(parents=True, exist_ok=True)

    def put(self, db, *, project_id, kind, name, data: bytes, content_type, scan_id=None, execution_id=None) -> Artifact:
        key = f'{project_id}/{uuid.uuid4().hex}/{Path(name).name}'
        target = safe_join(self.base, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(data).hexdigest()
        fd, tmp = tempfile.mkstemp(dir=target.parent)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:  # confirm before registering
            target.unlink(missing_ok=True)
            raise IOError('artifact hash mismatch after write')
        art = Artifact(project_id=project_id, scan_id=scan_id, execution_id=execution_id, kind=kind, name=Path(name).name,
                       object_key=key, sha256=digest, size_bytes=len(data), content_type=content_type)
        db.add(art)
        db.flush()
        return art

    def read(self, art: Artifact) -> bytes:
        data = safe_join(self.base, art.object_key).read_bytes()
        if hashlib.sha256(data).hexdigest() != art.sha256:
            raise IOError('artifact integrity check failed')
        return data

    def delete(self, art: Artifact):
        safe_join(self.base, art.object_key).unlink(missing_ok=True)
