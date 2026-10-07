"""Controlled subprocess execution for untrusted builds (Maven runs repository plugins).

- argument list only (shell=False); executables resolved from a fixed PATH
- new session / process group, killed as a whole on cancel or timeout (SIGTERM, then SIGKILL)
- clean environment: no database/redis credentials or tokens are inherited
- rlimits on Linux (CPU, file size, open files, no core dumps)
- output captured to a file, redacted before it reaches events or artifacts
"""
import os
import resource
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..security import redact

SAFE_PATH = '/opt/maven/bin:/opt/java/openjdk/bin:/usr/local/bin:/usr/bin:/bin'


class Cancelled(Exception):
    pass


class TimedOut(Exception):
    pass


@dataclass
class Context:
    """Per-job execution context shared by tasks and the heartbeat thread."""
    job_id: str
    workdir: Path
    deadline: float
    cancel: threading.Event = field(default_factory=threading.Event)
    secrets: list = field(default_factory=list)       # extra values to redact (e.g. git token)
    emit: callable = None                              # emit(level, phase, message)
    phase: str = ''

    def remaining(self):
        return self.deadline - time.monotonic()

    def check(self):
        if self.cancel.is_set():
            raise Cancelled()
        if self.remaining() <= 0:
            raise TimedOut()


@dataclass
class Result:
    exit_code: int
    duration_ms: int
    log_path: Path

    def log_text(self, ctx, limit=2_000_000):
        data = self.log_path.read_bytes()[-limit:].decode('utf-8', 'replace')
        return redact(data, ctx.secrets)


def _limits(pid, cpu_seconds):
    if not hasattr(resource, 'prlimit'):  # macOS (local tests): no per-pid limits
        return
    for res, val in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_FSIZE, 2 * 1024 ** 3),
                     (resource.RLIMIT_NOFILE, 8192), (resource.RLIMIT_CPU, cpu_seconds)):
        try:
            resource.prlimit(pid, res, (val, val))
        except (OSError, ValueError):
            pass


def _exited_unreaped(pid, timeout) -> bool:
    """Wait until the leader exits WITHOUT reaping it: its zombie keeps the process-group id reserved, so
    the final group SIGKILL can never hit an unrelated process that reused the id."""
    if not hasattr(os, 'waitid'):  # macOS: cannot wait without reaping; give a short grace instead
        time.sleep(min(timeout, 2))
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                return True
        except ChildProcessError:
            return True
        time.sleep(0.1)
    return False


def kill_group(proc: subprocess.Popen, grace=10):
    pgid = proc.pid  # start_new_session=True: the leader's pid is the group id
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 10)):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            break
        if _exited_unreaped(proc.pid, wait):
            try:
                os.killpg(pgid, signal.SIGKILL)  # stragglers (e.g. forked JVMs); group id still ours
            except (ProcessLookupError, PermissionError):
                pass
            break
    proc.wait()


def run(args, cwd, ctx: Context, *, name, timeout, env=None, stream=True) -> Result:
    if not isinstance(args, (list, tuple)) or not all(isinstance(a, str) for a in args):
        raise TypeError('args must be a list of strings')
    ctx.check()
    timeout = max(1, min(timeout, ctx.remaining()))
    log_path = ctx.workdir / 'logs' / f'{name}.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    from ..config import get_settings
    base_env = {'PATH': get_settings().tool_path or SAFE_PATH, 'HOME': str(ctx.workdir / 'home'), 'LANG': 'C.UTF-8', 'TMPDIR': str(ctx.workdir / 'tmp'),
                'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1'}
    for k in ('JAVA_HOME', 'MAVEN_HOME'):
        if os.environ.get(k):
            base_env[k] = os.environ[k]
    (ctx.workdir / 'home').mkdir(exist_ok=True)
    (ctx.workdir / 'tmp').mkdir(exist_ok=True)
    started = time.monotonic()
    with open(log_path, 'wb') as out:
        proc = subprocess.Popen(list(args), cwd=str(cwd), env=base_env | (env or {}), stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True, shell=False)
    _limits(proc.pid, int(timeout) * (os.cpu_count() or 2))
    pos, last_emit = 0, 0.0
    try:
        while True:
            try:
                proc.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                pass
            if ctx.cancel.is_set():
                kill_group(proc)
                raise Cancelled()
            if time.monotonic() - started > timeout:
                kill_group(proc)
                raise TimedOut()
            if stream and ctx.emit and time.monotonic() - last_emit > 2:
                pos = _stream(log_path, pos, ctx)
                last_emit = time.monotonic()
    except BaseException:
        if proc.poll() is None:
            kill_group(proc)
        raise
    if stream and ctx.emit:
        _stream(log_path, pos, ctx)
    return Result(proc.returncode, int((time.monotonic() - started) * 1000), log_path)


def _stream(log_path, pos, ctx, max_lines=40):
    with open(log_path, 'rb') as f:
        f.seek(pos)
        chunk = f.read(256_000)
        pos += len(chunk)
    lines = [l for l in chunk.decode('utf-8', 'replace').splitlines() if l.strip()]
    if len(lines) > max_lines:  # keep events small: summarize, full log goes to an artifact
        ctx.emit('info', ctx.phase, f'… {len(lines) - max_lines} líneas omitidas (ver log completo)')
        lines = lines[-max_lines:]
    for line in lines:
        ctx.emit('info', ctx.phase, redact(line[:500], ctx.secrets))
    return pos
