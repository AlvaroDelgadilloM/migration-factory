"""Repository discovery (17-CORRECCION-MULTI-PROJECT-ROOT-AMBIGUOUS): SINGLE_PROJECT, MAVEN_MULTI_MODULE or
MULTI_PROJECT_REPOSITORY; internal dependency graph, cycles, migration order and waves; shared findings.
Several POMs without an aggregator are a workspace, never an error, and no parent POM is ever created to hide it.
Stdlib only (used by the scanner side, the CLI and the platform)."""
from __future__ import annotations

from collections import Counter
from pathlib import Path, PurePosixPath

SINGLE, MULTI_MODULE, MULTI_PROJECT, SELECTION = 'SINGLE_PROJECT', 'MAVEN_MULTI_MODULE', 'MULTI_PROJECT_REPOSITORY', 'ROOT_SELECTION_REQUIRED'


class RootSelectionRequired(ValueError):
    def __init__(self, info: dict):
        self.info = info
        names = ', '.join(p['name'] for p in info['projects'][:10])
        super().__init__(f"ROOT_SELECTION_REQUIRED: el origen es un {info['repositoryType']} con {len(info['projects'])} proyectos "
                         f"({names}); indique uno (--project NOMBRE) o use --workspace")


def _dir(pom: str) -> str:
    return str(PurePosixPath(pom).parent)


def _under(d: str, ancestor: str) -> bool:
    return ancestor == '.' or d == ancestor or d.startswith(ancestor + '/')


def common_ancestor(dirs) -> str:
    parts = [[] if d == '.' else d.split('/') for d in dirs]
    out = []
    for seg in zip(*parts):
        if len(set(seg)) != 1:
            break
        out.append(seg[0])
    return '/'.join(out) or '.'


def classify(rel_files, read_modules=None) -> dict:
    """rel_files: repository-relative paths (only pom.xml are used). read_modules(pom) -> list of <module> (optional)."""
    poms = sorted({p for p in rel_files if p == 'pom.xml' or p.endswith('/pom.xml')}, key=lambda p: (p.count('/'), p))
    if not poms:
        return {'repositoryType': None, 'error': 'NO_POM', 'projects': []}
    dirs = [_dir(p) for p in poms]
    tops = [d for d in dirs if not any(o != d and _under(d, o) for o in dirs)]  # project roots: no POM above them
    if len(tops) == 1:
        root = tops[0]
        modules = read_modules(f'{root}/pom.xml' if root != '.' else 'pom.xml') if read_modules else []
        kind = SINGLE if len(poms) == 1 or not modules else MULTI_MODULE
        return {'repositoryType': kind, 'root': root, 'workspaceRoot': root, 'aggregator': kind == MULTI_MODULE,
                'projects': [{'name': PurePosixPath(root).name or 'root', 'dir': root, 'pom': f'{root}/pom.xml' if root != '.' else 'pom.xml'}],
                'findings': []}
    ws = common_ancestor(tops)
    rel = lambda d: d if ws == '.' else d[len(ws) + 1:]
    projects = [{'name': rel(d), 'dir': d, 'pom': f'{d}/pom.xml'} for d in sorted(tops)]
    return {'repositoryType': MULTI_PROJECT, 'root': None, 'workspaceRoot': ws, 'aggregator': False, 'projects': projects,
            'findings': [{'type': 'ROOT_AMBIGUOUS_RESOLVED_AS_WORKSPACE', 'severity': 'INFO',
                          'detail': f'{len(tops)} pom.xml sin agregador común: se trata {ws} como workspace de proyectos independientes'}]}


def classify_dir(root: Path) -> dict:
    from .analysis import maven
    files = [p.relative_to(root).as_posix() for p in root.rglob('pom.xml')
             if not any(x in ('target', '.git', 'node_modules') for x in p.relative_to(root).parts)]

    def mods(rel):
        try:
            return maven.read_pom(root / rel, rel)['modules']
        except Exception:  # unreadable POM: reported later by the per-project analysis
            return []
    return classify(files, mods)


def project_root(rel_files, read_modules=None) -> str:
    """The single Maven root the caller needs; raises RootSelectionRequired for a multi-project repository."""
    info = classify(rel_files, read_modules)
    if info.get('error'):
        raise ValueError('NO_POM')
    if info['repositoryType'] == MULTI_PROJECT:
        raise RootSelectionRequired(info)
    return info['root']


def coordinates(root: Path, project: dict) -> tuple[set, list]:
    """(GA set the project produces, [(groupId, artifactId)] it depends on), from every POM of the project."""
    from .analysis import maven
    produced, deps = set(), []
    base = root / project['dir'] if project['dir'] != '.' else root
    for f in sorted(base.rglob('pom.xml')):
        if any(x in ('target', '.git') for x in f.relative_to(base).parts):
            continue
        try:
            pom = maven.read_pom(f, f.relative_to(root).as_posix())
        except Exception:
            continue
        produced.add((pom['groupId'], pom['artifactId']))
        deps += [(d['groupId'], d['artifactId']) for d in pom['dependencies']]
    return produced, deps


def dependency_graph(root: Path, projects) -> dict:
    owner, needs = {}, {}
    for p in projects:
        produced, deps = coordinates(root, p)
        for ga in produced:
            owner[ga] = p['name']
        needs[p['name']] = deps
    edges = sorted({(p, owner[ga]) for p, deps in needs.items() for ga in deps if ga in owner and owner[ga] != p})
    return {'nodes': [p['name'] for p in projects],
            'edges': [{'from': a, 'to': b, 'type': 'INTERNAL_DEPENDENCY'} for a, b in edges]}  # a depends on b


def _sccs(nodes, adj):
    index, low, stack, on, out, i = {}, {}, [], set(), [], [0]

    def strong(v):  # Tarjan (recursion depth = number of projects, small)
        index[v] = low[v] = i[0]
        i[0] += 1
        stack.append(v)
        on.add(v)
        for w in adj.get(v, ()):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            out.append(sorted(comp))
    for v in nodes:
        if v not in index:
            strong(v)
    return out


def plan_order(graph: dict) -> dict:
    """Topological waves (dependencies first). Projects in a cycle, or depending on one, are only removed from the
    automatic order (DEPENDENCY_CYCLE / BLOCKED_BY_CYCLE); everything else keeps its waves."""
    nodes = graph['nodes']
    adj = {}
    for e in graph['edges']:
        adj.setdefault(e['from'], []).append(e['to'])
    cycles = [c for c in _sccs(nodes, adj) if len(c) > 1 or c[0] in adj.get(c[0], [])]
    in_cycle = {n for c in cycles for n in c}
    blocked = set(in_cycle)
    changed = True
    while changed:
        changed = False
        for n in nodes:
            if n not in blocked and any(d in blocked for d in adj.get(n, [])):
                blocked.add(n)
                changed = True
    level = {}
    for _ in nodes:
        for n in nodes:
            if n in blocked or n in level:
                continue
            deps = adj.get(n, [])
            if all(d in level for d in deps):
                level[n] = 1 + max((level[d] for d in deps), default=0)
    waves = [{'wave': w, 'projects': sorted(n for n, lv in level.items() if lv == w)} for w in sorted(set(level.values()))]
    return {'order': [n for w in waves for n in w['projects']], 'waves': waves,
            'cycles': [{'type': 'DEPENDENCY_CYCLE', 'projects': c, 'severity': 'HIGH'} for c in cycles],
            'blocked': {n: ('DEPENDENCY_CYCLE' if n in in_cycle else 'BLOCKED_BY_CYCLE') for n in sorted(blocked)}}


SIGNALS = {  # workspace-wide metric -> predicate on one project's analysis
    'Java 8': lambda r: any(str(j).replace('1.', '', 1) == '8' for j in (r.get('java') if isinstance(r.get('java'), list) else [r.get('java')])),
    'Camel 2': lambda r: any(f['ruleId'] == 'CAMEL2_VERSION' for f in r['findings']),
    'javax.jms': lambda r: any(f['ruleId'] == 'JAKARTA_JMS' for f in r['findings']),
    'ActiveMQ legacy': lambda r: 'activemq' in r.get('components', []) or any('activemq' in d['artifactId'] for m in r['modules'] for d in m['dependencies']),
    'Spring XML': lambda r: any(f['ruleId'] == 'SPRING_XML' for f in r['findings']),
    'JBoss': lambda r: any(f['ruleId'].startswith('JBOSS') for f in r['findings']),
}


def shared_findings(reports: dict) -> dict:
    """reports: project name -> analysis. Metrics over the workspace and rules repeated in 2+ projects."""
    n = len(reports)
    metrics = {k: f'{sum(1 for r in reports.values() if pred(r))}/{n}' for k, pred in SIGNALS.items()}
    by_rule = {}
    for name, r in reports.items():
        for f in r['findings']:
            by_rule.setdefault(f['ruleId'], {'projects': set(), 'recommendation': f['recommendation'], 'severity': f['severity']})['projects'].add(name)
    common = [{'id': f'COMMON_RULE-{i:03d}', 'ruleId': rid, 'severity': v['severity'], 'projects': sorted(v['projects']),
               'detected': f"{rid} en {len(v['projects'])} proyectos", 'target': v['recommendation']}
              for i, (rid, v) in enumerate(sorted(((k, v) for k, v in by_rule.items() if len(v['projects']) > 1),
                                                 key=lambda kv: (-len(kv[1]['projects']), kv[0])), 1)]
    return {'projects': n, 'metrics': metrics, 'commonRules': common,
            'findingsByProject': {name: dict(Counter(f['ruleId'] for f in r['findings'])) for name, r in reports.items()}}


if __name__ == '__main__':  # self-check: acceptance cases 1-5 of the document
    assert classify(['project/pom.xml'])['repositoryType'] == SINGLE
    assert classify(['root/pom.xml', 'root/a/pom.xml', 'root/b/pom.xml'], lambda p: ['a', 'b'])['repositoryType'] == MULTI_MODULE
    info = classify(['develop/commons/common-activemq/pom.xml', 'develop/commons/common-catalogue/pom.xml',
                     'develop/commons/common-datasource/pom.xml', 'develop/commons/x/sub/pom.xml', 'develop/commons/x/pom.xml', 'README.md'])
    assert info['repositoryType'] == MULTI_PROJECT and info['workspaceRoot'] == 'develop/commons'
    assert [p['name'] for p in info['projects']] == ['common-activemq', 'common-catalogue', 'common-datasource', 'x']  # x/sub belongs to x
    assert info['findings'][0]['type'] == 'ROOT_AMBIGUOUS_RESOLVED_AS_WORKSPACE'
    try:
        project_root(['a/pom.xml', 'b/pom.xml'])
        raise AssertionError('must require a selection')
    except RootSelectionRequired as e:
        assert e.info['workspaceRoot'] == '.'
    g = {'nodes': ['ds', 'mq', 'rate', 'cat', 'epay'],
         'edges': [{'from': 'epay', 'to': 'ds'}, {'from': 'cat', 'to': 'mq'}, {'from': 'epay', 'to': 'cat'}]}
    o = plan_order(g)
    assert o['waves'] == [{'wave': 1, 'projects': ['ds', 'mq', 'rate']}, {'wave': 2, 'projects': ['cat']}, {'wave': 3, 'projects': ['epay']}]
    o = plan_order({'nodes': ['A', 'B', 'C', 'D', 'E'], 'edges': [{'from': 'A', 'to': 'B'}, {'from': 'B', 'to': 'C'}, {'from': 'C', 'to': 'A'},
                                                                  {'from': 'D', 'to': 'A'}]})
    assert o['cycles'] == [{'type': 'DEPENDENCY_CYCLE', 'projects': ['A', 'B', 'C'], 'severity': 'HIGH'}]
    assert o['blocked'] == {'A': 'DEPENDENCY_CYCLE', 'B': 'DEPENDENCY_CYCLE', 'C': 'DEPENDENCY_CYCLE', 'D': 'BLOCKED_BY_CYCLE'}
    assert o['order'] == ['E']  # only the affected projects leave the automatic order
    print('ok')
