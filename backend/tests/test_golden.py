"""Golden tests (improvements/09): fixture input -> expected analysis and plan (offline: no Maven).
Regenerate after an intended change with MF_UPDATE_GOLDEN=1 and review the diff of tests/golden/*.json."""
import json
import os
from pathlib import Path

import pytest

from conftest import ROOT
from mf import cli_tool

HERE = Path(__file__).parent
FIXTURES = {'camel2-jboss': ROOT / 'examples' / 'pilot-orders', 'secrets-jms-jndi': ROOT / 'examples' / 'legacy-integrations',
            'camel2-spring-xml': HERE / 'fixtures' / 'camel2-spring-xml', 'camel3-springboot': HERE / 'fixtures' / 'camel3-springboot',
            'soap-cxf': HERE / 'fixtures' / 'soap-cxf'}


def digest(src: Path) -> dict:
    report = cli_tool.analyze(src, 'spring', None, False, lambda m: None)
    plan = cli_tool.build_plan(report, 'spring', '21', {'CAMEL2_VERSION': 'aceptado en la prueba golden'})
    rules = {}
    for f in report['findings']:
        rules[f['ruleId']] = rules.get(f['ruleId'], 0) + 1
    return {'runtime': report['runtime'], 'java': report['java'], 'camel': report['camel'],
            'findings': dict(sorted(rules.items())),
            'actions': dict(sorted({f['ruleId']: f['migrationAction'] for f in report['findings']}.items())),
            'integrations': report['summary']['integrationsByType'],
            'graph': {'routes': sum(n['kind'] == 'route' for n in report['graph']['nodes']),
                      'endpoints': sum(n['kind'] == 'endpoint' for n in report['graph']['nodes']),
                      'channels': sorted(e['channel'] for e in report['graph']['edges'] if e['kind'] == 'channel')},
            'plan': [[s['key'], s['classification'], s['state']] for s in plan['steps']]}


@pytest.mark.parametrize('name', sorted(FIXTURES))
def test_golden(name):
    got = digest(FIXTURES[name])
    path = HERE / 'golden' / f'{name}.json'
    if os.environ.get('MF_UPDATE_GOLDEN') == '1' or not path.exists():
        path.write_text(json.dumps(got, ensure_ascii=False, indent=2) + '\n')
        if os.environ.get('MF_UPDATE_GOLDEN') != '1':
            pytest.fail(f'golden creado: revisar {path.name} y volver a ejecutar')
    assert got == json.loads(path.read_text()), f'difiere de tests/golden/{name}.json (MF_UPDATE_GOLDEN=1 si el cambio es intencional)'
