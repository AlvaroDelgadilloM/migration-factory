"""Create read-only demo Git repositories from examples/ (pilot only). Deterministic commits => stable SHAs."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENV = {'GIT_AUTHOR_NAME': 'Migration Factory Demo', 'GIT_AUTHOR_EMAIL': 'demo@localhost', 'GIT_AUTHOR_DATE': '2026-10-01T00:00:00Z',
       'GIT_COMMITTER_NAME': 'Migration Factory Demo', 'GIT_COMMITTER_EMAIL': 'demo@localhost', 'GIT_COMMITTER_DATE': '2026-10-01T00:00:00Z',
       'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': tempfile.gettempdir(), 'GIT_CONFIG_NOSYSTEM': '1'}


def build(src: Path, dest: Path):
    if (dest / 'HEAD').exists():
        return
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / 'w'
        shutil.copytree(src, work, ignore=shutil.ignore_patterns('target', '.git'))
        run = lambda *a, cwd=work: subprocess.run(['git', '-c', 'init.defaultBranch=main', *a], cwd=cwd, env=ENV, check=True, capture_output=True)
        run('init', '-q')
        run('add', '-A')
        run('commit', '-q', '-m', f'Fixture sintética {src.name}')
        dest.parent.mkdir(parents=True, exist_ok=True)
        run('clone', '-q', '--bare', str(work), str(dest), cwd=tmp)


if __name__ == '__main__':
    examples, out = Path(sys.argv[1]), Path(sys.argv[2])
    for d in sorted(examples.iterdir()):
        if (d / 'pom.xml').exists():
            build(d, out / f'{d.name}.git')
            print(f'{d.name}: {out / (d.name + ".git")}')
