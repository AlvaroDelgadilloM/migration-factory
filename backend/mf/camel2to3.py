"""Camel 2 -> 3 as an automatic, gated step (it used to be a manual review only).

What it changes, all verified against the real jars or the Camel 3 migration guide:
- types that moved package (rules/camel/camel2-to-camel3-types.json, generated from camel-core 2.24.3 vs 3.22.4): OpenRewrite
  ChangeType, type-attributed on the project's Camel 2 classpath;
- @EndpointInject / @Produce / @Consume `uri=` -> `value=` (Camel 4 removed `uri`);
- Simple language `${property.x}` -> `${exchangeProperty.x}` (the `property` function was removed in Camel 3): located edits
  from the CAMEL2_SIMPLE_PROPERTY findings (file and line), never a blind replace.
Classes removed in Camel 3 are not guessed: the compile gate reports them and they stay as manual actions."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

RECIPE = 'mf.camel.Camel2To3'
ANNOTATIONS = ('org.apache.camel.EndpointInject', 'org.apache.camel.Produce', 'org.apache.camel.Consume')


@lru_cache
def types() -> dict:
    base = Path(os.environ.get('MF_RULES_CATALOG', Path(__file__).resolve().parents[2] / 'rules' / 'catalog.json')).parent
    return json.loads((base / 'camel' / 'camel2-to-camel3-types.json').read_text(encoding='utf-8'))


def recipe_items() -> list[tuple[str, dict]]:
    """OpenRewrite recipe list (rendered by planning.recipe_yaml)."""
    items = [('org.openrewrite.java.ChangeType', {'oldFullyQualifiedTypeName': old, 'newFullyQualifiedTypeName': new, 'ignoreDefinition': True})
             for old, new in sorted(types()['moves'].items())]
    return items + [('org.openrewrite.java.ChangeAnnotationAttributeName',
                     {'annotationType': a, 'oldAttributeName': 'uri', 'newAttributeName': 'value'}) for a in ANNOTATIONS]


def simple_edits(findings) -> list[dict]:
    return [{'op': 'simple-property', 'file': f.file, 'line': f.line} for f in findings]


if __name__ == '__main__':  # self-check
    items = dict((o['oldFullyQualifiedTypeName'], o['newFullyQualifiedTypeName']) for r, o in recipe_items() if r.endswith('ChangeType'))
    assert items['org.apache.camel.impl.DefaultExchange'] == 'org.apache.camel.support.DefaultExchange'
    assert 'org.apache.camel.spring.Main' not in items and sum(r.endswith('AttributeName') for r, _ in recipe_items()) == 3
    print('ok', len(items))
