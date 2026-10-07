import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_alembic_upgrade_matches_models(tmp_path):
    env = os.environ | {'MF_DATABASE_URL': os.environ.get('MF_TEST_MIGRATION_URL', f'sqlite:///{tmp_path}/m.db')}
    run = lambda *a: subprocess.run([sys.executable, '-m', 'alembic', *a], cwd=BACKEND, env=env, capture_output=True, text=True)
    up = run('upgrade', 'head')
    assert up.returncode == 0, up.stderr
    check = run('check')
    assert check.returncode == 0 and 'No new upgrade operations' in check.stdout + check.stderr, check.stdout + check.stderr
    assert run('downgrade', 'base').returncode == 0
