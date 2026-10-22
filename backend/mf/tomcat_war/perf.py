"""Comparative load test (docs/19): the same cases, with the same concurrency and duration, against the original system and
then against the migrated one, one at a time so they do not compete for the machine.

What it proves: whether the migrated system is slower, fails under concurrency or uses more memory than the original on the
SAME machine and data. What it does not: production capacity (that needs the client's environment, data and volumes).
The load generator is http.client threads (no extra dependency); the cases are the ones the functional comparison already
sent, so only requests both systems answered are used."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path

from . import verify

DEFAULTS = {'users': 10, 'seconds': 60, 'warmup': 10, 'tolerance': 0.20}


def _percentile(values: list, p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))], 1)


def memory_mb(pid: int | None) -> float | None:
    """Resident memory of a process tree root (the JVM), from the OS tool: ps on Linux/macOS, tasklist on Windows."""
    if not pid:
        return None
    try:
        if os.name == 'nt':
            out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'], capture_output=True, text=True, timeout=10).stdout
            m = re.search(r'"([\d.,\s]+)\s*K"', out)
            return round(int(re.sub(r'\D', '', m.group(1))) / 1024, 1) if m else None
        out = subprocess.run(['ps', '-o', 'rss=', '-p', str(pid)], capture_output=True, text=True, timeout=10).stdout.strip()
        return round(int(out) / 1024, 1) if out else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def load(base: str, cases: list, users: int, seconds: int, warmup: int, timeout: int = 30, pid: int | None = None,
         log=lambda m: None) -> dict:
    """`users` threads send the cases round-robin for `warmup` + `seconds`; only the measured window counts."""
    hosts = tuple(sorted({base.split('//', 1)[-1].rstrip('/')}, key=len, reverse=True))
    lat, statuses, per_case = [], Counter(), {}
    lock, stop = threading.Lock(), threading.Event()
    measuring = threading.Event()
    mem = []

    def worker(i: int):
        n = i
        while not stop.is_set():
            c = cases[n % len(cases)]
            n += users
            t0 = time.perf_counter()
            r = verify._send(base, c, timeout, hosts)
            ms = (time.perf_counter() - t0) * 1000
            if measuring.is_set() and not stop.is_set():
                with lock:
                    lat.append(ms)
                    statuses[r['status']] += 1
                    per_case.setdefault(c['name'], []).append(ms)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(users)]
    for t in threads:
        t.start()
    time.sleep(warmup)
    measuring.set()
    log(f'midiendo {seconds}s con {users} usuarios concurrentes en {base}')
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        m = memory_mb(pid)
        if m is not None:
            mem.append(m)
        time.sleep(min(2, max(0.1, seconds - (time.monotonic() - t0))))
    stop.set()
    for t in threads:
        t.join(timeout + 5)
    total = len(lat)
    errors = statuses.get(0, 0) + sum(v for k, v in statuses.items() if k >= 500)
    slow = sorted(((name, _percentile(v, 95)) for name, v in per_case.items()), key=lambda x: -(x[1] or 0))[:10]
    return {'requests': total, 'throughput': round(total / seconds, 1) if seconds else None,
            'p50': _percentile(lat, 50), 'p95': _percentile(lat, 95), 'p99': _percentile(lat, 99), 'max': round(max(lat), 1) if lat else None,
            'errors': errors, 'errorRate': round(errors / total, 4) if total else None,
            'noResponse': statuses.get(0, 0), 'statuses': {str(k): v for k, v in sorted(statuses.items())},
            'memoryMb': {'max': max(mem), 'avg': round(sum(mem) / len(mem), 1)} if mem else None, 'slowestCases': slow}


def compare(original: dict, migrated: dict, tolerance: float) -> dict:
    """FAIL: the migrated side answers less (more errors or no answer) or is much slower; REVIEW: within 2x the tolerance."""
    reasons = []
    if (migrated['errorRate'] or 0) > (original['errorRate'] or 0) + 0.01:
        reasons.append(f"más errores: {migrated['errorRate']:.1%} contra {original['errorRate']:.1%}")
    for k in ('p95', 'p50'):
        a, b = original[k], migrated[k]
        if a and b and b > a * (1 + tolerance) and b - a > 5:  # below 5 ms the difference is noise
            reasons.append(f'{k} {b} ms contra {a} ms (+{(b / a - 1):.0%})')
    ta, tb = original['throughput'], migrated['throughput']
    if ta and tb and tb < ta * (1 - tolerance):
        reasons.append(f'rendimiento {tb} contra {ta} peticiones/s ({(tb / ta - 1):.0%})')
    worse_p95 = original['p95'] and migrated['p95'] and migrated['p95'] > original['p95'] * (1 + 2 * tolerance)
    status = 'PASS' if not reasons else ('FAIL' if worse_p95 or any(r.startswith('más errores') for r in reasons) else 'REVIEW')
    return {'status': status, 'tolerance': tolerance, 'reasons': reasons}


def markdown(result: dict) -> str:
    o, m = result['original'], result['migrated']
    rows = [('Peticiones medidas', 'requests', ''), ('Peticiones por segundo', 'throughput', ''), ('Mediana (p50)', 'p50', ' ms'),
            ('p95', 'p95', ' ms'), ('p99', 'p99', ' ms'), ('Máximo', 'max', ' ms'), ('Errores (5xx o sin respuesta)', 'errors', '')]
    out = ['# Prueba de rendimiento comparativa', '',
           f"Estado: **{result['comparison']['status']}** · {result['users']} usuarios concurrentes · {result['seconds']} s medidos "
           f"(+{result['warmup']} s de calentamiento) · {result['cases']} casos · tolerancia {result['comparison']['tolerance']:.0%}", '',
           '| Medida | Original | Migrado |', '|---|---|---|']
    out += [f'| {label} | {o[k]}{u} | {m[k]}{u} |' for label, k, u in rows]
    if o.get('memoryMb') and m.get('memoryMb'):
        out.append(f"| Memoria máxima del proceso | {o['memoryMb']['max']} MB | {m['memoryMb']['max']} MB |")
    out += [''] + ([f'- {r}' for r in result['comparison']['reasons']] or ['Sin regresiones dentro de la tolerancia.'])
    out += ['', '## Alcance', '', result['scope'], '']
    return '\n'.join(out)


def run(original: dict, migrated: dict, cases: list, out: Path, users: int, seconds: int, warmup: int, tolerance: float,
        mode: str, log=lambda m: None) -> dict:
    """original/migrated: {'url', 'pid'}. Writes reports/performance.json and .md."""
    log(f'rendimiento: original ({len(cases)} casos)')
    a = load(original['url'], cases, users, seconds, warmup, pid=original.get('pid'), log=log)
    log('rendimiento: migrado')
    b = load(migrated['url'], cases, users, seconds, warmup, pid=migrated.get('pid'), log=log)
    result = {'check': 'performance', 'users': users, 'seconds': seconds, 'warmup': warmup, 'cases': len(cases),
              'original': a, 'migrated': b, 'comparison': compare(a, b, tolerance),
              'scope': ('Comparación en la misma máquina, uno después del otro, con los mismos casos'
                        + (' y los datos de prueba del cliente.' if mode == 'DATOS' else ', bases vacías y sistemas externos simulados (prueba de humo).')
                        + ' Detecta regresiones del migrado frente al original; no mide la capacidad de producción.')}
    reports = Path(out) / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    (reports / 'performance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (reports / 'performance.md').write_text(markdown(result), encoding='utf-8')
    log(f"rendimiento: {result['comparison']['status']} · p95 original {a['p95']} ms, migrado {b['p95']} ms · "
        f"{a['throughput']} vs {b['throughput']} peticiones/s")
    return result
