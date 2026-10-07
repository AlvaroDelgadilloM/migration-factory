"""Refactors applied by modernization (only on the isolated copy, each one gated by build + tests)."""
from pathlib import Path

from .. import transform
from . import java
from .detect import field_injection_candidates


def field_to_constructor(src: str) -> tuple[str, list[str]]:
    """Move @Autowired/@Inject fields into a constructor for classes without constructors. Returns (new_src, classes)."""
    parsed = java.parse(src)
    edits, done = [], []  # (start, end, replacement) on original offsets
    for c in field_injection_candidates(parsed):
        if c['blockedBy']:
            continue
        cdi = any(f['annotations'] == ['Inject'] for f in c['fields'])
        for f in c['fields']:
            text = src[f['start']:f['end']]
            ann = java.ANNOT.match(text)
            rest = text[ann.end():].lstrip()
            mods = [w for w in rest.split() if w in java.MODIFIERS]
            decl = rest
            for w in mods:
                decl = decl.replace(w + ' ', '', 1)
            access = next((w for w in mods if w in ('private', 'protected', 'public')), 'private')
            others = ' '.join(w for w in mods if w not in ('private', 'protected', 'public'))
            edits.append((f['start'], f['end'], f"{access} final {others + ' ' if others else ''}{decl.strip()}"))
        last = c['fields'][-1]
        indent = src[src.rfind('\n', 0, last['start']) + 1:last['start']]
        indent = indent if not indent.strip() else '    '
        params = ', '.join(f"{f['type']} {f['name']}" for f in c['fields'])
        body = ''.join(f"\n{indent}    this.{f['name']} = {f['name']};" for f in c['fields'])
        ctor = (f"\n\n{indent}@Inject" if cdi else '\n') + f"\n{indent}public {c['type']['name']}({params}) {{{body}\n{indent}}}"
        semi = last['end']  # index of ';'
        edits.append((semi + 1, semi + 1, ctor))
        done.append(c['type']['name'])
    for start, end, rep in sorted(edits, key=lambda e: e[0], reverse=True):
        src = src[:start] + rep + src[end:]
    return src, done


def apply_field_injection(root: Path, files) -> list[dict]:
    out = []
    for rel in files:
        p = root / rel
        before = p.read_text(encoding='utf-8')
        after, classes = field_to_constructor(before)
        if classes:
            p.write_text(after, encoding='utf-8')
            out.append({'file': rel, 'classes': classes, 'applied': True})
    return out


def apply_config(root: Path, endpoints, runtime: str) -> list[dict]:
    """Literal http/https endpoint URLs -> {{services.<key>.url}} with the current value as default (no behaviour change)."""
    edits = [{'op': 'scheme', 'file': e['file'], 'line': e['line'], 'old': e['component'], 'new': e['component'], 'externalize': True,
              'rule': 'MOD_HARDCODED_CONFIG'} for e in endpoints]
    return transform.apply_edits(root, edits, runtime)


def apply_secrets(root: Path, findings, runtime: str) -> list[dict]:
    edits = transform.plan_edits([], [{'ruleId': 'HARDCODED_SECRET', 'file': f['file'], 'line': f['line']} for f in findings])
    return transform.apply_edits(root, edits, runtime)


if __name__ == '__main__':  # self-check
    code = '''package demo;

import org.springframework.beans.factory.annotation.Autowired;

@Service
public class OrderService {
    @Autowired
    private OrderRepo repo;
    @Autowired private Map<String, Integer> cache;

    public String find(String id) { return repo.find(id); }
}
'''
    out, classes = field_to_constructor(code)
    assert classes == ['OrderService'], classes
    assert 'private final OrderRepo repo;' in out and 'private final Map<String, Integer> cache;' in out and '@Autowired' not in out.split('@Service')[1]
    assert 'public OrderService(OrderRepo repo, Map<String, Integer> cache) {\n        this.repo = repo;\n        this.cache = cache;\n    }' in out, out
    java.parse(out)  # still readable
    cdi, _ = field_to_constructor(code.replace('@Autowired\n    private', '@Inject\n    private').replace('@Autowired private', '@Inject private'))
    assert '@Inject\n    public OrderService(' in cdi
    blocked = code.replace('    public String find', '    public OrderService() {}\n    public String find')
    assert field_to_constructor(blocked) == (blocked, [])  # existing constructor: never guessed
    print('ok')
