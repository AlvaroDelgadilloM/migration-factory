"""Located edits that OpenRewrite does not cover (endpoint strings and configuration values).

Every edit targets one file and one line found by the analyzer, and one exact token on that line; nothing is
replaced globally. If the expected token is not on that line the edit is reported as not applied (manual).
"""
import re
from pathlib import Path

from .security import safe_join

from . import rules as _rules

SCHEME_RENAMES = _rules.scheme_renames()  # rules/migration-rules.json
EXTERNALIZED = _rules.externalized_schemes()
SECRET_LINE = re.compile(r'^(\s*["\']?([\w.\-]*?(?:password|passwd|pwd|secret|token|api[_-]?key)[\w.\-]*)["\']?\s*[=:]\s*)(["\']?)([^\s"\'#]+)(["\']?)(.*)$', re.I)


def env_name(*parts) -> str:
    return re.sub(r'[^A-Z0-9]+', '_', '_'.join(parts).upper()).strip('_')


def plan_edits(endpoints, findings):
    """endpoints: [{component, file, line, dynamic}]; findings: [{ruleId, file, line}] -> edit specs (JSON)."""
    edits, seen = [], set()
    for e in endpoints:
        if e['component'] in SCHEME_RENAMES and (e['file'], e['line'], e['component']) not in seen:
            seen.add((e['file'], e['line'], e['component']))
            edits.append({'op': 'scheme', 'file': e['file'], 'line': e['line'], 'old': e['component'],
                          'new': SCHEME_RENAMES[e['component']], 'externalize': e['component'] in EXTERNALIZED and not e['dynamic'],
                          'rule': _rules.rule_for(e['component'])['id']})
    for f in findings:
        if f['ruleId'] == 'HARDCODED_SECRET' and f['file'].endswith(('.properties', '.yml', '.yaml')):
            edits.append({'op': 'secret', 'file': f['file'], 'line': f['line'], 'rule': 'HARDCODED_SECRET'})
        elif f['ruleId'] == 'HARDCODED_SECRET' and f['file'].endswith(('.java', '.xml')):
            # secret query parameter inside an endpoint URI literal
            edits.append({'op': 'uri-secret', 'file': f['file'], 'line': f['line'], 'rule': 'HARDCODED_SECRET'})
    return edits


def _module_of(rel: str) -> str:
    return rel.split('src/main/', 1)[0] if 'src/main/' in rel else ''


def existing_service_keys(root: Path) -> set:
    """(module, key) of services.<key>.url already defined, so a later run never redefines a key."""
    out = set()
    for f in list(root.rglob('application.yml')) + list(root.rglob('application.properties')):
        rel = f.relative_to(root).as_posix()
        text = f.read_text(encoding='utf-8', errors='replace')
        keys = set(re.findall(r'^services\.([\w-]+)\.url\s*=', text, re.M))
        block = re.search(r'^services:\n((?:[ \t]+.*\n?)*)', text, re.M)
        if block:
            keys |= set(re.findall(r'^  ([\w-]+):', block[1], re.M))
        out |= {(_module_of(rel), k) for k in keys}
    return out


def apply_edits(root: Path, edits, runtime: str) -> list[dict]:
    """Apply edits in place (on the isolated candidate). Returns one result per edit (values never included)."""
    results, services, secret_props = [], {}, {}
    used = {k: None for k in existing_service_keys(root)}  # reserved: never reuse a key already in the configuration
    for e in edits:
        path = safe_join(root, e['file'])
        r = {'op': e['op'], 'file': e['file'], 'line': e['line'], 'applied': False}
        if not path.is_file():
            results.append(r | {'reason': 'archivo no encontrado'})
            continue
        lines = path.read_text(encoding='utf-8').split('\n')
        i = e['line'] - 1
        if i >= len(lines):
            results.append(r | {'reason': 'línea fuera de rango'})
            continue
        line = lines[i]
        if e['op'] == 'scheme':
            lit = re.search(r'(["\'])' + re.escape(e['old']) + r'(://|:)([^"\']*)\1', line)
            if not lit:
                results.append(r | {'reason': f"no se encontró el literal {e['old']}: en la línea"})
                continue
            new_uri = f"{e['new']}{lit[2]}{lit[3]}"
            replacement = new_uri
            if e.get('externalize') and '${' not in lit[3] and '{{' not in lit[3] and '@' not in lit[3].split('/')[0]:
                parts = re.split(r'[/?]', lit[3].lstrip('/'))
                hostport, seg = parts[0] or 'service', (parts[1] if len(parts) > 1 else '')
                base = re.sub(r'[^a-z0-9]+', '-', f'{hostport}-{seg}'.lower()).strip('-')
                key, n = base, 1
                while used.get((_module_of(e['file']), key), new_uri) != new_uri:  # distinct URLs never share a property
                    n += 1
                    key = f'{base}-{n}'
                used[(_module_of(e['file']), key)] = new_uri
                services[(_module_of(e['file']), key)] = new_uri
                replacement = f'{{{{services.{key}.url}}}}'
                r['property'] = f'services.{key}.url'
            lines[i] = line[:lit.start()] + lit[1] + replacement + lit[1] + line[lit.end():]
            r |= {'applied': True, 'change': f"{e['old']}: → {e['new']}:" + (' + URL externalizada' if 'property' in r else '')}
        elif e['op'] == 'uri-secret':
            lit = re.search(r'(["\'])([\w+-]+:[^"\']*?[?&](password|passwd|secret|token|api[_-]?key|accessKey|secretKey)=)([^&"\']+)([^"\']*)\1', line, re.I)
            if not lit or lit[4].startswith(('{{', 'RAW({{', '${')):
                results.append(r | {'reason': 'parámetro secreto no reconocido en el literal de la línea'})
                continue
            host = re.split(r'[/:?@]', re.sub(r'^[\w+-]+:/*', '', lit[2]).split('@')[-1])[0] or 'endpoint'
            prop = f"secrets.{re.sub(r'[^a-z0-9]+', '-', host.lower()).strip('-')}.{lit[3].lower()}"
            var = env_name('MF_SECRET', prop.split('.', 1)[1])
            secret_props[(_module_of(e['file']), prop)] = var
            lines[i] = line[:lit.start()] + lit[1] + lit[2] + 'RAW({{' + prop + '}})' + lit[5] + lit[1] + line[lit.end():]
            r |= {'applied': True, 'change': f'valor del parámetro {lit[3]} -> RAW({{{{{prop}}}}}) = ${{{var}}}', 'env': var, 'property': prop}
        elif e['op'] == 'secret':
            m = SECRET_LINE.match(line)
            if not m or m[4].startswith('${'):
                results.append(r | {'reason': 'valor no reconocido en la línea'})
                continue
            var = env_name('MF_SECRET', m[2])
            lines[i] = f'{m[1]}{m[3]}${{{var}}}{m[5]}{m[6]}'
            r |= {'applied': True, 'change': f'valor reemplazado por ${{{var}}}', 'env': var}
        path.write_text('\n'.join(lines), encoding='utf-8')
        results.append(r)
    for (module, key), url in sorted(services.items()):
        _add_service_config(root, module, key, url, runtime)
    for (module, prop), var in sorted(secret_props.items()):
        _add_secret_config(root, module, prop, var, runtime)
    return results


def _add_secret_config(root: Path, module: str, prop: str, var: str, runtime: str):
    # value only from the environment (no default): a missing variable fails fast instead of leaking
    res = safe_join(root, f'{module}src/main/resources')
    res.mkdir(parents=True, exist_ok=True)
    if runtime == 'spring':
        f = res / 'application.yml'
        text = f.read_text() if f.exists() else ''
        _, name, key = prop.split('.', 2)
        block = f'  {name}:\n    {key}: ${{{var}}}\n'
        if '\nsecrets:\n' in '\n' + text:
            text = text.replace('secrets:\n', 'secrets:\n' + block, 1)
        else:
            text += ('' if text.endswith('\n') or not text else '\n') + 'secrets:\n' + block
        f.write_text(text)
    else:
        f = res / 'application.properties'
        text = f.read_text() if f.exists() else ''
        f.write_text(text + ('' if text.endswith('\n') or not text else '\n') + f'{prop}=${{{var}}}\n')


def _add_service_config(root: Path, module: str, key: str, url: str, runtime: str):
    res = safe_join(root, f'{module}src/main/resources')
    res.mkdir(parents=True, exist_ok=True)
    var = env_name('SERVICES', key, 'URL')
    if runtime == 'spring':
        f = res / 'application.yml'
        text = f.read_text() if f.exists() else ''
        block = f'  {key}:\n    url: ${{{var}:{url}}}\n'
        text = text.replace('services:\n', 'services:\n' + block, 1) if '\nservices:\n' in '\n' + text else text + ('' if text.endswith('\n') or not text else '\n') + 'services:\n' + block
        f.write_text(text)
    else:
        f = res / 'application.properties'
        text = f.read_text() if f.exists() else ''
        f.write_text(text + ('' if text.endswith('\n') or not text else '\n') + f'services.{key}.url=${{{var}:{url}}}\n')


if __name__ == '__main__':  # self-check
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / 'm/src/main/java').mkdir(parents=True)
        (root / 'm/src/main/resources').mkdir(parents=True)
        (root / 'm/src/main/java/R.java').write_text('a\n  .to("http4://payments.internal/api/pay").to("quartz2://t")\n'
                                                     '  .to("http4://payments.internal/api/refund")\n'
                                                     '  from("sftp://u@h:22/in?password=S3cr3t&delay=1")\n')
        (root / 'm/src/main/resources/app.properties').write_text('x=1\ndb.password=S3cr3t\n')
        res = apply_edits(root, [{'op': 'scheme', 'file': 'm/src/main/java/R.java', 'line': 2, 'old': 'http4', 'new': 'http', 'externalize': True},
                                 {'op': 'scheme', 'file': 'm/src/main/java/R.java', 'line': 2, 'old': 'quartz2', 'new': 'quartz', 'externalize': False},
                                 {'op': 'scheme', 'file': 'm/src/main/java/R.java', 'line': 3, 'old': 'http4', 'new': 'http', 'externalize': True},
                                 {'op': 'uri-secret', 'file': 'm/src/main/java/R.java', 'line': 4},
                                 {'op': 'secret', 'file': 'm/src/main/resources/app.properties', 'line': 2}], 'spring')
        assert all(r['applied'] for r in res), res
        java = (root / 'm/src/main/java/R.java').read_text()
        assert '.to("{{services.payments-internal-api.url}}").to("quartz://t")' in java, java
        assert '.to("{{services.payments-internal-api-2.url}}")' in java  # distinct URL, distinct property
        assert 'password=RAW({{secrets.h.password}})&delay=1' in java and 'S3cr3t' not in java
        assert 'S3cr3t' not in (root / 'm/src/main/resources/app.properties').read_text()
        yml = (root / 'm/src/main/resources/application.yml').read_text()
        assert 'url: ${SERVICES_PAYMENTS_INTERNAL_API_URL:http://payments.internal/api/pay}' in yml, yml
        assert 'url: ${SERVICES_PAYMENTS_INTERNAL_API_2_URL:http://payments.internal/api/refund}' in yml
        assert 'password: ${MF_SECRET_H_PASSWORD}' in yml
    print('ok')
