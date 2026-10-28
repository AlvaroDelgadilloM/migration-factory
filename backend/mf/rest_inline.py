"""Camel 4 removed inlined routes in the REST DSL (`rest("/x").get().route() ... .endRest()`, common in Camel 2 code). Each inlined
route becomes its own route: the REST verb points to `direct:<routeId>` and a `from("direct:<routeId>")` route keeps the same
body and routeId, so route names, metrics and behaviour stay. Deterministic text transform over the Java source (strings, chars
and comments are skipped); the compile/test gate validates it. A rest(...) statement it cannot read is left untouched."""
from __future__ import annotations

import re

VERBS = ('get', 'post', 'put', 'delete', 'patch', 'head', 'options')


def _skip_literal(src: str, i: int) -> int:
    """Index after the string/char literal or comment starting at i (i itself when none starts there)."""
    if src.startswith('//', i):
        j = src.find('\n', i)
        return len(src) if j < 0 else j
    if src.startswith('/*', i):
        j = src.find('*/', i + 2)
        return len(src) if j < 0 else j + 2
    if src.startswith('"""', i):
        j = src.find('"""', i + 3)
        return len(src) if j < 0 else j + 3
    if src[i] in '"\'':
        q, j = src[i], i + 1
        while j < len(src) and src[j] != q:
            j += 2 if src[j] == '\\' else 1
        return j + 1
    return i


def _calls(stmt: str) -> list[tuple[int, int, str]]:
    """Top-level chain calls of a statement: (start of '.', end after ')', method name)."""
    out, i, depth = [], 0, 0
    while i < len(stmt):
        j = _skip_literal(stmt, i)
        if j != i:
            i = j
            continue
        c = stmt[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif c == '.' and depth == 0:
            m = re.match(r'\.\s*(\w+)\s*\(', stmt[i:])
            if m:
                k, d = i + m.end(), 1
                while k < len(stmt) and d:
                    k2 = _skip_literal(stmt, k)
                    if k2 != k:
                        k = k2
                        continue
                    d += (stmt[k] == '(') - (stmt[k] == ')')
                    k += 1
                out.append((i, k, m[1]))
                i = k
                continue
        i += 1
    return out


def _statements(src: str):
    """(start, end) of every statement that begins with rest( at the start of a line."""
    for m in re.finditer(r'(?m)^[ \t]*rest\s*\(', src):
        i, depth = m.start(), 0
        while i < len(src):
            j = _skip_literal(src, i)
            if j != i:
                i = j
                continue
            depth += (src[i] in '([{') - (src[i] in ')]}')
            if src[i] == ';' and depth == 0:
                yield m.start(), i + 1
                break
            i += 1


def transform(src: str) -> tuple[str, list[str]]:
    """Returns (new source, route ids extracted). Unchanged when there is no inlined route."""
    out, last, made = [], 0, []
    used = set(re.findall(r'direct:([\w.\-]+)', src))
    for start, end in _statements(src):
        stmt = src[start:end]
        calls = _calls(stmt)
        names = [c[2] for c in calls]
        if 'route' not in names:
            continue
        indent = re.match(r'[ \t]*', stmt)[0]
        pieces, routes, pos, n = [], [], 0, 0
        idx = 0
        ok = True
        while idx < len(calls):
            s, e, name = calls[idx]
            if name == 'route':
                try:
                    stop = next(k for k in range(idx + 1, len(calls)) if calls[k][2] == 'endRest')
                except StopIteration:
                    ok = False
                    break
                if idx == 0 or calls[idx - 1][2] not in VERBS:
                    ok = False
                    break
                body = stmt[e:calls[stop][0]]
                rid = re.match(r'\s*\.\s*routeId\s*\(\s*"([^"]+)"\s*\)', body)
                n += 1
                target = re.sub(r'[^\w.\-]', '-', rid[1]) if rid else f'rest-route-{len(made) + n}'
                while target in used:
                    target += '-rest'
                used.add(target)
                pieces.append(stmt[pos:s] + f'.to("direct:{target}")')
                routes.append((target, body if rid else f'.routeId("{target}")' + body))
                pos = calls[stop][1]
                idx = stop + 1
                continue
            idx += 1
        if not ok or not routes:
            continue
        pieces.append(stmt[pos:])
        new_stmt = ''.join(pieces)
        new_stmt = re.sub(r'\s*\.\s*endRest\s*\(\s*\)', '', new_stmt)  # endRest() left over after the last verb
        extra = ''.join(f'\n\n{indent}from("direct:{t}"){b.rstrip()};' for t, b in routes)
        out.append(src[last:start] + new_stmt + extra)
        last = end
        made += [t for t, _ in routes]
    if not made:
        return src, []
    out.append(src[last:])
    return ''.join(out), made


if __name__ == '__main__':  # self-check on the shape of examples/camel2-rest-catalog
    src = '''    public void configure() {
        rest("/items")
            .get().route().routeId("items")
                .setHeader("X", constant("a)b;"))
                .process(e -> e.getIn().setBody("{\\"x\\":1}"))
            .endRest()
            .post("/validate").route()
                .process(e -> e.getIn().setBody("ok"))
            .endRest();
        rest("/stats").get().to("direct:stats");
    }
'''
    new, ids = transform(src)
    assert ids == ['items', 'rest-route-2'], ids
    assert '.get().to("direct:items")' in new and '.post("/validate").to("direct:rest-route-2");' in new, new
    assert 'from("direct:items").routeId("items")' in new and 'constant("a)b;")' in new
    assert 'from("direct:rest-route-2").routeId("rest-route-2")' in new and '.route()' not in new and 'endRest' not in new
    assert 'rest("/stats").get().to("direct:stats");' in new           # no inlined route: untouched
    assert transform('rest("/a").get().to("direct:a");')[1] == []
    print('ok')
