"""Semantic view of a scan (improvements/02, levels 4-5): integration graph, migration action per finding, risks and
hints. Derived only from what the scanner observed; Java DSL routes stay approximate (regex), XML DSL is parsed."""
from __future__ import annotations

import re

from .. import rules as mrules

LINK_SCHEMES = {'direct', 'direct-vm', 'vm', 'seda', 'jms', 'activemq', 'sjms', 'sjms2', 'amqp'}
RISK_ORDER = ['none', 'info', 'low', 'medium', 'high']


def _link_key(uri):
    """Channel identity used to connect a producer of one route to the consumer of another (query ignored)."""
    m = re.match(r'([\w+-]+):(?://)?([^?]*)', uri or '')
    if not m or m[1] not in LINK_SCHEMES:
        return None
    scheme = 'direct' if m[1] in ('direct', 'direct-vm', 'vm', 'seda') else 'jms'  # in-JVM vs broker channel
    return f'{scheme}:{m[2].removeprefix("queue:")}'


def graph(report, kinds) -> dict:
    risk = {}
    for f in report['findings']:
        k = (f['file'], f['line'])
        if RISK_ORDER.index(f['severity']) > RISK_ORDER.index(risk.get(k, 'none')):
            risk[k] = f['severity']
    nodes, edges, consumers, producers = [], [], {}, []
    for i, r in enumerate(report['routes']):
        rid = f"route:{r['routeId'] or r['file'] + ':' + str(r['line'])}#{i}"
        nodes.append({'id': rid, 'kind': 'route', 'routeId': r['routeId'], 'dsl': r['dsl'], 'file': r['file'], 'line': r['line'],
                      'risk': risk.get((r['file'], r['line']), 'none')})
        for j, e in enumerate(r['endpoints']):
            nid = f'{rid}/ep{j}'
            nodes.append({'id': nid, 'kind': 'endpoint', 'type': kinds.get(e['component'], e['component'] or 'dynamic'),
                          'endpoint': e['uri'], 'dynamic': e['dynamic'], 'file': e['file'], 'line': e['line'],
                          'risk': risk.get((e['file'], e['line']), 'none')})
            consumer = e['kind'] in ('from', 'fromF')
            edges.append({'from': nid, 'to': rid, 'kind': 'consumes'} if consumer else {'from': rid, 'to': nid, 'kind': 'produces', 'order': j})
            key = None if e['dynamic'] else _link_key(e['uri'])
            if key:
                (consumers.setdefault(key, []).append(nid) if consumer else producers.append((key, nid)))
    for key, nid in producers:  # Route -> JMS/direct -> Route: the chain across routes
        edges += [{'from': nid, 'to': c, 'kind': 'channel', 'channel': key} for c in consumers.get(key, [])]
    return {'nodes': nodes, 'edges': edges, 'approximate': any(r['dsl'] == 'java' for r in report['routes'])}


def enrich(report, kinds) -> dict:
    data = mrules.load()
    for f in report['findings']:
        f['migrationAction'] = mrules.finding_action(f['ruleId'], f['classification'], data)
    mods = report['modules']
    javas = sorted({m.get('javaResolved') or m.get('javaObserved') for m in mods if m.get('javaResolved') or m.get('javaObserved')})
    camels = sorted({d['versionResolved'] for m in mods for d in m['dependencies']
                     if d['groupId'].startswith('org.apache.camel') and d.get('versionResolved')})
    rule_ids = {f['ruleId'] for f in report['findings']}
    osgi = rule_ids & {'OSGI_BUNDLE', 'BLUEPRINT', 'TARGET_RUNTIME_MIGRATION_REQUIRED'} or any(m['packaging'] == 'bundle' for m in mods)
    report['runtime'] = 'osgi-blueprint' if osgi else 'jboss' if rule_ids & {'JBOSS', 'JBOSS_DEPLOYMENT', 'JBOSS_SECURITY', 'EJB', 'JNDI'} else \
        ('servlet-container' if any(m['packaging'] in ('war', 'ear') for m in mods) else 'unknown')
    report['runtimeMigration'] = ({'sourceRuntime': 'OSGI_BLUEPRINT', 'targetRuntime': 'QUARKUS' if report.get('target') == 'quarkus' else 'SPRING_BOOT',
                                   'status': 'ARCHITECTURAL_MIGRATION',
                                   'signals': sorted(rule_ids & {'OSGI_BUNDLE', 'BLUEPRINT'}) + (['packaging=bundle'] if any(m['packaging'] == 'bundle' for m in mods) else [])}
                                  if osgi else None)
    report['java'] = javas[0] if len(javas) == 1 else javas
    report['camel'] = camels[0] if len(camels) == 1 else camels
    report['risks'] = [{k: f[k] for k in ('ruleId', 'severity', 'blocking', 'file', 'line', 'recommendation')}
                       for f in report['findings'] if f['severity'] == 'high' or f['blocking']]
    hints = {}
    for f in report['findings']:
        h = hints.setdefault(f['ruleId'], {'ruleId': f['ruleId'], 'action': f['migrationAction'], 'occurrences': 0,
                                           'recommendation': f['recommendation']})
        h['occurrences'] += 1
    report['migrationHints'] = sorted(hints.values(), key=lambda h: (h['action'], h['ruleId']))
    report['graph'] = graph(report, kinds)
    return report


if __name__ == '__main__':  # self-check
    ep = lambda c, u, k, line: {'component': c, 'uri': u, 'kind': k, 'dynamic': False, 'file': 'R.java', 'line': line}
    rep = {'modules': [{'packaging': 'war', 'javaObserved': '1.8', 'dependencies': [
               {'groupId': 'org.apache.camel', 'artifactId': 'camel-core', 'versionResolved': '2.24.3'}]}],
           'findings': [{'ruleId': 'HTTP4', 'classification': 'REVIEW', 'severity': 'medium', 'blocking': False, 'file': 'R.java', 'line': 3,
                         'recommendation': 'x'}],
           'routes': [{'routeId': 'in', 'dsl': 'java', 'file': 'R.java', 'line': 1,
                       'endpoints': [ep('jms', 'jms:queue:orders', 'from', 1), ep('direct', 'direct:pay', 'to', 2)]},
                      {'routeId': 'pay', 'dsl': 'java', 'file': 'R.java', 'line': 3,
                       'endpoints': [ep('direct', 'direct:pay', 'from', 3), ep('http4', 'http4://h/p', 'to', 3)]}]}
    out = enrich(rep, {'jms': 'JMS', 'http4': 'REST/HTTP'})
    assert out['java'] == '1.8' and out['camel'] == '2.24.3' and out['runtime'] == 'servlet-container'
    assert out['findings'][0]['migrationAction'] == 'replace' and out['migrationHints'][0]['occurrences'] == 1
    ch = [e for e in out['graph']['edges'] if e['kind'] == 'channel']
    assert len(ch) == 1 and ch[0]['channel'] == 'direct:pay'
    assert [n['risk'] for n in out['graph']['nodes'] if n.get('type') == 'REST/HTTP'] == ['medium']
    print('ok')
