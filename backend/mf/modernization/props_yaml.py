"""application*.properties -> application*.yml for Spring Boot (MOD_PROPERTIES_YAML).

Deterministic and verified: the .properties file is read with java.util.Properties semantics, every value is written as a
double-quoted YAML string (Spring binds strings, so `on`, `010` or `1e3` keep their exact text), and the YAML produced is read
back by this module and must give exactly the same key -> value map before anything is written. What YAML cannot express
the same way is refused with a reason (never guessed): indexed keys (`a[0]`), a key that is also a prefix of another
(`a=1` and `a.b=2`), keys with characters outside [A-Za-z0-9_.-], or a .yml/.yaml for the same profile already present."""
from __future__ import annotations

import re
from pathlib import Path

KEY = re.compile(r'^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$')
_ESC = {'t': '\t', 'n': '\n', 'r': '\r', 'f': '\f'}


def _unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s):
            n = s[i + 1]
            if n == 'u' and re.fullmatch(r'[0-9a-fA-F]{4}', s[i + 2:i + 6]):
                out.append(chr(int(s[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append(_ESC.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def parse_properties(text: str) -> list[tuple[str, str]]:
    """java.util.Properties.load semantics (comments, = : or whitespace separator, continuation lines, escapes)."""
    logical, buf = [], ''
    for raw in text.splitlines():
        line = raw.lstrip(' \t\f') if not buf else raw.lstrip(' \t\f')
        if not buf and (not line or line[0] in '#!'):
            continue
        trailing = len(line) - len(line.rstrip('\\'))
        if trailing % 2:  # odd number of backslashes: the line continues
            buf += line[:-1]
            continue
        logical.append(buf + line)
        buf = ''
    if buf:
        logical.append(buf)
    pairs = []
    for line in logical:
        i, key = 0, []
        while i < len(line) and line[i] not in '=: \t\f':
            if line[i] == '\\' and i + 1 < len(line):
                key.append(line[i:i + 2])
                i += 2
                continue
            key.append(line[i])
            i += 1
        while i < len(line) and line[i] in ' \t\f':
            i += 1
        if i < len(line) and line[i] in '=:':
            i += 1
            while i < len(line) and line[i] in ' \t\f':
                i += 1
        pairs.append((_unescape(''.join(key)), _unescape(line[i:])))
    return pairs


def _quote(v: str) -> str:
    return '"' + ''.join({'\\': '\\\\', '"': '\\"', '\n': '\\n', '\t': '\\t', '\r': '\\r'}.get(c, c) if c >= ' ' or c in '\n\t\r'
                         else f'\\u{ord(c):04x}' for c in v) + '"'


def to_yaml(pairs: list[tuple[str, str]]) -> str:
    """Nested YAML (order of first appearance kept); call check() first."""
    tree: dict = {}
    for k, v in pairs:
        node = tree
        parts = k.split('.')
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = v
    lines = []

    def emit(node, depth):
        for k, v in node.items():
            if isinstance(v, dict):
                lines.append(f"{'  ' * depth}{k}:")
                emit(v, depth + 1)
            else:
                lines.append(f"{'  ' * depth}{k}: {_quote(v)}")
    emit(tree, 0)
    return '\n'.join(lines) + '\n'


def read_back(yaml_text: str) -> dict:
    """Reader for exactly the subset to_yaml writes (two-space nesting, double-quoted scalars): the verification step."""
    out, stack = {}, []
    for line in yaml_text.splitlines():
        if not line.strip():
            continue
        depth = (len(line) - len(line.lstrip(' '))) // 2
        m = re.fullmatch(r'([A-Za-z0-9_-]+):(?: (".*"))?', line.strip())
        if not m:
            raise ValueError(f'línea no reconocida: {line!r}')
        stack = stack[:depth] + [m[1]]
        if m[2] is not None:
            out['.'.join(stack)] = _unescape(m[2][1:-1])
    return out


def check(pairs: list[tuple[str, str]]) -> list[str]:
    """Why this file cannot be converted without changing what Spring reads ([] = convertible)."""
    problems, keys = [], {}
    for k, v in pairs:
        if not KEY.match(k):
            problems.append(f'clave «{k}» con caracteres que YAML no representa igual' + (' (índice)' if '[' in k else ''))
        keys[k] = v  # the last one wins, as in java.util.Properties
    ks = sorted(keys)
    for a, b in zip(ks, ks[1:]):
        if b.startswith(a + '.'):
            problems.append(f'«{a}» tiene valor y también es prefijo de «{b}»')
    return problems


def convert(path: Path) -> tuple[str | None, list[str]]:
    """(yaml text, []) or (None, reasons). The conversion is accepted only if reading it back gives the same map."""
    yml = path.with_suffix('.yml')
    if yml.exists() or path.with_suffix('.yaml').exists():
        return None, [f'ya existe {yml.name} o .yaml para el mismo perfil']
    pairs = parse_properties(path.read_text(encoding='iso-8859-1'))  # what Spring Boot reads; the YAML (UTF-8) keeps those exact strings
    problems = check(pairs)
    if problems:
        return None, problems
    expected = dict(pairs)
    text = to_yaml(list(expected.items()))
    if read_back(text) != expected:
        return None, ['la verificación de ida y vuelta no coincide']
    return text, []


def candidates(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob('application*.properties')
                  if 'target' not in p.parts and '.git' not in p.parts and p.parent.name == 'resources' and 'main' in p.parts)


if __name__ == '__main__':  # self-check
    props = ('# c\nserver.port = 8080\nspring.datasource.url=jdbc:h2:mem:x;MODE=Oracle\nflag: on\nmsg=line1\\\n    line2\n'
             'path=C:\\\\data\\\\in\nquote="x"\nesc=\\u00e1rbol\n')
    pairs = parse_properties(props)
    assert dict(pairs) == {'server.port': '8080', 'spring.datasource.url': 'jdbc:h2:mem:x;MODE=Oracle', 'flag': 'on', 'msg': 'line1line2',
                           'path': 'C:\\data\\in', 'quote': '"x"', 'esc': 'árbol'}, dict(pairs)
    y = to_yaml(pairs)
    assert read_back(y) == dict(pairs) and 'flag: "on"' in y and 'server:\n  port: "8080"' in y
    assert check([('a', '1'), ('a.b', '2')]) and check([('list[0]', 'x')]) and not check(pairs)
    print('ok')
