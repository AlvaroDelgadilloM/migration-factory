"""Product tests (API + worker). They do not prove equivalence of any migrated application.

By default they use SQLite; set MF_TEST_DATABASE_URL=postgresql+psycopg://... to run on PostgreSQL.
Maven is replaced by fakes here; the real Maven/OpenRewrite path is exercised by scripts/pilot.py.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix='mf-tests-'))
os.environ.update({
    'MF_ENV': 'test',
    'MF_DATABASE_URL': os.environ.get('MF_TEST_DATABASE_URL', f'sqlite:///{TMP}/test.db'),
    'MF_AUTH_MODE': 'dev',
    'MF_DEV_JWT_SECRET': 'test-secret-' + 'x' * 40,
    'MF_ARTIFACT_DIR': str(TMP / 'artifacts'),
    'MF_WORK_DIR': str(TMP / 'work'),
    'MF_LOCAL_SOURCE_ROOTS': str(TMP / 'sources'),
    'MF_REDIS_URL': 'redis://127.0.0.1:1/0',  # unreachable on purpose: outbox rows stay pending
    'MF_LEASE_SECONDS': '6',
})
sys.path.insert(0, str(ROOT / 'backend'))

from fastapi.testclient import TestClient  # noqa: E402

from mf import db as dbmod  # noqa: E402
from mf.models import Base  # noqa: E402
from mf.profiles import seed  # noqa: E402
from mf.worker import fixtures  # noqa: E402


@pytest.fixture(scope='session', autouse=True)
def database():
    engine = dbmod.engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with dbmod.session() as s:
        seed(s)
    fixtures.build(ROOT / 'examples' / 'pilot-orders', TMP / 'repos' / 'pilot-orders.git')
    shutil.copytree(ROOT / 'examples' / 'pilot-orders', TMP / 'sources' / 'pilot-orders')
    yield
    shutil.rmtree(TMP, ignore_errors=True)


@pytest.fixture(scope='session')
def pilot_dir():
    """Authorized local folder (copy of the synthetic pilot)."""
    return str(TMP / 'sources' / 'pilot-orders')


@pytest.fixture(scope='session')
def pilot_repo(pilot_dir):
    """Kept for compatibility: tests now use a local folder source by default."""
    return pilot_dir


@pytest.fixture
def git_source(monkeypatch):
    """A local bare Git repo used through the git source type. file:// is NOT accepted by the product;
    the test swaps URL validation so the real clone/checkout code path runs offline."""
    from mf.api import projects
    from mf.worker import gitops
    url = f"file://{TMP / 'repos' / 'pilot-orders.git'}"
    fake = lambda u, *a, **k: {'provider': 'generic', 'url': u, 'host': None}
    monkeypatch.setattr(projects, 'validate_git_url', fake)
    monkeypatch.setattr(gitops, 'validate_git_url', fake)
    orig = gitops._git
    monkeypatch.setattr(gitops, '_git', lambda ctx, args, cwd, name, timeout=600, env=None:
                        orig(ctx, ['-c', 'protocol.file.allow=always'] + args, cwd, name, timeout, env))
    return url


@pytest.fixture(scope='session')
def client():
    from mf.main import app
    return TestClient(app)


@pytest.fixture(scope='session')
def token(client):
    cache = {}

    def get(user):
        if user not in cache:
            r = client.post('/api/v1/auth/dev-login', json={'username': user})
            assert r.status_code == 200, r.text
            cache[user] = {'Authorization': f"Bearer {r.json()['accessToken']}"}
        return cache[user]
    return get


def git_head(bare: Path) -> str:
    return subprocess.run(['git', '--git-dir', str(bare), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
