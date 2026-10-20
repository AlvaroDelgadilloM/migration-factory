"""Automatic test of a tomcat-war migration: nothing to set up by hand.

From the original source (the analysed snapshot) and the migrated WAR, for one source container (mobile, web1...):
  1. a test configuration (etc) for both sides, generated from the keys the code reads. Values that could reach a real
     system (hosts, URLs, users, passwords, JDBC, brokers) are never copied: URLs point to a local simulator, databases to
     an H2 database of each side, brokers to an in-memory broker;
  2. the cases, read from the code: every REST operation (verb + path) and every SOAP operation the modules publish;
  3. the original code on its Camel 2 (BaselineServer, Jetty) and the WAR in a real Tomcat, each on a free local port;
  4. the comparison and the evidence (http_run), then both are stopped.

Smoke mode (no data package): empty databases and a simulator that answers 503 to every external call, so both sides are
compared on the same failures. With the client's test data package (see DATA_PACKAGE) the same run uses their databases,
configuration, simulated answers and parameter values.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import blueprint, config_keys, http_run, pipeline, verify

DATA_PACKAGE = """paquete de datos de prueba (carpeta o .zip), todo opcional:
  etc/<ruta>.properties   configuración de prueba; reemplaza al archivo generado con la misma ruta
  sql/*.sql               datos iniciales; se cargan en la base H2 de cada lado (modo Oracle), en orden alfabético
  mocks.json              respuestas de los sistemas externos: [{"key": "<clave de config>", "method": "POST",
                          "contains": "<texto del cuerpo>", "status": 200, "contentType": "text/xml", "body": "..."}]
  valores.json            valores para los parámetros de ruta: {"memberNumber": "95715637"}
  casos.json              casos adicionales con el formato de verify responses"""
H2 = 'com.h2database:h2:2.4.240'
SECRET_KEY = re.compile(r'(?i)pass|pwd|secret|token|api\.?key|\.key$|user|usr|login|credential|auth|cert|salt|\.iv$|cipher|'
                        r'private|signature|merchant|terminal|account|card|client\.?id|licen')
SQL_VALUE = re.compile(r'(?i)^\s*(select|insert|update|delete|merge|call|begin|\{\s*call|with)\b')
BROKER = re.compile(r'(?i)^(failover:|tcp://|nio://|ssl://|amqp://|mqtt://|stomp://|vm://)')
SCHEME = re.compile(r'(?i)^([a-z][a-z0-9+.-]*)://')
HOST = re.compile(r'(?i)^[\w-]+(\.[\w-]+)+(:\d+)?(/.*)?$|^\d{1,3}(\.\d{1,3}){3}(:\d+)?')
PERCENT = re.compile(r'%\{([\w.\-]+)\}')
ETC_LOCATION = re.compile(r'file:\$\{mf\.etc\}/([^<"\s,]+)')
REST_KEY = re.compile(r'^rest:(get|post|put|patch|delete|head|options):(.*)$', re.I)


class AutoTestError(verify.ToolError):
    pass


# --- what is deployed -----------------------------------------------------------------------------------------------------
def explode_war(war: Path, target: Path) -> dict:
    """Unpacks the WAR; every module jar (it carries META-INF/modules/<name>/module.properties) to its own folder.
    Returns {name: {'dir', 'containers'}}."""
    shutil.rmtree(target, ignore_errors=True)
    with zipfile.ZipFile(war) as z:
        z.extractall(target / 'war')
    modules = {}
    for jar in sorted((target / 'war' / 'WEB-INF' / 'lib').glob('*.jar')):
        with zipfile.ZipFile(jar) as z:
            props = [n for n in z.namelist() if re.fullmatch(r'META-INF/modules/[^/]+/module\.properties', n)]
            if not props:
                continue
            name = props[0].split('/')[2]
            d = target / 'modules' / name
            z.extractall(d)
            text = (d / props[0]).read_text(encoding='utf-8', errors='replace')
            m = re.search(r'^containers\s*=\s*(.+)$', text, re.M)
            modules[name] = {'dir': d, 'jar': jar, 'containers': [c.strip() for c in m.group(1).split(',')] if m else []}
    if not modules:
        raise AutoTestError(f'{war.name} no trae módulos migrados (META-INF/modules/*/module.properties)')
    return modules


def source_modules(source: Path) -> dict:
    """artifactId -> source project folder of the original workspace."""
    return {pipeline._head(pipeline.read(p / 'pom.xml'), 'artifactId'): p for p in pipeline.discover(source)}


def servlets(projects: dict) -> dict:
    """Servlet name -> alias, from the OsgiServletRegisterer beans of the original Blueprint files."""
    found = {}
    for p in projects.values():
        for f in sorted((p / 'src' / 'main' / 'resources' / 'OSGI-INF' / 'blueprint').glob('*.xml')):
            text = pipeline.read(f)
            if not blueprint.is_servlet_registration(text):
                continue
            for m in blueprint._SERVLET_BEAN.finditer(text):
                alias = re.search(r'name="alias"\s+value="([^"]+)"', m.group(1))
                name = re.search(r'name="servletName"\s+value="([^"]+)"', m.group(1))
                if alias and name:
                    found[name.group(1)] = alias.group(1).rstrip('/')
    return found


def _module_text(project: Path) -> str:
    parts = []
    for sub in ('java', 'resources'):
        d = project / 'src' / 'main' / sub
        if d.is_dir():
            parts += [pipeline.read(f) for f in sorted(d.rglob('*')) if f.suffix in ('.java', '.xml') and f.is_file()]
    return '\n'.join(parts)


# --- configuration ---------------------------------------------------------------------------------------------------------
def delimiters(text: str) -> list:
    """Placeholder delimiters in use: Camel {{..}}, Blueprint ${..} and every custom pair an ext:property-placeholder
    declares (placeholder-prefix="%[" placeholder-suffix="]", ...)."""
    pairs = {('{{', '}}'), ('${', '}')}
    for m in re.finditer(r'<[\w:]*property-placeholder\b([^>]*)>', text):
        pre = re.search(r'placeholder-prefix="([^"]+)"', m.group(1))
        suf = re.search(r'placeholder-suffix="([^"]+)"', m.group(1))
        if pre:
            pairs.add((pre.group(1), suf.group(1) if suf else '}'))
    return sorted(pairs, key=lambda p: -len(p[0]))


def _ph(key: str, pre: str, suf: str) -> str:
    return re.escape(pre) + re.escape(key) + r'(?::[^' + re.escape(suf[0]) + r'\n]*)?' + re.escape(suf)


def used_keys(text: str, delims: list | None = None) -> set:
    keys = {m[1] for m in config_keys.CAMEL.finditer(text)} | {m[1] for m in config_keys.INJECT.finditer(text)} | set(PERCENT.findall(text))
    keys |= {k for k in config_keys.BLUEPRINT.findall(text) if not config_keys.SIMPLE.match(k)}
    for pre, suf in delims or []:
        if (pre, suf) not in (('{{', '}}'), ('${', '}')):
            keys |= set(re.findall(re.escape(pre) + r'([\w.\-]+)(?::[^' + re.escape(suf[0]) + r'\n]*)?' + re.escape(suf), text))
    return keys


def _scheme_in_code(key: str, text: str, delims: list | None = None) -> bool:
    """`https4://{{key}}` or `http4:{{key}}`: the code writes the scheme itself and the value is only host/path."""
    return any(re.search(r'(?:https?4?|wss?|[a-z0-9-]+://)(?::(?://)?|)' + _ph(key, pre, suf), text, re.I)
               for pre, suf in delims or [('{{', '}}'), ('${', '}')])


def java_index(projects: dict) -> dict:
    """Fully qualified class name -> Java source of the whole workspace (to read the type of a bean property)."""
    index = {}
    for p in projects.values():
        d = p / 'src' / 'main' / 'java'
        for f in d.rglob('*.java') if d.is_dir() else []:
            index['.'.join(f.relative_to(d).with_suffix('').parts)] = f
    return index


def _setter_type(cls: str, prop: str, classes: dict) -> str | None:
    f = classes.get(cls)
    if f is None:
        return None
    m = re.search(r'\bset' + re.escape(prop[:1].upper() + prop[1:]) + r'\s*\(\s*(?:final\s+)?([\w.<>]+)\s+\w+\s*\)', pipeline.read(f))
    return m.group(1) if m else None


def hint(key: str, text: str, delims: list, classes: dict | None = None) -> str:
    """What the code does with the key: the Java type of the bean property it sets (`<bean class="X"><property
    name="p" value="${k}"/>` -> X.setP(type)), the URI option (`delay={{k}}`), the bean property name, or the Java type of
    an @PropertyInject field. Empty when nothing says it."""
    for pre, suf in delims:
        ph = _ph(key, pre, suf)
        m = re.search(r'name="([\w.]+)"\s+value="' + ph, text)
        if m and classes:
            bean = None
            for bean in re.finditer(r'<bean\b[^>]*\bclass="([\w.$]+)"', text[:m.start()]):
                pass
            t = _setter_type(bean.group(1), m.group(1), classes) if bean else None
            if t:
                return f'java:{t}'
        m = m or re.search(r'([A-Za-z][\w.]*)=' + ph, text)
        if m:
            return m.group(1)
    m = re.search(r'@PropertyInject\(\s*(?:value\s*=\s*)?"' + re.escape(key) + r'"[^)]*\)\s*(?:(?:private|protected|public|static|final)\s+)*([\w.]+)', text)
    return f'java:{m.group(1)}' if m else ''


def dummy(key: str, mock: str, jdbc: str, files: Path, use: str = '') -> str:
    """Value for a key the repository does not define (or whose value is unsafe to use): from what the code does with it
    (see hint), else from its name. Never a real system."""
    if use.startswith('java:'):
        t = use[5:].rsplit('.', 1)[-1].lower()
        if t in ('int', 'integer', 'long', 'short', 'double', 'float', 'bigdecimal', 'biginteger'):
            return '3'
        if t == 'boolean':
            return 'false'
    k, u = key.lower(), use.lower()
    if re.search(r'(^|\.)(sql|query|sp|procedure)(\.|$)', k):  # before the secret check: sql.select.token is a query
        return 'select 1 from dual'
    if SECRET_KEY.search(key):
        return 'mf-prueba'
    if u == 'cron' or u.endswith('.cron') or re.search(r'cron|periodicity', k):
        return '0+0+3+1+1+?'
    if re.search(r'delay|period|timeout|interval|ttl|millis', u):
        return '30000'
    if re.search(r'size|count|max|min|increment|attempts|retries|limit', u):
        return '3'
    if u.startswith(('is', 'use', 'enable', 'disable')) or u in ('bridgeendpoint', 'throwexceptiononfailure', 'synchronous', 'transacted'):
        return 'false'
    if re.search(r'time\.?unit', k):
        return 'MINUTES'
    if re.search(r'jdbc|datasource\.url|db\.url|\.url\.db', k):
        return jdbc
    if re.search(r'driver', k):
        return 'org.h2.Driver'
    if re.search(r'broker|jms\.url|activemq', k):
        return 'vm://mf-test?broker.persistent=false&broker.useJmx=false'
    if re.search(r'(^|\.)(sql|query|sp|procedure)(\.|$)', k):
        return 'select 1 from dual'
    if k.endswith('port'):
        return str(free_port())  # each side its own: a port both bind would fail on one of them
    if re.search(r'timeout|delay|period|interval|ttl|millis|time', k):
        return '30000'
    if re.search(r'size|max|min|attempt|retr|limit|count|days|minutes|hours|seconds|number|num$|length|increment', k):
        return '3'
    if re.search(r'enable|disable|active|flag|^is\.', k):
        return 'false'
    if re.search(r'url|endpoint|uri|wsdl|host|server|address|ws\.', k):
        return f'{mock}/{key}'
    if re.search(r'mail|email|recipient|sender|from$|\.to$', k):
        return 'prueba@example.com'
    if re.search(r'path|dir|folder|file', k):
        return (files / key).as_posix()
    return '1'  # ids, codes, queue names and numbers all accept it


def safe(key: str, value: str, mock: str, jdbc: str, files: Path) -> tuple[str, bool]:
    """(value to use, replaced). Only values that cannot reach a real system are kept: SQL, numbers, flags, names, cron."""
    v = value.strip()
    if not v or SQL_VALUE.match(v):
        return value, False
    if SECRET_KEY.search(key):
        return 'mf-prueba', True
    if v.lower().startswith('jdbc:'):
        return jdbc, True
    if BROKER.match(v):
        return 'vm://mf-test?broker.persistent=false&broker.useJmx=false', True
    m = SCHEME.match(v)
    if m:
        scheme = m.group(1).lower()
        if scheme.startswith(('http', 'ws')):
            return f'{mock}/{key}', True
        return f'{scheme}://127.0.0.1:9', True
    if '@' in v and '.' in v.split('@')[-1]:
        return 'prueba@example.com', True
    if HOST.match(v):
        return f"127.0.0.1:9/{key}", True
    if re.match(r'^(/|[A-Za-z]:[\\/])', v):
        return (files / key).as_posix(), True
    return value, False


def _props(text: str) -> dict:
    out, key = {}, None
    for line in text.splitlines():
        if key is not None:  # continuation of the previous value
            out[key] += line.strip()
            key = key if line.endswith('\\') else None
            continue
        s = line.strip()
        if not s or s[0] in '#!':
            continue
        m = re.match(r'([^=:\s]+)\s*[=:]?\s*(.*)$', s)
        if m:
            out[m.group(1)] = m.group(2).rstrip('\\')
            key = m.group(1) if s.endswith('\\') else None
    return out


def build_etc(projects: dict, modules: dict, target: Path, mock: str, jdbc: str, data: Path | None) -> dict:
    """Writes target/<location> for every `file:${mf.etc}/...` the given migrated modules load. Base values: the data
    package etc/ if it has the file, else a same-named .properties of the repository with every unsafe value replaced;
    keys a module uses and no file defines are added with a value from their name. Returns the report (no values)."""
    shutil.rmtree(target, ignore_errors=True)
    files = target.parent / 'files'
    repo_props = {}
    for p in projects.values():
        for f in p.rglob('*.properties'):
            if 'target' not in f.relative_to(p).parts:
                repo_props.setdefault(f.name, []).append(f)
    report = {'files': [], 'replaced': 0, 'generated': 0, 'fromDataPackage': []}
    classes = java_index(projects)
    written = {}
    for name, mod in sorted(modules.items()):
        xml = '\n'.join(pipeline.read(f) for f in (mod['dir'] / 'META-INF' / 'modules' / name).glob('*.xml'))
        locations = list(dict.fromkeys(ETC_LOCATION.findall(xml)))
        project = projects.get(name)
        code = _module_text(project) if project else xml
        delims = delimiters(code)
        for rel in locations:
            if rel in written:
                continue
            given = data / 'etc' / rel if data else None
            if given and given.is_file():
                values = _props(given.read_text(encoding='utf-8', errors='replace'))
                report['fromDataPackage'].append(rel)
            else:
                candidates = repo_props.get(Path(rel).name, [])
                best = next((c for c in candidates if Path(rel).parent.name in c.parts), candidates[0] if candidates else None)
                values = {}
                for k, v in (_props(best.read_text(encoding='utf-8', errors='replace')) if best else {}).items():
                    values[k], replaced = safe(k, v, mock, jdbc, files)
                    use = hint(k, code, delims, classes) if replaced else ''
                    if use.startswith('java:'):  # a typed property: a text placeholder would stop the module
                        values[k] = dummy(k, mock, jdbc, files, use)
                    if replaced and k.lower().endswith('port'):
                        values[k] = str(free_port())
                    if replaced and SCHEME.match(values[k]) and _scheme_in_code(k, code, delims):
                        values[k] = SCHEME.sub('', values[k])
                    report['replaced'] += replaced
            written[rel] = values
        have = {k for rel in locations for k in written.get(rel, {})}
        if locations:
            for key in sorted(used_keys(code, delims) - have):
                value = dummy(key, mock, jdbc, files, hint(key, code, delims, classes))
                if _scheme_in_code(key, code, delims) and SCHEME.match(value):
                    value = SCHEME.sub('', value)
                written[locations[-1]][key] = value
                report['generated'] += 1
    for rel, values in written.items():
        f = target / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(''.join(f"{k}={v.replace(chr(92), '/') if re.match(r'^[A-Za-z]:', v) else v}\n" for k, v in values.items()), encoding='utf-8')
        report['files'].append(rel)
    return report


# --- cases -----------------------------------------------------------------------------------------------------------------
def _join(*parts: str) -> str:
    return '/' + '/'.join(p.strip('/') for p in parts if p and p.strip('/'))


def rest_cases(dumps: dict, projects: dict, aliases: dict, values: dict) -> list:
    """One case per REST operation of the route dumps: rest:<verb>:<path> or rest:<verb>:<base>:<path>."""
    cases = []
    for module, routes in sorted(dumps.items()):
        project = projects.get(module)
        text = _module_text(project) if project else ''
        m = re.search(r'servletName"\s*,\s*"([^"]+)"|key="servletName"\s+value="([^"]+)"', text)
        servlet = (m.group(1) or m.group(2)) if m else 'CamelServlet'
        alias = aliases.get(servlet)
        if alias is None:
            continue
        for key in sorted(routes):
            uri = key.split(' | ', 1)[-1]
            r = REST_KEY.match(uri)
            if not r:
                continue
            verb, path = r.group(1).upper(), r.group(2)
            path = _join(alias, *path.split(':', 1))
            path = re.sub(r'\{([^}]+)\}', lambda p: str(values.get(p.group(1), '1')), path)
            case = {'name': f'{module} · {verb} {path}', 'method': verb, 'path': path, 'headers': {'Accept': 'application/json'}}
            if verb in ('POST', 'PUT', 'PATCH'):
                case |= {'body': '{}', 'contentType': 'application/json'}
            cases.append(case)
    return cases


def soap_cases(modules: dict) -> list:
    """One case per operation of every CXF endpoint the modules publish (address "/..."): a document/literal request with
    the operation's input element, empty."""
    cases = []
    for name, mod in sorted(modules.items()):
        for f in sorted((mod['dir'] / 'META-INF' / 'modules' / name).glob('*.xml')):
            for m in re.finditer(r'<[\w-]+:cxfEndpoint\b([^>]*)>', pipeline.read(f)):
                address = re.search(r'address="(/[^"]*)"', m.group(1))
                wsdl = re.search(r'wsdlURL="([^"]+)"', m.group(1))
                if not address or not wsdl:
                    continue
                wsdl_file = mod['dir'] / wsdl.group(1).replace('classpath:', '').lstrip('/')
                for op, element, action in _wsdl_operations(wsdl_file):
                    ns, local = element
                    body = ('<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Header/><soapenv:Body>'
                            f'<ns:{local} xmlns:ns="{ns}"/></soapenv:Body></soapenv:Envelope>')
                    cases.append({'name': f'{name} · SOAP {address.group(1)} {op}', 'method': 'POST', 'path': '/cxf' + address.group(1),
                                  'headers': {'SOAPAction': f'"{action}"'}, 'body': body, 'contentType': 'text/xml; charset=UTF-8'})
    return cases


def _wsdl_operations(path: Path) -> list:
    """(operation, (namespace, input element), soapAction) of a WSDL 1.1 document/literal service."""
    if not path.is_file():
        return []
    W, S = '{http://schemas.xmlsoap.org/wsdl/}', '{http://schemas.xmlsoap.org/wsdl/soap/}'
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    nsmap = dict(re.findall(r'xmlns:([\w-]+)="([^"]+)"', path.read_text(encoding='utf-8', errors='replace')))

    def qname(v):
        prefix, _, local = v.rpartition(':')
        return nsmap.get(prefix, root.get('targetNamespace', '')), local
    messages = {m.get('name'): [qname(p.get('element')) for p in m.findall(f'{W}part') if p.get('element')] for m in root.iter(f'{W}message')}
    actions = {o.get('name'): (o.find(f'{S}operation').get('soapAction', '') if o.find(f'{S}operation') is not None else '')
               for b in root.iter(f'{W}binding') for o in b.findall(f'{W}operation')}
    out = []
    for pt in root.iter(f'{W}portType'):
        for o in pt.findall(f'{W}operation'):
            i = o.find(f'{W}input')
            parts = messages.get(qname(i.get('message'))[1], []) if i is not None else []
            if parts:
                out.append((o.get('name'), parts[0], actions.get(o.get('name'), '')))
    return out


# --- servers ---------------------------------------------------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Simulator:
    """External systems of both sides: answers from the data package (mocks.json) or 503 with the key it was called for."""

    def __init__(self, mocks: list):
        self.mocks, self.calls = mocks, []
        sim = self

        class H(BaseHTTPRequestHandler):
            def _answer(self):
                n = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(n).decode('utf-8', errors='replace') if n else ''
                key = self.path.lstrip('/').split('/', 1)[0].split('?', 1)[0]
                sim.calls.append(key)
                hit = next((m for m in sim.mocks if m.get('key') == key and m.get('method', self.command).upper() == self.command
                            and m.get('contains', '') in body), None)
                status, ctype, text = (hit.get('status', 200), hit.get('contentType', 'text/plain'), hit.get('body', '')) if hit else \
                    (503, 'text/plain', f'sin respuesta simulada para {key}')
                data = text.encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _answer

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()


def _start(cmd: list, log_file: Path, env: dict | None = None, cwd: Path | None = None) -> subprocess.Popen:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    kw = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    return subprocess.Popen([str(c) for c in cmd], stdout=log_file.open('wb'), stderr=subprocess.STDOUT, env=env, cwd=cwd, **kw)


def _stop(p: subprocess.Popen | None):
    if p is None or p.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], capture_output=True)
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def _wait(p: subprocess.Popen, log_file: Path, ready: str, timeout: int) -> str:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        text = log_file.read_text(encoding='utf-8', errors='replace') if log_file.exists() else ''
        if ready in text:
            return text
        if p.poll() is not None:
            raise AutoTestError(f'El proceso terminó antes de estar listo (código {p.returncode}); ver {log_file}')
        time.sleep(2)
    raise AutoTestError(f'No estuvo listo en {timeout}s; ver {log_file}')


def _fetch(artifact: str, dest: Path, work: Path) -> Path:
    """One artifact from the Maven repositories the environment allows (the worker's mirror)."""
    dest.mkdir(parents=True, exist_ok=True)
    mvn = shutil.which('mvn')
    if not mvn:
        raise AutoTestError('mvn no está en el PATH')
    if verify._run([mvn, '-B', '-q', 'dependency:copy', f'-Dartifact={artifact}', f'-DoutputDirectory={dest}'], work / 'logs' / 'fetch.log') != 0:
        raise AutoTestError(f"No se pudo obtener {artifact}; ver {work / 'logs' / 'fetch.log'}")
    group, art, version = artifact.split(':')[:3]
    found = sorted(dest.glob(f'{art}-{version}*'))
    if not found:
        raise AutoTestError(f'{artifact} no quedó en {dest}')
    return found[0]


def _h2_database(h2: Path, folder: Path, data: Path | None, work: Path) -> str:
    """H2 file database (Oracle mode) for one side, with the data package scripts loaded. Returns its JDBC URL."""
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    url = f"jdbc:h2:file:{(folder / 'mf').as_posix()};MODE=Oracle;DEFAULT_NULL_ORDERING=HIGH"
    for script in sorted((data / 'sql').glob('*.sql')) if data and (data / 'sql').is_dir() else []:
        if verify._run([verify._java('java'), '-cp', h2, 'org.h2.tools.RunScript', '-url', url, '-script', script],
                       work / 'logs' / f'sql-{folder.name}.log') != 0:
            raise AutoTestError(f"No se pudo cargar {script.name} en la base de prueba; ver {work / 'logs' / f'sql-{folder.name}.log'}")
    return url


def start_tomcat(war_dir: Path, work: Path, version: str, port: int, etc: Path, container: str, jdbc: str, h2: Path,
                 java_home: Path | None = None) -> tuple:
    """The migrated WAR in a real Tomcat (official distribution), with the test configuration."""
    zip_file = _fetch(f'org.apache.tomcat:tomcat:{version}:zip', work / 'dist', work)
    home = work / 'tomcat'
    shutil.rmtree(home, ignore_errors=True)
    with zipfile.ZipFile(zip_file) as z:
        z.extractall(work / 'tomcat-unzip')
    shutil.move(str(next((work / 'tomcat-unzip').iterdir())), home)
    shutil.rmtree(work / 'tomcat-unzip', ignore_errors=True)
    server = (home / 'conf' / 'server.xml').read_text(encoding='utf-8')
    server = re.sub(r'<Server port="\d+"', '<Server port="-1"', server)
    server = server.replace('port="8080"', f'port="{port}"')
    (home / 'conf' / 'server.xml').write_text(server, encoding='utf-8')
    shutil.rmtree(home / 'webapps', ignore_errors=True)
    shutil.copytree(war_dir, home / 'webapps' / 'ROOT')
    shutil.copy(h2, home / 'lib')
    for d in ('logs', 'temp', 'work'):
        (home / d).mkdir(exist_ok=True)
    java = str(Path(java_home) / 'bin' / ('java.exe' if os.name == 'nt' else 'java')) if java_home else verify._java('java')
    cmd = [java, f'-Dcatalina.home={home}', f'-Dcatalina.base={home}', f"-Djava.io.tmpdir={home / 'temp'}",
           f"-Djava.util.logging.config.file={home / 'conf' / 'logging.properties'}", '-Djava.util.logging.manager=org.apache.juli.ClassLoaderLogManager',
           f'-Dmf.etc={etc}', f'-Dmf.container={container}', f'-Dmf.test.jdbcUrl={jdbc}',
           '-cp', os.pathsep.join(str(home / 'bin' / j) for j in ('bootstrap.jar', 'tomcat-juli.jar')), 'org.apache.catalina.startup.Bootstrap', 'start']
    log = work / 'logs' / 'tomcat.log'
    return _start(cmd, log), log


def inspect_data_package(data: bytes, max_mb: int = 100) -> dict:
    """What a data package (.zip) brings, or AutoTestError when it is not one: only the layout of DATA_PACKAGE, no absolute
    paths or '..', bounded size, and JSON files that parse."""
    import io
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise AutoTestError('El paquete de datos debe ser un .zip') from None
    names = [n for n in z.namelist() if not n.endswith('/')]
    if sum(i.file_size for i in z.infolist()) > max_mb * 1024 * 1024 or len(names) > 5000:
        raise AutoTestError(f'El paquete de datos supera {max_mb} MB o 5000 archivos')
    roots = {n.split('/', 1)[0] for n in names}
    prefix = next(iter(roots)) + '/' if len(roots) == 1 and all('/' in n for n in names) else ''  # a single top folder is allowed
    contents = {'etc': [], 'sql': [], 'mocks': 0, 'values': 0, 'cases': 0, 'other': []}
    for n in names:
        if n.startswith('/') or '..' in n.split('/') or ':' in n:
            raise AutoTestError(f'Ruta no permitida en el paquete de datos: {n}')
        rel = n[len(prefix):]
        if rel.startswith('etc/') and rel.endswith(('.properties', '.cfg')):
            contents['etc'].append(rel[4:])
        elif rel.startswith('sql/') and rel.endswith('.sql'):
            contents['sql'].append(rel[4:])
        elif rel.startswith('mocks/'):
            continue
        elif rel in ('mocks.json', 'valores.json', 'casos.json'):
            try:
                parsed = json.loads(z.read(n).decode('utf-8'))
            except (ValueError, UnicodeDecodeError) as e:
                raise AutoTestError(f'{rel} no es JSON válido: {e}') from None
            key = {'mocks.json': 'mocks', 'valores.json': 'values', 'casos.json': 'cases'}[rel]
            contents[key] = len(parsed['cases'] if key == 'cases' and isinstance(parsed, dict) else parsed)
        else:
            contents['other'].append(rel)
    if contents['other']:
        raise AutoTestError(f"Archivos no reconocidos en el paquete de datos: {', '.join(contents['other'][:10])}. {DATA_PACKAGE}")
    del contents['other']
    return contents


# --- the run ---------------------------------------------------------------------------------------------------------------
def _data_dir(data: Path | None, work: Path) -> Path | None:
    if data is None:
        return None
    data = Path(data)
    if data.is_dir():
        return data
    target = work / 'data'
    shutil.rmtree(target, ignore_errors=True)
    with zipfile.ZipFile(data) as z:
        for n in z.namelist():
            if n.startswith('/') or '..' in Path(n).parts:
                raise AutoTestError(f'Ruta no permitida en el paquete de datos: {n}')
        z.extractall(target)
    inner = [p for p in target.iterdir() if p.is_dir()]
    return inner[0] if len(inner) == 1 and not any(target.glob('*.json')) and not (target / 'etc').exists() else target


def run(source: Path, war: Path, out: Path, container: str = '', data: Path | None = None, screenshots: bool = True,
        timeout: int = 900, log=lambda m: None, migrated_java_home: Path | None = None) -> dict:
    source, out = Path(source).resolve(), Path(out).resolve()
    work = out / '.verify' / 'auto'
    work.mkdir(parents=True, exist_ok=True)
    data = _data_dir(data, work)
    mode = 'DATOS' if data else 'HUMO'
    log(f"prueba automática ({'con paquete de datos' if data else 'de humo, sin datos del cliente'})")
    modules = explode_war(Path(war), work / 'migrated')
    deployed = {n: m for n, m in modules.items() if not container or container in m['containers'] or not m['containers']}
    if not deployed:
        raise AutoTestError(f"Ningún módulo se despliega en el contenedor '{container}'")
    projects = source_modules(source)
    aliases = servlets(projects)  # the bundle that registers a servlet is not always the one whose routes use it
    values = json.loads((data / 'valores.json').read_text(encoding='utf-8')) if data and (data / 'valores.json').is_file() else {}
    mocks = json.loads((data / 'mocks.json').read_text(encoding='utf-8')) if data and (data / 'mocks.json').is_file() else []
    for m in mocks:
        if m.get('bodyFile'):
            m['body'] = (data / m['bodyFile']).read_text(encoding='utf-8')

    log(f"{len(deployed)} módulos en el contenedor '{container or 'todos'}'; leyendo sus servicios")
    java_migrated = str(Path(migrated_java_home) / 'bin' / ('java.exe' if os.name == 'nt' else 'java')) if migrated_java_home else None
    dumps = verify.dump_migrated(source, {n: m['dir'] for n, m in deployed.items()}, work / 'migrated' / 'war', work, log, java_migrated)
    cases = rest_cases(dumps, projects, aliases, values) + soap_cases(deployed)
    if data and (data / 'casos.json').is_file():
        extra = json.loads((data / 'casos.json').read_text(encoding='utf-8'))
        cases += extra['cases'] if isinstance(extra, dict) else extra
    if not cases:
        raise AutoTestError('No se encontraron servicios REST ni SOAP publicados en este contenedor')
    log(f'{len(cases)} casos generados')

    sim = Simulator(mocks)
    h2 = _fetch(H2, work / 'dist', work)
    rules = pipeline.load_rules()
    procs = []
    try:
        sides = {}
        for side in ('original', 'migrado'):
            jdbc = _h2_database(h2, work / f'db-{side}', data, work)
            etc_report = build_etc(projects, deployed, work / f'etc-{side}', sim.base, jdbc, data)
            sides[side] = {'jdbc': jdbc, 'etc': work / f'etc-{side}', 'port': free_port(), 'etcReport': etc_report}
        o, mg = sides['original'], sides['migrado']
        log('levantando el original (código actual sobre Camel 2) y el migrado (WAR en Tomcat)')
        servlet_arg = ','.join(f'{n}={a}' for n, a in sorted(aliases.items())) or 'CamelServlet=/camel'
        cmd, env = verify.baseline_command(source, out, sorted(n for n in deployed if n in projects), servlet_arg, o['etc'], o['port'], [h2], log)
        cmd = cmd[:1] + [f"-Dmf.test.jdbcUrl={o['jdbc']}"] + cmd[1:]
        p_orig = _start(cmd, work / 'logs' / 'original.log', env=env)
        procs.append(p_orig)
        p_tc, tc_log = start_tomcat(work / 'migrated' / 'war', work, rules['profile']['tomcatVersion'], mg['port'], mg['etc'], container, mg['jdbc'], h2,
                                     migrated_java_home)
        procs.append(p_tc)
        text_o = _wait(p_orig, work / 'logs' / 'original.log', 'BASELINE LISTO', timeout)
        text_m = _wait(p_tc, tc_log, 'Modulos iniciados:', timeout)
        started = {'original': _summary(text_o, r'BASELINE modulos iniciados: (\d+) de (\d+)\. Con falla: \[(.*?)\]'),
                   'migrado': _summary(text_m, r'Modulos iniciados: (\d+) de (\d+)\. Con falla: \[(.*?)\]')}
        log(f"original: {started['original']['started']} de {started['original']['total']} módulos · "
            f"migrado: {started['migrado']['started']} de {started['migrado']['total']}")
        _wait(p_tc, tc_log, 'Server startup in', 120)
        labels = ({'label': 'ORIGINAL (Camel 2)', 'url': f"http://127.0.0.1:{o['port']}"},
                  {'label': 'MIGRADO (Tomcat, Camel 4)', 'url': f"http://127.0.0.1:{mg['port']}"})
        summary = http_run.run(*labels, cases, out, screenshots, 60, log)
    finally:
        for p in procs:
            _stop(p)
        sim.stop()
    return summary | {'mode': mode, 'container': container, 'modules': sorted(deployed), 'startup': started,
                      'externalCalls': sorted(set(sim.calls)),
                      'configuration': {k: v for k, v in sides['original']['etcReport'].items() if k != 'files'} | {'files': len(sides['original']['etcReport']['files'])},
                      'generatedCases': len(cases), 'logs': str(work / 'logs')}


def _summary(text: str, pattern: str) -> dict:
    m = re.search(pattern, text)
    if not m:
        return {'started': None, 'total': None, 'failed': []}
    return {'started': int(m.group(1)), 'total': int(m.group(2)), 'failed': [x.strip() for x in m.group(3).split(',') if x.strip()]}
