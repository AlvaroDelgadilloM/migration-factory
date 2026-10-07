"""Compile Error Classifier (camel-correcciones-compatibilidad 28): javac errors from a Maven log as structured records.

Confidence is a level (HIGH: file, line and kind recognised; MEDIUM: kind recognised, symbol missing; LOW: unknown kind),
never a percentage. A failed build with no parseable javac error is reported as such (UNPARSED_BUILD_FAILURE), so an
unknown error is never confused with the absence of a parser."""
from __future__ import annotations

import re

LINE = re.compile(r'^\[ERROR\]\s+(?P<path>/?[^\s:\[]+\.java):\[(?P<line>\d+),(?P<col>\d+)\]\s+(?P<msg>.+?)\s*$')
DETAIL = re.compile(r'^\[ERROR\]\s+(symbol|location)\s*:\s*(.+?)\s*$')
KINDS = [
    ('PACKAGE_DOES_NOT_EXIST', re.compile(r'package ([\w.]+) does not exist')),
    ('CANNOT_FIND_SYMBOL', re.compile(r'cannot find symbol')),
    ('METHOD_NOT_APPLICABLE', re.compile(r'method (\w+) in \S+ \S+ cannot be applied to given types')),
    ('CONSTRUCTOR_NOT_APPLICABLE', re.compile(r'constructor (\w+) in \S+ \S+ cannot be applied to given types')),
    ('INCOMPATIBLE_TYPES', re.compile(r'incompatible types')),
    ('METHOD_DOES_NOT_OVERRIDE', re.compile(r'method does not override or implement a method from a supertype')),
]


def _rel(path: str) -> str:
    """Project-relative path: after the candidate/baseline work tree, else from the module directly above src/."""
    for marker in ('/.work/candidate/', '/.work/baseline/', '/candidate/', '/baseline/', '/migrated-project/'):
        if marker in path:
            return path.split(marker, 1)[1]
    m = re.search(r'([\w.\-]+/src/(?:main|test)/java/.+)$', path)
    return m[1] if m else path


def parse(log_text: str) -> list[dict]:
    out, seen, cur = [], set(), None
    for raw in log_text.splitlines():
        m = LINE.match(raw.strip())
        if m:
            kind, symbol = 'UNKNOWN_JAVA_ERROR', None
            for k, rx in KINDS:
                km = rx.search(m['msg'])
                if km:
                    kind, symbol = k, (km.group(1) if km.groups() else None)
                    break
            key = (_rel(m['path']), int(m['line']), m['msg'])
            if key in seen:
                cur = None
                continue
            seen.add(key)
            cur = {'type': 'JAVA_COMPILATION_ERROR', 'file': key[0], 'line': key[1], 'column': int(m['col']), 'errorType': kind,
                   'symbol': symbol, 'message': m['msg'][:300]}
            out.append(cur)
            continue
        d = DETAIL.match(raw.strip())
        if d and cur is not None:
            if d[1] == 'symbol' and not cur['symbol']:
                cur['symbol'] = d[2].split()[-1]
            elif d[1] == 'location':
                cur['location'] = d[2][:200]
    for e in out:
        e['confidence'] = 'LOW' if e['errorType'] == 'UNKNOWN_JAVA_ERROR' else ('HIGH' if e['symbol'] else 'MEDIUM')
    return out


def summary(log_text: str, build_failed: bool) -> dict:
    errors = parse(log_text)
    if build_failed and not errors:
        parser = 'UNPARSED_BUILD_FAILURE' if re.search(r'COMPILATION ERROR|CompilationFailureException', log_text) else 'NOT_A_COMPILATION_ERROR'
    else:
        parser = 'PARSED' if errors else 'NO_ERRORS'
    by = {}
    for e in errors:
        by[e['errorType']] = by.get(e['errorType'], 0) + 1
    return {'parser': parser, 'count': len(errors), 'byType': by, 'errors': errors[:200]}


if __name__ == '__main__':  # self-check
    log = '''[ERROR] COMPILATION ERROR :
[ERROR] /w/.work/candidate/orders/src/main/java/demo/Api.java:[42,17] cannot find symbol
[ERROR]   symbol:   class SwaggerDefinition
[ERROR]   location: class demo.Api
[ERROR] /w/.work/candidate/orders/src/main/java/demo/Api.java:[3,24] package io.swagger.annotations does not exist
[ERROR] /w/.work/candidate/orders/src/main/java/demo/R.java:[9,5] method does not override or implement a method from a supertype
[ERROR] /w/.work/candidate/orders/src/main/java/demo/R.java:[12,9] constructor Foo in class demo.Foo cannot be applied to given types;
[ERROR] /w/.work/candidate/orders/src/main/java/demo/R.java:[20,9] some brand new javac message
[ERROR] /w/.work/candidate/orders/src/main/java/demo/Api.java:[42,17] cannot find symbol
'''
    e = parse(log)
    assert [x['errorType'] for x in e] == ['CANNOT_FIND_SYMBOL', 'PACKAGE_DOES_NOT_EXIST', 'METHOD_DOES_NOT_OVERRIDE', 'CONSTRUCTOR_NOT_APPLICABLE',
                                         'UNKNOWN_JAVA_ERROR']
    assert e[0] == {'type': 'JAVA_COMPILATION_ERROR', 'file': 'orders/src/main/java/demo/Api.java', 'line': 42, 'column': 17,
                    'errorType': 'CANNOT_FIND_SYMBOL', 'symbol': 'SwaggerDefinition', 'message': 'cannot find symbol',
                    'location': 'class demo.Api', 'confidence': 'HIGH'}
    assert e[1]['symbol'] == 'io.swagger.annotations' and e[4]['confidence'] == 'LOW'
    assert summary('[ERROR] COMPILATION ERROR :\n[ERROR] something odd', True)['parser'] == 'UNPARSED_BUILD_FAILURE'
    assert summary('[ERROR] Could not resolve dependencies', True)['parser'] == 'NOT_A_COMPILATION_ERROR'
    print('ok')
