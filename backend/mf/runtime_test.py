"""Pruebas > Automáticas for a migrated project (Spring Boot jar, Tomcat WAR, Quarkus): the worker builds the candidate, starts it
in its own runtime with the variables the user gave, maps the endpoints Camel actually published (its startup log), and sends
the cases to it and, when given, to the original system (a running test environment) to compare.

Variables: every ${NAME} / ${NAME:default} of the candidate's configuration is listed with a hint of what it is for. Values that
look like secrets are only accepted as credential references (env:MF_CRED_* / vault:...), and every URL or JDBC URL must point
to a host in MF_TEST_ALLOWED_HOSTS (the test environment, never production). The app gets only these variables plus PATH/HOME:
nothing of the worker's environment."""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import unquote

VAR = re.compile(r'\$\{([A-Z][A-Z0-9_]{1,100})(?::([^}]*))?\}')
SECRET = re.compile(r'PASS|PWD|SECRET|TOKEN|KEY|CREDENTIAL', re.I)
CONFIG = ('application.yml', 'application.yaml', 'application.properties')
READY = re.compile(r'Apache Camel \S+ \([^)]*\) started')
ROUTE = re.compile(r'Started (\S+) \(([a-z0-9+-]+):([^)\s]*)\)')
TOMCAT = {'10': '10.1.48', '11': '11.0.27'}  # official distributions (org.apache.tomcat:tomcat:<v>:zip from the approved mirror)
DEPENDENCY_PLUGIN = 'org.apache.maven.plugins:maven-dependency-plugin:3.11.0'


class RuntimeTestError(ValueError):
    pass


def _flatten_yaml(text: str) -> dict:
    """Plain `key: value` YAML with nested maps (what the migration writes); lists and multi-line values are skipped."""
    out, stack = {}, []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith(('#', '-')):
            continue
        indent = len(raw) - len(raw.lstrip(' '))
        m = re.match(r'\s*([\w.\-\[\]"\']+)\s*:\s*(.*)$', raw)
        if not m:
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        key = '.'.join([k for _, k in stack] + [m[1].strip('"\'')])
        val = m[2].split(' #')[0].strip()
        if val:
            out[key] = val.strip('"\'')
        else:
            stack.append((indent, m[1].strip('"\'')))
    return out


def module(project: Path) -> Path:
    """The deployable module of a (possibly multi-module) project: the one with its own application configuration, else a
    war/Quarkus module, else the root."""
    poms = sorted((p.parent for p in project.rglob('pom.xml') if 'target' not in p.relative_to(project).parts), key=lambda d: len(d.parts))
    for d in poms:
        if any((d / 'src' / 'main' / 'resources' / n).is_file() for n in CONFIG):
            return d
    for d in poms:
        pom = (d / 'pom.xml').read_text(encoding='utf-8', errors='replace')
        if 'io.quarkus' in pom or re.search(r'<packaging>\s*war\s*</packaging>', pom):
            return d
    return project


def config(project: Path) -> dict:
    """key -> raw value of the main configuration files (src/main/resources)."""
    res, flat = project / 'src' / 'main' / 'resources', {}
    for name in CONFIG:
        f = res / name
        if not f.is_file():
            continue
        text = f.read_text(encoding='utf-8', errors='replace')
        if name.endswith('.properties'):
            from .modernization.props_yaml import parse_properties
            flat |= dict(parse_properties(text))
        else:
            flat |= _flatten_yaml(text)
    return flat


def runtime(project: Path) -> str:
    """project: the deployable module (module())."""
    pom = (project / 'pom.xml').read_text(encoding='utf-8', errors='replace') if (project / 'pom.xml').is_file() else ''
    if 'io.quarkus' in pom or 'camel-quarkus' in pom:
        return 'quarkus'
    return 'tomcat' if re.search(r'<packaging>\s*war\s*</packaging>', pom) else 'spring'


def _hint(key: str, name: str) -> str:
    k = key.lower()
    if 'datasource' in k or 'jdbc' in k:
        part = 'URL JDBC' if k.endswith('url') else 'usuario' if 'user' in k else 'contraseña' if 'pass' in k else key.rsplit('.', 1)[-1]
        return f'Base de datos de pruebas: {part} ({key})'
    if k.startswith('services.') and k.endswith('.url'):
        return f'URL del servicio externo de pruebas ({key})'
    if 'broker' in k or 'jms' in k or 'activemq' in k or 'artemis' in k:
        return f'Broker de mensajería de pruebas ({key})'
    return f'Valor de {key}' if key else name


def variables(project: Path) -> list[dict]:
    """Every ${NAME} the configuration needs: required (no default) or optional (with its default), with a hint and whether it
    is a secret (then only a credential reference is accepted)."""
    found = {}
    for key, value in config(project).items():
        for m in VAR.finditer(str(value)):
            name, default = m[1], m[2]
            v = found.setdefault(name, {'name': name, 'keys': [], 'required': default is None, 'default': default,
                                        'secret': bool(SECRET.search(name) or SECRET.search(key))})
            v['keys'].append(key)
            v['required'] = v['required'] and default is None
    for v in found.values():
        v['hint'] = _hint(v['keys'][0], v['name'])
    return sorted(found.values(), key=lambda v: (not v['required'], v['name']))


def base_path(kind: str, project: Path, war_name: str = '') -> str:
    """Where the Camel HTTP consumers are published: context + servlet mapping (Spring Boot), WAR name + servlet mapping
    (Tomcat: the context is the WAR, like the legacy server), root path (Quarkus platform-http)."""
    c = config(project)
    servlet = c.get('camel.servlet.mapping.context-path', '/camel/*').rstrip('*').rstrip('/')
    if kind == 'quarkus':  # servlet REST: quarkus.camel.servlet.url-patterns; platform-http: the root path
        return c.get('quarkus.http.root-path', '').rstrip('/') + c.get('quarkus.camel.servlet.url-patterns', '').split(',')[0].rstrip('*').rstrip('/')
    ctx = ('/' + war_name if war_name and war_name != 'ROOT' else '') if kind == 'tomcat' else c.get('server.servlet.context-path', '').rstrip('/')
    return ctx + servlet


def endpoints(log_text: str) -> list[dict]:
    """The HTTP consumers Camel started (route id, method, path relative to the Camel HTTP base), from its startup log."""
    out = []
    for route, scheme, rest in ROUTE.findall(log_text):
        rest = unquote(rest)
        if scheme == 'rest':  # rest://get:/customers:/{id}
            parts = rest.lstrip('/').split(':', 2)
            if len(parts) < 2:
                continue
            path = '/' + '/'.join(p.strip('/') for p in parts[1:] if p.strip('/'))
            out.append({'route': route, 'method': parts[0].upper(), 'path': path})
        elif scheme in ('servlet', 'platform-http'):
            out.append({'route': route, 'method': 'GET', 'path': '/' + rest.lstrip('/').split('?', 1)[0]})
    return out


def cases(eps: list[dict], params: dict) -> list[dict]:
    """GET endpoints become cases (path parameters from `params`, else 1); other methods need a body: given as extra cases."""
    out = []
    for e in eps:
        if e['method'] != 'GET':
            continue
        path = re.sub(r'\{(\w+)\}', lambda m: str(params.get(m[1], '1')), e['path'])
        out.append({'name': f"{e['route']} GET {e['path']}", 'method': 'GET', 'path': path, 'headers': {'Accept': 'application/json'}})
    return out


def check_value(value: str, allowed) -> None:
    """A URL or JDBC URL must point to an allowed test host (allowed: callable host -> bool)."""
    m = re.search(r'(?:jdbc:[\w:]+:(?:thin:@)?(?://)?|\w+://)(?:[^@/\s]*@)?\[?([\w.\-:]+?)\]?(?::\d+)?(?:[/;?]|$)', value)
    if m and not allowed(m[1]):
        raise RuntimeTestError(f'{m[1]} no está en MF_TEST_ALLOWED_HOSTS: solo ambientes de prueba autorizados')


def resolve(spec: list[dict], given: dict, allowed, credential) -> tuple[dict, list[str]]:
    """Environment for the app from the user's values. given: NAME -> {'value'} | {'ref'}. Names the configuration does not
    reference are extra environment variables (Spring Boot and Quarkus bind QUARKUS_DATASOURCE_JDBC_URL, SPRING_DATASOURCE_URL...
    to their properties), with the same rules. Returns (env, secret values)."""
    env, secrets = {}, []
    known = {v['name'] for v in spec}
    extra = [{'name': n, 'required': False, 'secret': bool(SECRET.search(n)), 'hint': n} for n in sorted(given) if n not in known]
    for v in spec + extra:
        g = given.get(v['name']) or {}
        if 'ref' in g:
            val = credential(g['ref'])
            if not val:
                raise RuntimeTestError(f"La referencia de {v['name']} no tiene valor en el worker")
            secrets.append(val)
        elif 'value' in g:
            if v['secret']:
                raise RuntimeTestError(f"{v['name']} es un secreto: use una referencia env:MF_CRED_* o vault:, no el valor")
            val = str(g['value'])
        elif v['required']:
            raise RuntimeTestError(f"Falta {v['name']} ({v['hint']})")
        else:
            continue
        check_value(val, allowed)
        env[v['name']] = val
    return env, secrets


def start(kind: str, project: Path, work: Path, port: int, env: dict, java: str, log, mvn=None) -> tuple:
    """Start the built candidate; returns (process, log file, base url). mvn(goals, name) runs Maven with the worker's settings
    (approved mirror, the job's local repository): used to fetch the Tomcat distribution."""
    from .tomcat_war import autotest
    target = project / 'target'
    base_env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(work / 'home'), 'LANG': 'C.UTF-8'} | env
    (work / 'home').mkdir(parents=True, exist_ok=True)
    logf = work / 'logs' / f'{kind}.log'
    if kind == 'quarkus':
        jar = target / 'quarkus-app' / 'quarkus-run.jar'
        if not jar.is_file():
            raise RuntimeTestError('No se generó target/quarkus-app/quarkus-run.jar')
        cmd = [java, f'-Dquarkus.http.port={port}', '-Dquarkus.http.host=127.0.0.1', '-jar', str(jar)]
        return autotest._start(cmd, logf, env=base_env, cwd=project), logf, f'http://127.0.0.1:{port}'
    if kind == 'spring':
        jars = [j for j in target.glob('*.jar') if not j.name.endswith(('-sources.jar', '-javadoc.jar', '.original'))]
        if not jars:
            raise RuntimeTestError('No se generó el jar ejecutable en target/')
        cmd = [java, f'-Dserver.port={port}', '-Dserver.address=127.0.0.1', '-jar', str(jars[0])]
        return autotest._start(cmd, logf, env=base_env, cwd=project), logf, f'http://127.0.0.1:{port}'
    wars = sorted(target.glob('*.war'))
    if not wars:
        raise RuntimeTestError('No se generó el WAR en target/')
    pom = (project / 'pom.xml').read_text(encoding='utf-8', errors='replace')
    major = '11' if re.search(r'<artifactId>spring-boot-dependencies</artifactId>\s*<version>4\.', pom) else '10'
    dist = work / 'dist'
    res = mvn([f'{DEPENDENCY_PLUGIN}:copy', f'-Dartifact=org.apache.tomcat:tomcat:{TOMCAT[major]}:zip', f'-DoutputDirectory={dist}'], 'fetch-tomcat')
    zips = sorted(dist.glob(f'tomcat-{TOMCAT[major]}*.zip'))
    if res.exit_code != 0 or not zips:
        raise RuntimeTestError(f'No se pudo obtener Tomcat {TOMCAT[major]} del mirror de Maven aprobado')
    zip_file = zips[0]
    home = work / 'tomcat'
    shutil.rmtree(home, ignore_errors=True)
    shutil.unpack_archive(str(zip_file), work / 'tomcat-unzip', 'zip')
    shutil.move(str(next((work / 'tomcat-unzip').iterdir())), home)
    server = (home / 'conf' / 'server.xml').read_text(encoding='utf-8')
    server = re.sub(r'<Server port="\d+"', '<Server port="-1"', server).replace('port="8080"', f'port="{port}" address="127.0.0.1"')
    (home / 'conf' / 'server.xml').write_text(server, encoding='utf-8')
    shutil.rmtree(home / 'webapps', ignore_errors=True)
    (home / 'webapps').mkdir()
    shutil.copy(wars[0], home / 'webapps' / wars[0].name)  # the context is the WAR name, as on the legacy server
    for d in ('logs', 'temp', 'work'):
        (home / d).mkdir(exist_ok=True)
    cmd = [java, f'-Dcatalina.home={home}', f'-Dcatalina.base={home}', f"-Djava.io.tmpdir={home / 'temp'}",
           '-cp', os.pathsep.join(str(home / 'bin' / j) for j in ('bootstrap.jar', 'tomcat-juli.jar')), 'org.apache.catalina.startup.Bootstrap', 'start']
    log(f'Tomcat {TOMCAT[major]}: {wars[0].name}')
    return autotest._start(cmd, logf, env=base_env, cwd=home), logf, f'http://127.0.0.1:{port}'


def wait_ready(proc, logf: Path, timeout: int) -> str:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        text = logf.read_text(encoding='utf-8', errors='replace') if logf.exists() else ''
        if READY.search(text):
            time.sleep(2)  # the HTTP connector opens right after the Camel context
            return logf.read_text(encoding='utf-8', errors='replace')
        if proc.poll() is not None:
            tail = '\n'.join(text.splitlines()[-15:])
            raise RuntimeTestError(f'La aplicación terminó al arrancar (código {proc.returncode}):\n{tail}')
        time.sleep(2)
    raise RuntimeTestError(f'La aplicación no arrancó en {timeout}s (no apareció «Apache Camel … started» en el log)')


def smoke(base: str, cases_: list, out: Path, timeout: int) -> dict:
    """Without an original system: each case once to the migrated app; PASS when nothing fails with 5xx or no answer."""
    from .tomcat_war import verify
    from .tomcat_war.http_run import _side, mask
    rows = []
    for c in cases_:
        r = verify._send(base, c, timeout)
        state = 'DIFFERENT' if r['status'] == 0 or r['status'] >= 500 else 'SAME'
        rows.append({'name': c['name'], 'method': c.get('method', 'GET'), 'path': c['path'], 'state': state, 'kind': None,
                     'issues': [] if state == 'SAME' else [f"respuesta {r['status'] or 'ninguna'}"], 'accept': None,
                     'request': {**c, 'headers': mask(c.get('headers'))}, 'original': {'status': None, 'type': '', 'body': '', 'truncated': False},
                     'migrated': _side(r), 'raw': r})
    out.mkdir(parents=True, exist_ok=True)
    (out / 'responses.json').write_text(json.dumps({'mode': 'SMOKE', 'cases': [{k: v for k, v in x.items() if k != 'raw'} | {'migratedFull': x['raw']}
                                                                               for x in rows]}, ensure_ascii=False, indent=1), encoding='utf-8')
    import zipfile
    with zipfile.ZipFile(out / 'evidence.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(out / 'responses.json', 'evidencia/responses.json')
    bad = sum(r['state'] != 'SAME' for r in rows)
    return {'status': 'PASS' if rows and not bad else 'NOT_EXECUTABLE' if not rows else 'DIFFERENCES', 'mode': 'SMOKE',
            'totals': {'cases': len(rows), 'same': len(rows) - bad, 'different': bad, 'accepted': 0, 'notExecutable': 0},
            'differencesByKind': {}, 'screenshots': 0, 'screenshotProblem': None, 'cases': [{k: v for k, v in x.items() if k != 'raw'} for x in rows]}


if __name__ == '__main__':  # self-check of the pure parts
    log = ('  Started get-customer (rest://get:/customers:/%7Bid%7D)\n  Started products (rest://get:/products)\n'
           '  Started create-order (rest://post:/orders)\n  Started up (platform-http:///health)\n')
    eps = endpoints(log)
    assert eps == [{'route': 'get-customer', 'method': 'GET', 'path': '/customers/{id}'}, {'route': 'products', 'method': 'GET', 'path': '/products'},
                   {'route': 'create-order', 'method': 'POST', 'path': '/orders'}, {'route': 'up', 'method': 'GET', 'path': '/health'}], eps
    assert [c['path'] for c in cases(eps, {'id': '42'})] == ['/customers/42', '/products', '/health']
    assert _flatten_yaml('spring:\n  datasource:\n    url: "${MF_DS_URL}"\nserver:\n  servlet:\n    context-path: "/shop"\n') == {
        'spring.datasource.url': '${MF_DS_URL}', 'server.servlet.context-path': '/shop'}
    ok = lambda h: h in ('db-test', 'host.docker.internal')  # noqa: E731
    check_value('jdbc:postgresql://db-test:5432/shop', ok)
    check_value('jdbc:oracle:thin:@db-test:1521/APP', ok)
    for bad in ('jdbc:oracle:thin:@10.136.51.71:1521/PRODBANREG', 'https://prod.example.com/api'):
        try:
            check_value(bad, ok)
            raise AssertionError(bad)
        except RuntimeTestError:
            pass
    print('ok')
