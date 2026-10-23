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

DEFAULTS = {'users': 10, 'seconds': 60, 'warmup': 10, 'tolerance': 0.20, 'slowMs': 3000}


def probe(original: str, migrated: str, cases: list, slow_ms: int, timeout: int = 30) -> tuple[list, list]:
    """Each case once on both sides. A case slower than slow_ms on either side is waiting on something this test does not
    have (an external system that is simulated, unreachable or slow): it would measure that wait, not the migration. Those
    are left out and listed. Each kept case carries how each side answers it alone (`_ref`): under load, an error is a
    different answer than that (or none), not a 500 the service also gives alone. Returns (cases to load, excluded)."""
    keep, out = [], []
    for c in cases:
        times = {}
        for side, base in (('original', original), ('migrated', migrated)):
            hosts = (base.split('//', 1)[-1].rstrip('/'),)
            t0 = time.perf_counter()
            r = verify._send(base, c, timeout, hosts)
            times[side] = round((time.perf_counter() - t0) * 1000), r['status']
        slow = [f"{side} {ms} ms" for side, (ms, st) in times.items() if ms > slow_ms or st == 0]
        if slow:
            out.append({'name': c['name'], 'reason': 'lento o sin respuesta (' + ', '.join(slow) + '): depende de un sistema externo '
                                                     'que en esta prueba no responde a tiempo'})
        else:
            keep.append(dict(c, _ref={side: st for side, (_, st) in times.items()}))
    return keep, out


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
            m = re.search(r'"([\d.,\s\xa0]+)\s*KB?"', out)  # 'K' in English, 'KB' in Spanish Windows
            return round(int(re.sub(r'\D', '', m.group(1))) / 1024, 1) if m else None
        out = subprocess.run(['ps', '-o', 'rss=', '-p', str(pid)], capture_output=True, text=True, timeout=10).stdout.strip()
        return round(int(out) / 1024, 1) if out else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _send_kept(conn: list, base: str, case: dict, timeout: int) -> dict:
    """One request on the user's kept-alive connection (conn[0]); a broken or closed connection is reopened once.
    Only the status matters here: the comparison of bodies is the functional test's job."""
    import http.client
    from urllib.parse import urlsplit
    u = urlsplit(base)
    body = case.get('body')
    headers = dict(case.get('headers') or {})
    if body is not None:
        headers.setdefault('Content-Type', case.get('contentType', 'text/xml; charset=UTF-8'))
    for attempt in (1, 2):
        if conn[0] is None:
            conn[0] = (http.client.HTTPSConnection if u.scheme == 'https' else http.client.HTTPConnection)(u.hostname, u.port, timeout=timeout)
        try:
            conn[0].request(case.get('method', 'GET'), u.path.rstrip('/') + case['path'],
                            body=body.encode('utf-8') if body is not None else None, headers=headers)
            r = conn[0].getresponse()
            r.read()
            if r.getheader('Connection', '').lower() == 'close':
                conn[0].close()
                conn[0] = None
            return {'status': r.status}
        except (OSError, http.client.HTTPException):
            conn[0].close()
            conn[0] = None
            if attempt == 2:
                return {'status': 0}
    return {'status': 0}


def load(base: str, cases: list, users: int, seconds: int, warmup: int, timeout: int = 30, pid: int | None = None,
         log=lambda m: None, side: str | None = None) -> dict:
    """`users` threads send the cases round-robin for `warmup` + `seconds`; only the measured window counts. With `side` and
    cases from probe(), an error is an answer other than the one that side gives the case alone; without them, a 5xx or none."""
    lat, statuses, per_case, failed = [], Counter(), {}, Counter()
    lock, stop = threading.Lock(), threading.Event()
    measuring = threading.Event()
    mem = []

    def worker(i: int):
        n = i
        conn = [None]  # one kept-alive connection per user, as JMeter and Gatling do: a new connection per request ran
        while not stop.is_set():  # the load generator out of local ports (Windows TIME_WAIT) and read as "no answer"
            c = cases[n % len(cases)]
            n += users
            t0 = time.perf_counter()
            r = _send_kept(conn, base, c, timeout)
            ms = (time.perf_counter() - t0) * 1000
            if measuring.is_set() and not stop.is_set():
                with lock:
                    lat.append(ms)
                    statuses[r['status']] += 1
                    per_case.setdefault(c['name'], []).append(ms)
                    ref = (c.get('_ref') or {}).get(side) if side else None
                    if r['status'] == 0 or (r['status'] != ref if ref is not None else r['status'] >= 500):
                        failed[c['name']] += 1

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
    errors = sum(failed.values())
    slow = sorted(((name, _percentile(v, 95)) for name, v in per_case.items()), key=lambda x: -(x[1] or 0))[:10]
    return {'requests': total, 'throughput': round(total / seconds, 1) if seconds else None,
            'p50': _percentile(lat, 50), 'p95': _percentile(lat, 95), 'p99': _percentile(lat, 99), 'max': round(max(lat), 1) if lat else None,
            'errors': errors, 'errorRate': round(errors / total, 4) if total else None,
            'noResponse': statuses.get(0, 0), 'statuses': {str(k): v for k, v in sorted(statuses.items())},
            'failedCases': dict(failed.most_common(10)),
            'memoryMb': {'max': max(mem), 'avg': round(sum(mem) / len(mem), 1)} if mem else None, 'slowestCases': slow}


def compare(original: dict, migrated: dict, tolerance: float) -> dict:
    """FAIL: the migrated side answers less (more errors or no answer) or is much slower; REVIEW: within 2x the tolerance."""
    reasons = []
    if (migrated['errorRate'] or 0) > (original['errorRate'] or 0) + 0.01:
        reasons.append(f"más errores bajo carga: {migrated['errorRate']:.1%} contra {original['errorRate']:.1%} "
                       f"(respuestas distintas a las que da cada servicio sin carga, o sin respuesta)")
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
            ('p95', 'p95', ' ms'), ('p99', 'p99', ' ms'), ('Máximo', 'max', ' ms'), ('Errores bajo carga (respuesta distinta a la de sin carga, o ninguna)', 'errors', '')]
    out = ['# Prueba de rendimiento comparativa', '',
           f"Estado: **{result['comparison']['status']}** · {result['users']} usuarios concurrentes · {result['seconds']} s medidos "
           f"(+{result['warmup']} s de calentamiento) · {result['cases']} casos · tolerancia {result['comparison']['tolerance']:.0%}", '',
           '| Medida | Original | Migrado |', '|---|---|---|']
    out += [f'| {label} | {o[k]}{u} | {m[k]}{u} |' for label, k, u in rows]
    if o.get('memoryMb') and m.get('memoryMb'):
        out.append(f"| Memoria máxima del proceso | {o['memoryMb']['max']} MB | {m['memoryMb']['max']} MB |")
    out += [''] + ([f'- {r}' for r in result['comparison']['reasons']] or ['Sin regresiones dentro de la tolerancia.'])
    if result.get('excluded'):
        out += ['', f"## Casos fuera de la medición ({len(result['excluded'])} de {result['casesTotal']})", '',
                f"Tardan más de {result['slowMs']} ms o no responden en algún lado: miden la espera a un sistema externo, no la migración.", '']
        out += [f"- {x['name']}: {x['reason']}" for x in result['excluded']]
    out += ['', '## Alcance', '', result['scope'], '']
    return '\n'.join(out)


def jmeter_plan(cases: list, users: int, seconds: int) -> str:
    """The same cases as an Apache JMeter (5.x) test plan, for the client's environment: HOST, PORT and PROTOCOL are plan
    variables (also -JHOST=... on the command line), users and duration come from this test; each case is one HTTP request
    with its method, path, headers and body. Values of the client's matrix are added in JMeter (CSV Data Set)."""
    from xml.sax.saxutils import escape, quoteattr

    def prop(kind, name, value):
        return f'<{kind}Prop name="{name}">{escape(str(value))}</{kind}Prop>'

    samplers = []
    for c in cases:
        path, _, query = c['path'].partition('?')
        headers = dict(c.get('headers') or {})
        if c.get('body') is not None:
            headers.setdefault('Content-Type', c.get('contentType', 'text/xml; charset=UTF-8'))
        hdr = ''.join(f'<elementProp name={quoteattr(k)} elementType="Header">{prop("string", "Header.name", k)}{prop("string", "Header.value", v)}'
                      f'</elementProp>' for k, v in headers.items())
        body = ''
        if c.get('body') is not None:
            body = ('<boolProp name="HTTPSampler.postBodyRaw">true</boolProp><elementProp name="HTTPsampler.Arguments" elementType="Arguments">'
                    '<collectionProp name="Arguments.arguments"><elementProp name="" elementType="HTTPArgument">'
                    f'{prop("bool", "HTTPArgument.always_encode", "false")}{prop("string", "Argument.value", c["body"])}'
                    f'{prop("string", "Argument.metadata", "=")}</elementProp></collectionProp></elementProp>')
        samplers.append(
            f'<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname={quoteattr(c["name"])} enabled="true">'
            f'{body}{prop("string", "HTTPSampler.domain", "${HOST}")}{prop("string", "HTTPSampler.port", "${PORT}")}'
            f'{prop("string", "HTTPSampler.protocol", "${PROTOCOL}")}{prop("string", "HTTPSampler.path", path + ("?" + query if query else ""))}'
            f'{prop("string", "HTTPSampler.method", c.get("method", "GET"))}{prop("bool", "HTTPSampler.follow_redirects", "true")}'
            f'{prop("bool", "HTTPSampler.use_keepalive", "true")}</HTTPSamplerProxy><hashTree>'
            + (f'<HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="Encabezados" enabled="true">'
               f'<collectionProp name="HeaderManager.headers">{hdr}</collectionProp></HeaderManager><hashTree/>' if hdr else '')
            + '</hashTree>')
    variables = ''.join(f'<elementProp name="{k}" elementType="Argument">{prop("string", "Argument.name", k)}'
                        f'{prop("string", "Argument.value", "${__P(" + k + "," + v + ")}")}{prop("string", "Argument.metadata", "=")}</elementProp>'
                        for k, v in (('HOST', 'localhost'), ('PORT', '8080'), ('PROTOCOL', 'http')))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3"><hashTree>'
            '<TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="Migration Factory: prueba de rendimiento" enabled="true">'
            '<elementProp name="TestPlan.user_defined_variables" elementType="Arguments" guiclass="ArgumentsPanel" testclass="Arguments" '
            f'testname="Variables"><collectionProp name="Arguments.arguments">{variables}</collectionProp></elementProp></TestPlan><hashTree>'
            '<ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Usuarios concurrentes" enabled="true">'
            f'{prop("string", "ThreadGroup.num_threads", "${__P(USERS," + str(users) + ")}")}{prop("string", "ThreadGroup.ramp_time", "10")}'
            f'{prop("bool", "ThreadGroup.scheduler", "true")}{prop("string", "ThreadGroup.duration", "${__P(SECONDS," + str(seconds) + ")}")}'
            '<elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController">'
            f'{prop("bool", "LoopController.continue_forever", "false")}{prop("int", "LoopController.loops", "-1")}</elementProp>'
            f'{prop("string", "ThreadGroup.on_sample_error", "continue")}</ThreadGroup><hashTree>' + ''.join(samplers)
            + '<ResultCollector guiclass="SummaryReport" testclass="ResultCollector" testname="Resumen" enabled="true">'
            '<boolProp name="ResultCollector.error_logging">false</boolProp><stringProp name="filename"></stringProp></ResultCollector><hashTree/>'
            '</hashTree></hashTree></hashTree></jmeterTestPlan>\n')


def run(original: dict, migrated: dict, cases: list, out: Path, users: int, seconds: int, warmup: int, tolerance: float,
        mode: str, log=lambda m: None, slow_ms: int = DEFAULTS['slowMs']) -> dict:
    """original/migrated: {'url', 'pid'}. Writes reports/performance.json and .md, and a JMeter plan with the same cases."""
    reports = Path(out) / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    (reports / 'performance-plan.jmx').write_text(jmeter_plan(cases, users, seconds), encoding='utf-8')
    all_cases = len(cases)
    cases, excluded = probe(original['url'], migrated['url'], cases, slow_ms)
    log(f'rendimiento: {len(cases)} de {all_cases} casos responden en menos de {slow_ms} ms en los dos lados; '
        f'{len(excluded)} quedan fuera (esperan a un sistema externo)')
    if not cases:
        result = {'check': 'performance', 'users': users, 'seconds': seconds, 'warmup': warmup, 'cases': 0, 'casesTotal': all_cases,
                  'excluded': excluded, 'slowMs': slow_ms, 'original': None, 'migrated': None,
                  'comparison': {'status': 'NOT_EXECUTABLE', 'tolerance': tolerance,
                                 'reasons': ['Ningún caso responde a tiempo sin sistemas externos: hace falta el paquete de datos (mocks) o el ambiente de pruebas']},
                  'scope': 'Sin casos medibles.'}
        (reports / 'performance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        (reports / 'performance.md').write_text(f"# Prueba de rendimiento comparativa\n\nNo ejecutable: {result['comparison']['reasons'][0]}\n",
                                                encoding='utf-8')
        return result
    log(f'rendimiento: original ({len(cases)} casos)')
    a = load(original['url'], cases, users, seconds, warmup, pid=original.get('pid'), log=log, side='original')
    log('rendimiento: migrado')
    b = load(migrated['url'], cases, users, seconds, warmup, pid=migrated.get('pid'), log=log, side='migrated')
    result = {'check': 'performance', 'users': users, 'seconds': seconds, 'warmup': warmup, 'cases': len(cases), 'casesTotal': all_cases,
              'excluded': excluded, 'slowMs': slow_ms, 'original': a, 'migrated': b, 'comparison': compare(a, b, tolerance),
              'scope': ('Comparación en la misma máquina, uno después del otro, con los mismos casos'
                        + (' y los datos de prueba del cliente.' if mode == 'DATOS' else ', bases vacías y sistemas externos simulados (prueba de humo).')
                        + ' Detecta regresiones del migrado frente al original; no mide la capacidad de producción.')}
    (reports / 'performance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (reports / 'performance.md').write_text(markdown(result), encoding='utf-8')
    log(f"rendimiento: {result['comparison']['status']} · p95 original {a['p95']} ms, migrado {b['p95']} ms · "
        f"{a['throughput']} vs {b['throughput']} peticiones/s")
    return result
