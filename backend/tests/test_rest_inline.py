"""Camel 4 removed inlined routes in the REST DSL: the plan separates them into direct: routes (mf.rest_inline). Real run with Maven:
examples/camel2-rest-catalog to Quarkus compiles and passes; the automatic test compares it with the original on WildFly."""
from pathlib import Path

from mf import cli_tool, rest_inline, transform

CATALOG = Path(__file__).resolve().parents[2] / 'examples/camel2-rest-catalog/app'


def test_plan_separates_inlined_rest_routes():
    report = cli_tool.analyze(CATALOG, 'quarkus', None, False, lambda m: None)
    plan = cli_tool.build_plan(report, 'quarkus', '21', {'CAMEL2_VERSION': 'receta camel-2-to-3 revisada'})
    step = next(s for s in plan['steps'] if s['key'] == 'camel-rest-inline')
    assert step['kind'] == 'transform' and step['recipe']['edits'] == [{'op': 'rest-inline', 'file': 'src/main/java/demo/catalog/CatalogRoutes.java',
                                                                        'line': step['recipe']['edits'][0]['line']}]
    decisions = {d['artifact']: d['action'] for s in plan['steps'] if s['key'] == 'camel-3-to-4' for d in s['recipe']['compat']}
    assert decisions['camel-servletlistener'] == 'REMOVE'                               # the runtime starts Camel now


def test_transform_keeps_route_ids_and_bodies(tmp_path):
    src = (CATALOG / 'src/main/java/demo/catalog/CatalogRoutes.java').read_text()
    new, ids = rest_inline.transform(src)
    assert ids == ['items', 'item', 'validate', 'stats'] and '.route()' not in new and 'endRest' not in new
    assert '.get("/{id}").to("direct:item")' in new and 'from("direct:item").routeId("item")' in new
    assert new.count('Catalog.') == src.count('Catalog.')                              # every body kept
    f = tmp_path / 'R.java'
    f.write_text(src)
    res = transform.apply_edits(tmp_path, [{'op': 'rest-inline', 'file': 'R.java', 'line': 1}], 'quarkus')
    assert res[0]['applied'] and f.read_text() == new
    assert not transform.apply_edits(tmp_path, [{'op': 'rest-inline', 'file': 'R.java', 'line': 1}], 'quarkus')[0]['applied']  # idempotent


def test_rest_dsl_component_survives_the_effective_pom_pass():
    from mf.analysis.scanner import finalize, load_catalog, scan
    report = scan(CATALOG, 'quarkus', load_catalog())
    assert report['components'] == ['rest']
    assert finalize(report, load_catalog())['components'] == ['rest']                  # second pass (apply_effective) keeps it
