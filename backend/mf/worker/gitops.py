"""Git operations inside the job workspace. The source repository is only ever read (clone/fetch)."""
import os
import re
import stat
from pathlib import Path

from ..config import get_settings
from ..security import UnsafeInput, symlink_escapes, validate_git_url
from . import runner

SHA = re.compile(r'^[0-9a-f]{40}$')


class GitError(Exception):
    pass


def _git(ctx, args, cwd, name, timeout=600, env=None):
    base = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always',
            '-c', 'http.followRedirects=false', '-c', 'core.symlinks=true', '-c', 'safe.directory=*',
            '-c', 'user.name=Migration Factory', '-c', 'user.email=migration-factory@localhost', '-c', 'commit.gpgsign=false']
    res = runner.run(base + args, cwd, ctx, name=name, timeout=timeout, env=env, stream=False)
    out = res.log_text(ctx)
    if res.exit_code != 0:
        raise GitError(f'git {args[0]} falló (exit {res.exit_code}): {out[-800:]}')
    return out


def _askpass(ctx, token):
    """GIT_ASKPASS helper: the token is passed by environment to git only, never in the URL or logs."""
    if not token:
        return {}
    ctx.secrets.append(token)
    script = ctx.workdir / 'askpass.sh'
    script.write_text('#!/bin/sh\ncase "$1" in Username*) echo x-access-token ;; *) echo "$MF_GIT_TOKEN" ;; esac\n')
    script.chmod(stat.S_IRWXU)
    return {'GIT_ASKPASS': str(script), 'MF_GIT_TOKEN': token}


def checkout(ctx, url, ref, dest: Path, token=None) -> str:
    """Clone `url` and check out `ref` (branch, tag or full SHA) detached. Returns the commit SHA."""
    try:
        validate_git_url(url)  # re-validated at use time (host allowlist + DNS)
    except UnsafeInput as e:
        raise GitError(f'URL rechazada: {e}') from None
    env = _askpass(ctx, token)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(ctx, ['clone', '--no-checkout', '--quiet', '--', url, str(dest)], ctx.workdir, 'git-clone', env=env)
    if SHA.match(ref):
        target = ref
    else:
        if not re.fullmatch(r'[\w./-]{1,200}', ref) or '..' in ref:
            raise GitError('Referencia inválida')
        target = f'origin/{ref}'
        try:
            _git(ctx, ['rev-parse', '--verify', '--quiet', f'{target}^{{commit}}'], dest, 'git-rev-parse')
        except GitError:
            target = f'refs/tags/{ref}'
    _git(ctx, ['checkout', '--quiet', '--detach', target], dest, 'git-checkout')
    sha = head(ctx, dest)
    if SHA.match(ref) and sha != ref:
        raise GitError('El commit obtenido no coincide con el solicitado')
    bad = symlink_escapes(dest)
    if bad:
        raise GitError(f'El repositorio contiene enlaces simbólicos que salen del checkout: {bad[:5]}')
    size = sum(f.stat().st_size for f in dest.rglob('*') if f.is_file() and not f.is_symlink())
    if size > get_settings().max_repo_mb * 1024 * 1024:
        raise GitError('Repositorio supera el tamaño máximo permitido')
    exclude = dest / '.git' / 'info' / 'exclude'
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with open(exclude, 'a') as f:
        f.write('\ntarget/\n')
    return sha


def head(ctx, repo) -> str:
    return _git(ctx, ['rev-parse', 'HEAD'], repo, 'git-head').strip().splitlines()[-1]


def commit_all(ctx, repo, message) -> str | None:
    _git(ctx, ['add', '-A'], repo, 'git-add')
    status = _git(ctx, ['status', '--porcelain'], repo, 'git-status')
    if not status.strip():
        return None
    _git(ctx, ['commit', '--quiet', '--no-verify', '-m', message], repo, 'git-commit')
    return head(ctx, repo)


def reset_hard(ctx, repo, sha):
    _git(ctx, ['reset', '--quiet', '--hard', sha], repo, 'git-reset')
    _git(ctx, ['clean', '-fdq'], repo, 'git-clean')  # untracked, keeps ignored target/


def changed_files(ctx, repo, a, b) -> list[str]:
    out = _git(ctx, ['diff', '--name-only', '-z', a, b], repo, 'git-names')
    return [x for x in out.split('\0') if x.strip()]


def diff_files(ctx, repo, base, tip):
    """[(path, change_type, additions, deletions, patch)] between two commits."""
    status = _git(ctx, ['diff', '--no-color', '--no-ext-diff', '--name-status', '-z', '-M', base, tip], repo, 'git-name-status')
    parts = [p for p in status.split('\0') if p != '']
    entries, i = [], 0
    while i < len(parts):
        code = parts[i]
        if code.startswith(('R', 'C')):
            entries.append(('renamed', parts[i + 2])); i += 3
        else:
            entries.append(({'A': 'added', 'D': 'deleted'}.get(code[0], 'modified'), parts[i + 1])); i += 2
    numstat = {}
    for line in _git(ctx, ['diff', '--numstat', '-M', base, tip], repo, 'git-numstat').splitlines():
        cols = line.split('\t')
        if len(cols) == 3:
            numstat[cols[2]] = (int(cols[0]) if cols[0].isdigit() else 0, int(cols[1]) if cols[1].isdigit() else 0)
    out = []
    for kind, path in entries:
        patch = _git(ctx, ['diff', '--no-color', '--no-ext-diff', '-M', base, tip, '--', path], repo, 'git-diff-file')
        a, d = next((v for k, v in numstat.items() if k == path or k.endswith(path)), (0, 0))
        out.append((path, kind, a, d, patch[:500_000]))
    return out


def full_patch(ctx, repo, base, tip) -> str:
    return _git(ctx, ['diff', '--no-color', '--no-ext-diff', '-M', base, tip], repo, 'git-full-diff')



def worktree(ctx, repo, sha, dest: Path):
    _git(ctx, ['worktree', 'add', '--detach', '--quiet', str(dest), sha], repo, 'git-worktree')


def push_branch(ctx, repo, url, branch, token):
    if not re.fullmatch(r'mf/[\w.-]{1,100}', branch):
        raise GitError('Nombre de rama no permitido')
    env = _askpass(ctx, token)
    # Explicit refspec to a new branch; never force, never the default branch.
    _git(ctx, ['push', '--quiet', url, f'HEAD:refs/heads/{branch}'], repo, 'git-push', env=env)


FIXED_ENV = {'GIT_AUTHOR_DATE': '2000-01-01T00:00:00Z', 'GIT_COMMITTER_DATE': '2000-01-01T00:00:00Z'}


def init_snapshot_repo(ctx, root) -> str:
    """Internal, deterministic repository over a restored snapshot (a tool for per-step commits/diffs, not the
    user's VCS). Same content => same commit id."""
    _git(ctx, ['init', '--quiet', '--initial-branch=snapshot'], root, 'git-init')
    exclude = Path(root) / '.git' / 'info' / 'exclude'
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text('target/\n')
    _git(ctx, ['add', '-A'], root, 'git-add')
    _git(ctx, ['commit', '--quiet', '--no-verify', '--allow-empty', '-m', 'snapshot'], root, 'git-commit', env=FIXED_ENV)
    return head(ctx, root)


def apply_patch(ctx, repo, patch_path, directory):
    args = ['apply', '--index', '--whitespace=nowarn']
    if directory and directory != '.':
        args.append(f'--directory={directory}')
    _git(ctx, args + [str(patch_path)], repo, 'git-apply')



def os_safe_rmtree(path: Path):
    import shutil
    if path.exists():
        shutil.rmtree(path, onerror=lambda f, p, e: (os.chmod(p, stat.S_IWRITE), f(p)))
