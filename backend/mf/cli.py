"""Operational commands: python -m mf.cli init | demo-project"""
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

from .config import get_settings
from .db import session


def init():
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=Path(__file__).resolve().parents[1], check=True)
    from .profiles import seed
    with session() as s:
        seed(s)
    print('schema migrated, profiles and rule catalog seeded')


def demo_project():
    """Registers the synthetic pilot repository as a demo project (dev mode only; idempotent)."""
    from .api.projects import default_org
    from .models import Membership, Project, Repository
    s_ = get_settings()
    from .security import local_roots
    roots = local_roots()
    folder = next((r / 'pilot-orders' for r in roots if (r / 'pilot-orders' / 'pom.xml').exists()), None)
    if s_.auth_mode != 'dev' or folder is None:
        print('demo-project needs MF_AUTH_MODE=dev and pilot-orders inside MF_LOCAL_SOURCE_ROOTS')
        return
    with session() as db:
        if db.scalar(select(Project).where(Project.name == 'Demo Orders (fixture sintética)')):
            print('demo project already exists')
            return
        repo = Repository(source_type='local', provider='local', local_path=str(folder))
        db.add(repo)
        db.flush()
        p = Project(organization_id=default_org(db).id, name='Demo Orders (fixture sintética)', repository_id=repo.id,
                    description='Proyecto sintético de demostración: no contiene código de cliente.',
                    target_runtime='spring', created_by='ana.arquitecta', default_branch='-')
        db.add(p)
        db.flush()
        for sub, role in (('ana.arquitecta', 'owner'), ('diego.dev', 'developer'), ('rosa.revisora', 'reviewer'), ('aldo.auditor', 'auditor')):
            db.add(Membership(project_id=p.id, subject_id=sub, role=role))
        db.commit()
        print(f'demo project created: {p.id}')


if __name__ == '__main__':
    {'init': init, 'demo-project': demo_project}[sys.argv[1]]()
