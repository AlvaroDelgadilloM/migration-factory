"""Regression (real repo, common-epay): reading a secret from code is not a hardcoded secret, and one secret edit that cannot be
applied never rolls back the ones that were (it becomes a manual action with its reason)."""
import json
import shutil

from conftest import ROOT
from helpers import FakeMaven, install
from mf import cli_tool
from mf.analysis.scanner import scan

PILOT = ROOT / 'examples' / 'pilot-orders'
ACCEPT = 'CAMEL2_VERSION=Revisado contra la guía 2→3 en la fixture'


def secrets_in(root):
    return sorted((f['file'].rsplit('/', 1)[-1], f['line']) for f in scan(root)['findings'] if f['ruleId'] == 'HARDCODED_SECRET')


def test_code_reading_a_secret_is_not_a_hardcoded_secret(tmp_path):
    (tmp_path / 'src/main/java/demo').mkdir(parents=True)
    (tmp_path / 'src/main/resources').mkdir(parents=True)
    (tmp_path / 'src/main/java/demo/Cipher.java').write_text(
        'package demo;\nclass Cipher {\n  void p(org.apache.camel.Exchange exchange) {\n    String password;\n'
        '    password = exchange.getProperty("passwordKey", String.class);\n    this.token = token;\n    String secret = null;\n'
        '    String apiKey = "AKIAexample123";\n  }\n}\n')
    (tmp_path / 'src/main/resources/app.properties').write_text('db.user=app\ndb.password=s3cretValue\n')
    assert secrets_in(tmp_path) == [('Cipher.java', 8), ('app.properties', 2)]


def test_an_unappliable_secret_edit_does_not_roll_back_the_others(tmp_path, monkeypatch):
    install(monkeypatch, FakeMaven(effective_ok=True))
    src = tmp_path / 'legacy'
    shutil.copytree(PILOT, src)
    res = src / 'orders-routes/src/main/resources'
    res.mkdir(parents=True, exist_ok=True)
    (res / 'legacy.properties').write_text('db.password=s3cretValue\n')
    java = src / 'orders-routes/src/main/java/demo/orders/Keys.java'
    java.write_text('package demo.orders;\nclass Keys {\n  String apiKey = "AKIAexample123";\n}\n')   # a Java literal, not a URI parameter
    out = tmp_path / 'out'
    cli_tool.main(['migrate', str(src), '--output', str(out), '--accept', ACCEPT, '--skip-baseline'])
    rep = json.loads((out / 'reports/migration-report.json').read_text())
    step = next(s for s in rep['steps'] if s['key'] == 'secret-remediation')
    assert step['state'] == 'APPLIED', step                                        # before: ROLLED_BACK, every edit lost
    props = (out / 'migrated-project/orders-routes/src/main/resources/legacy.properties').read_text()
    assert 's3cretValue' not in props and '${MF_SECRET_' in props
    assert [(e['file'].rsplit('/', 1)[-1], e['line']) for e in rep['editsNotApplied']] == [('Keys.java', 3)]
    assert 'Keys.java:3' in (out / 'reports/manual-actions.md').read_text()
