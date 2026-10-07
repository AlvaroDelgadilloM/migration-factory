"""Declarative migration rules (rules/migration-rules.json, improvements/03) and confidence levels (improvements/05).

Confidence is a level, never a percentage: AUTO > AUTO_TEST > REVIEW > MANUAL (same vocabulary as plan steps).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path

# stdlib only: the scanner (and its CLI shim) must run without the API dependencies
RULES_PATH = Path(os.environ.get('MF_MIGRATION_RULES', Path(__file__).resolve().parents[2] / 'rules' / 'migration-rules.json'))
LEVELS = ('AUTO', 'AUTO_TEST', 'REVIEW', 'MANUAL')  # most to least certain
ACTIONS = {'replaceDependency', 'removeDependency', 'rewriteEndpointScheme', 'externalizeUrl'}
FINDING_ACTIONS = {'copy', 'replace', 'rewrite', 'remove', 'manual-review'}
_GA = re.compile(r'^[\w.\-]+:[\w.\-]+$')


@lru_cache
def load(path: str | None = None) -> dict:
    raw = Path(path or RULES_PATH).read_bytes()
    data = json.loads(raw)
    for r in data['rules']:  # fail at load time, never halfway through a migration
        assert r['level'] in LEVELS and set(r['actions']) <= ACTIONS and isinstance(r['rollback'], bool), r['id']
        assert _GA.match(r['source']['artifact']) and (r['target'] is None or _GA.match(r['target']['artifact'])), r['id']
        assert ('replaceDependency' in r['actions']) == (r['target'] is not None), r['id']
    assert all(v in FINDING_ACTIONS for k, v in data['findingActions'].items() if k != '_default')
    data['digest'] = hashlib.sha256(raw).hexdigest()
    return data


def _aid(ga):
    return ga.split(':', 1)[1]


def renamed(data=None) -> dict:
    return {_aid(r['source']['artifact']): _aid(r['target']['artifact']) for r in (data or load())['rules'] if 'replaceDependency' in r['actions']}


def removed(data=None) -> dict:
    return {_aid(r['source']['artifact']): r['rationale'] for r in (data or load())['rules'] if 'removeDependency' in r['actions']}


def scheme_renames(data=None) -> dict:
    out = {}
    for r in (data or load())['rules']:
        if 'rewriteEndpointScheme' in r['actions']:
            out.update(zip(r['source']['schemes'], r['target']['schemes']))
    return out


def externalized_schemes(data=None) -> set:
    return {s for r in (data or load())['rules'] if 'externalizeUrl' in r['actions'] for s in r['source']['schemes']}


def rule_for(artifact_or_scheme: str, data=None) -> dict | None:
    for r in (data or load())['rules']:
        if _aid(r['source']['artifact']) == artifact_or_scheme or artifact_or_scheme in r['source'].get('schemes', []):
            return r
    return None


def finding_action(rule_id: str, classification: str, data=None) -> str:
    fa = (data or load())['findingActions']
    return fa.get(rule_id) or fa['_default'].get(classification, 'manual-review')


def needs_approval(level: str, auto_up_to: str) -> bool:
    """True when `level` is less certain than the configured auto-apply threshold."""
    return LEVELS.index(level) > LEVELS.index(auto_up_to)


if __name__ == '__main__':  # self-check
    d = load()
    assert {'camel-http4': 'camel-http', 'camel-quartz2': 'camel-quartz', 'camel-cxf': 'camel-cxf-soap', 'camel-netty4': 'camel-netty'}.items() <= renamed(d).items()
    assert set(removed(d)) == {'camel-cdi'}
    assert scheme_renames(d) == {'http4': 'http', 'https4': 'https', 'quartz2': 'quartz', 'netty4': 'netty', 'netty4-http': 'netty-http',
                                 'mina2': 'mina', 'mongodb3': 'mongodb'}
    assert externalized_schemes(d) == {'http4', 'https4'}
    assert rule_for('https4', d)['id'] == 'CAMEL_HTTP4_TO_HTTP'
    assert finding_action('HTTP4', 'REVIEW', d) == 'replace' and finding_action('EJB', 'MANUAL', d) == 'manual-review'
    assert needs_approval('REVIEW', 'AUTO_TEST') and not needs_approval('AUTO_TEST', 'REVIEW') and needs_approval('MANUAL', 'REVIEW')
    print('ok')
