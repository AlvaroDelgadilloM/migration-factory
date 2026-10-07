"""Structural Java reader for modernization detectors and the field-injection transform.

Comments and string/char literals are masked (same length, newlines kept) so braces and keywords are read only from
code; offsets in the masked text are valid in the original. Reads top-level types and their direct members. Not a full
AST: transforms that OpenRewrite offers are delegated to OpenRewrite (MOD_SAFE_CLEANUP)."""
import re

ANNOT = re.compile(r'@([\w.]+)(\s*\((?:[^()]|\([^()]*\))*\))?')
TYPE_DECL = re.compile(r'\b(class|interface|enum|record)\s+(\w+)')
MODIFIERS = {'public', 'protected', 'private', 'static', 'final', 'abstract', 'transient', 'volatile', 'synchronized', 'native', 'default', 'strictfp', 'sealed'}


def mask(src: str) -> str:
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith('//', i):
            j = src.find('\n', i)
            j = n if j < 0 else j
            out.append(' ' * (j - i)); i = j
        elif src.startswith('/*', i):
            j = src.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out.append(''.join('\n' if ch == '\n' else ' ' for ch in src[i:j])); i = j
        elif src.startswith('"""', i):
            j = src.find('"""', i + 3)
            j = n if j < 0 else j + 3
            out.append('"' + ''.join('\n' if ch == '\n' else ' ' for ch in src[i + 1:j - 1]) + '"'); i = j
        elif c in '"\'':
            j = i + 1
            while j < n and src[j] != c and src[j] != '\n':
                j += 2 if src[j] == '\\' else 1
            j = min(j + 1, n)
            out.append(c + ' ' * (j - i - 2) + (src[j - 1] if j - i >= 2 else '')); i = j
        else:
            out.append(c); i += 1
    return ''.join(out)


def match_brace(m: str, open_pos: int) -> int:
    depth = 0
    for k in range(open_pos, len(m)):
        if m[k] == '{':
            depth += 1
        elif m[k] == '}':
            depth -= 1
            if depth == 0:
                return k
    return len(m) - 1


def line_of(text: str, pos: int) -> int:
    return text.count('\n', 0, pos) + 1


def _annotations(seg: str):
    names, pos = [], 0
    while True:
        m = ANNOT.match(seg, pos)
        if not m:
            break
        names.append(m[1].rsplit('.', 1)[-1])
        pos = m.end()
        while pos < len(seg) and seg[pos].isspace():
            pos += 1
    return names, pos


def _member(src, m, start, end, terminator):
    """Classify the declaration text m[start:end] (end = index of ';' or '{')."""
    lead = len(m[start:end]) - len(m[start:end].lstrip())
    s = start + lead
    head = m[s:end]
    annots, apos = _annotations(head)
    rest = head[apos:].strip()
    words = rest.split()
    mods = [w for w in words if w in MODIFIERS]
    core = ' '.join(w for w in words if w not in MODIFIERS)
    info = {'start': s, 'end': end, 'line': line_of(m, s), 'annotations': annots, 'modifiers': mods, 'text': src[s:end]}
    if TYPE_DECL.search(core) and terminator == '{' and not core.startswith('new '):
        return info | {'kind': 'type', 'name': TYPE_DECL.search(core)[2]}
    if terminator == '{' and '(' in core:
        name = re.search(r'(\w+)\s*\(', core)[1]
        params = core[core.find('(') + 1:core.rfind(')')] if ')' in core else ''
        return info | {'kind': 'method', 'name': name, 'params': params.strip(), 'returnType': core[:core.find(name)].strip() or None}
    if terminator == '{':
        return info | {'kind': 'initializer'}
    if '(' in core.split('=')[0]:
        name = re.search(r'(\w+)\s*\(', core)[1]
        return info | {'kind': 'method', 'name': name, 'abstract': True, 'params': '', 'returnType': None}
    decl = core.split('=', 1)[0].strip()
    parts = decl.rsplit(None, 1)
    multi = ',' in re.sub(r'<[^<>]*>', '', decl)
    return info | {'kind': 'field', 'type': parts[0] if len(parts) == 2 else '', 'name': parts[-1], 'multi': multi,
                   'initialized': '=' in core}


def parse(src: str) -> dict:
    m = mask(src)
    pkg = re.search(r'^\s*package\s+([\w.]+)\s*;', m, re.M)
    out = {'package': pkg[1] if pkg else '', 'imports': re.findall(r'^\s*import\s+(?:static\s+)?([\w.*]+)\s*;', src, re.M), 'types': []}
    depth, i = 0, 0
    while i < len(m):
        if m[i] == '{' and depth == 0:
            head_start = max(m.rfind(';', 0, i), m.rfind('}', 0, i)) + 1
            head = m[head_start:i]
            td = TYPE_DECL.search(head)
            close = match_brace(m, i)
            if td:
                annots, apos = _annotations(head.lstrip())
                t = {'kind': td[1], 'name': td[2], 'line': line_of(m, head_start + len(head) - len(head.lstrip())),
                     'annotations': annots, 'header': src[head_start:i].strip(), 'bodyStart': i, 'bodyEnd': close,
                     'extends': (re.search(r'\bextends\s+([\w.<>, ]+?)(?:\s+implements\b|\s*$)', head) or [None, None])[1],
                     'abstract': bool(re.search(r'\babstract\b', head[:td.start()])), 'members': []}
                t['extends'] = t['extends'].strip() if t['extends'] else None
                _members(src, m, t)
                out['types'].append(t)
            i = close + 1
            continue
        i += 1
    return out


def _members(src, m, t):
    i, seg_start, end = t['bodyStart'] + 1, t['bodyStart'] + 1, t['bodyEnd']
    paren = 0
    while i < end:
        c = m[i]
        if c == '(':
            paren += 1
        elif c == ')':
            paren -= 1
        elif c == ';' and paren == 0:
            if m[seg_start:i].strip():
                t['members'].append(_member(src, m, seg_start, i, ';'))
            seg_start = i + 1
        elif c == '{' and paren == 0:
            close = match_brace(m, i)
            head = m[seg_start:i]
            if head.strip() and not head.strip().endswith(('=', ',')):  # skip array/anonymous initializers in field values
                mem = _member(src, m, seg_start, i, '{')
                mem |= {'bodyStart': i, 'bodyEnd': close, 'bodyLines': line_of(m, close) - line_of(m, i) + 1, 'endLine': line_of(m, close)}
                t['members'].append(mem)
                seg_start = close + 1
            i = close + 1
            continue
        i += 1


if __name__ == '__main__':  # self-check
    code = '''package demo;
import org.springframework.beans.factory.annotation.Autowired;
// class Fake {
@Component
public class OrderService extends Base {
    @Autowired
    private OrderRepo repo; // "}"
    @Autowired private Map<String, List<Integer>> cache;
    private final String name = "a;b{";
    private int a, b;
    public String find(String id) {
        if (id == null) { return "}"; }
        return repo.find(id);
    }
    static class Inner { int x; }
}
'''
    p = parse(code)
    t = p['types'][0]
    assert p['package'] == 'demo' and t['name'] == 'OrderService' and t['extends'] == 'Base' and t['annotations'] == ['Component']
    fields = [x for x in t['members'] if x['kind'] == 'field']
    assert [(f['name'], f['type'], f['annotations']) for f in fields[:2]] == [('repo', 'OrderRepo', ['Autowired']), ('cache', 'Map<String, List<Integer>>', ['Autowired'])]
    assert fields[2]['initialized'] and fields[3]['multi']
    meth = next(x for x in t['members'] if x['kind'] == 'method')
    assert meth['name'] == 'find' and meth['bodyLines'] == 4 and meth['params'] == 'String id'
    assert any(x['kind'] == 'type' and x['name'] == 'Inner' for x in t['members'])
    assert len(mask(code)) == len(code)
    print('ok')
