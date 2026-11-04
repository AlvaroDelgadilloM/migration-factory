"""Camel 2 -> 3 automatic step: relocations generated from the jars, annotation uri= -> value=, Simple property -> exchangeProperty.
Verified end to end with real Maven on examples/camel2-api (2/2 baseline tests -> candidate on Camel 4.14 3/3, ESTADO)."""
from pathlib import Path

from mf import camel2to3, cli_tool, transform

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples/camel2-api'
ACCEPT = {'CAMEL2_VERSION': 'receta camel-2-to-3 revisada'}


def test_relocations_come_from_the_jars_and_skip_what_changes_meaning():
    t = camel2to3.types()
    assert t['moves']['org.apache.camel.impl.DefaultExchange'] == 'org.apache.camel.support.DefaultExchange'
    assert t['moves']['org.apache.camel.processor.aggregate.AggregationStrategy'] == 'org.apache.camel.AggregationStrategy'
    assert 'org.apache.camel.spring.Main' in t['excluded'] and 'org.apache.camel.spring.Main' not in t['moves']
    assert 'org.apache.camel.impl.DefaultCamelContext' not in t['moves']                    # same name in Camel 3: untouched


def test_plan_runs_camel2_to_3_as_a_recipe_then_the_simple_language_edits():
    report = cli_tool.analyze(EXAMPLE, 'spring', None, False, lambda m: None)
    plan = cli_tool.build_plan(report, 'spring', '21', ACCEPT)
    steps = {s['key']: s for s in plan['steps']}
    c23 = steps['camel-2-to-3']
    assert c23['kind'] == 'recipe' and c23['recipe']['activeRecipes'] == [camel2to3.RECIPE]
    assert '"org.apache.camel.support.service.ServiceSupport"' in c23['recipe']['yaml'] and '"uri"' in c23['recipe']['yaml']
    simple = steps['camel2-simple-language']
    assert simple['dependsOn'] == ['camel-2-to-3'] and {e['line'] for e in simple['recipe']['edits']} == {14, 15}
    assert steps['camel-3-to-4']['dependsOn'] == ['camel2-simple-language']


def test_simple_property_edit_is_located_and_leaves_other_placeholders(tmp_path):
    f = tmp_path / 'R.java'
    f.write_text('a\n.log("${property.id} ${properties:x} {{y}} $simple{property[k]}")\n')
    res = transform.apply_edits(tmp_path, [{'op': 'simple-property', 'file': 'R.java', 'line': 2},
                                           {'op': 'simple-property', 'file': 'R.java', 'line': 1}], 'spring')
    assert [r['applied'] for r in res] == [True, False]
    assert f.read_text().split('\n')[1] == '.log("${exchangeProperty.id} ${properties:x} {{y}} $simple{exchangeProperty[k]}")'


def test_cxf_classes_that_camel4_moved_go_with_the_camel2_to_3_recipe():
    """examples/jboss-pedidos-completo: SOAP in PAYLOAD mode; the official 3->4 recipe leaves CxfPayload in its Camel 2 package."""
    moves = dict((o['oldFullyQualifiedTypeName'], o['newFullyQualifiedTypeName']) for r, o in camel2to3.recipe_items() if r.endswith('ChangeType'))
    assert moves['org.apache.camel.component.cxf.CxfPayload'] == 'org.apache.camel.component.cxf.common.CxfPayload'
    assert moves['org.apache.camel.component.cxf.DataFormat'] == 'org.apache.camel.component.cxf.common.DataFormat'
    assert moves['org.apache.camel.impl.DefaultExchange'] == 'org.apache.camel.support.DefaultExchange'   # the generated map is kept


def test_logging_error_handler_becomes_a_dead_letter_channel_to_a_log_endpoint(tmp_path):
    f = tmp_path / 'R.java'
    f.write_text('from("jms:q").errorHandler(loggingErrorHandler())\nerrorHandler(loggingErrorHandler("pedidos"));\n'
                 'errorHandler(loggingErrorHandler(LOG, LoggingLevel.WARN));\n')
    res = transform.apply_edits(tmp_path, camel2to3.error_handler_edits([type('F', (), {'file': 'R.java', 'line': n}) for n in (1, 2, 3)]), 'spring')
    assert [r['applied'] for r in res] == [True, True, False] and 'revisar a mano' in res[2]['reason']
    lines = f.read_text().split('\n')
    assert lines[0] == 'from("jms:q").errorHandler(deadLetterChannel("log:" + getClass().getName() + "?level=ERROR&showException=true"))'
    assert lines[1] == 'errorHandler(deadLetterChannel("log:pedidos?level=ERROR&showException=true"));'
