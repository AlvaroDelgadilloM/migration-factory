"""Scanner tests (stdlib unittest). Run: python -m unittest discover -s tests -v"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from mf.analysis.report import render  # noqa: E402
from mf.analysis.scanner import load_catalog, redact_uri, scan  # noqa: E402


def write(base, rel, text):
    p = Path(base, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')


class ScannerTests(unittest.TestCase):
    def test_fixture(self):
        r = scan(ROOT / 'examples/legacy')
        self.assertEqual(r['summary']['routesJavaApproximate'], 1)
        self.assertEqual(r['summary']['routesXmlParsed'], 1)
        dep = r['modules'][0]['dependencies'][0]
        self.assertEqual((dep['versionObserved'], dep['versionResolved']), ('${camel.version}', '2.24.3'))
        self.assertIn('vm', r['components'])

    def test_finding_ids_are_stable_and_not_overwritten(self):
        r = scan(ROOT / 'examples/legacy')
        fp = {f['fingerprint'] for f in r['findings']}
        self.assertIn('CAMEL_VM:routes.xml:1', fp)
        self.assertEqual(len(fp), len(r['findings']))

    def test_java_se_not_jakarta(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, 'A.java', 'import javax.sql.DataSource;\nimport javax.crypto.Cipher;\nimport javax.transaction.xa.XAResource;')
            self.assertFalse(scan(d)['findings'])

    def test_bad_xml_and_dtd(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, 'bad.xml', '<route>')
            write(d, 'dtd.xml', '<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>')
            self.assertEqual({e['code'] for e in scan(d)['errors']}, {'INVALID_XML', 'XML_DTD_REJECTED'})

    def test_exclusion_and_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, 'target/A.java', 'import javax.jms.X;')
            Path(d, 'A.java').symlink_to(Path(d, 'target/A.java'))
            Path(d, 'linkdir').symlink_to(Path(d, 'target'))
            r = scan(d)
            self.assertFalse(r['findings'])
            self.assertIn('SYMLINK_IGNORED', {e['code'] for e in r['errors']})

    def test_html_escape(self):
        r = {'summary': {}, 'findings': [{'file': '<script>', 'line': 1, 'ruleId': 'x', 'severity': 'x', 'classification': 'x', 'recommendation': 'x'}], 'errors': []}
        self.assertNotIn('<script>', render(r))
        self.assertIn('&lt;script&gt;', render(r))

    def test_target(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(scan(d, 'quarkus')['target'], 'quarkus')
            with self.assertRaises(ValueError):
                scan(d, 'invalid')

    def test_comments_do_not_count(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, 'R.java', '// from("vm:a")\n/* import javax.jms.X;\n from("jms:q") */\nclass R {}')
            write(d, 'r.xml', '<routes><!-- <route><from uri="vm:x"/></route> --></routes>')
            r = scan(d)
            self.assertFalse(r['findings'])
            self.assertFalse(r['routes'])

    def test_dynamic_endpoints_are_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, 'R.java', 'class R { void c(){ from(uri).toD("log:${header.x}"); from("direct:" + name); } }')
            routes = scan(d)['routes']
            self.assertEqual(len(routes), 2)
            self.assertTrue(all(r['dynamicFrom'] and r['fromUri'] is None for r in routes))
            self.assertTrue(routes[0]['endpoints'][1]['dynamic'])

    def test_xml_route_lines_and_redaction(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, 'routes.xml', '<routes>\n <route id="a">\n  <from uri="ftp://bob:pw@host/x?password=s3cret&amp;binary=true"/>\n </route>\n</routes>')
            route = scan(d)['routes'][0]
            self.assertEqual((route['line'], route['routeId']), (2, 'a'))
            self.assertNotIn('s3cret', route['fromUri'])
            self.assertNotIn('bob:pw', route['fromUri'])
            self.assertEqual(route['endpoints'][0]['line'], 3)
        self.assertEqual(redact_uri('jms:q?token=abc'), 'jms:q?token=***')

    def test_maven_multimodule_parent_properties_and_local_bom(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, 'pom.xml', '<project><groupId>g</groupId><artifactId>parent</artifactId><version>1</version><packaging>pom</packaging>'
                  '<properties><camel.version>2.25.4</camel.version><maven.compiler.release>11</maven.compiler.release></properties>'
                  '<modules><module>a</module><module>bom</module></modules>'
                  '<dependencyManagement><dependencies><dependency><groupId>g</groupId><artifactId>bom</artifactId><version>1</version>'
                  '<type>pom</type><scope>import</scope></dependency>'
                  '<dependency><groupId>org.external</groupId><artifactId>ext-bom</artifactId><version>9</version><type>pom</type><scope>import</scope></dependency>'
                  '</dependencies></dependencyManagement></project>')
            write(d, 'bom/pom.xml', '<project><groupId>g</groupId><artifactId>bom</artifactId><version>1</version><packaging>pom</packaging>'
                  '<dependencyManagement><dependencies><dependency><groupId>org.apache.camel</groupId><artifactId>camel-core</artifactId>'
                  '<version>2.25.4</version></dependency></dependencies></dependencyManagement></project>')
            write(d, 'a/pom.xml', '<project><parent><groupId>g</groupId><artifactId>parent</artifactId><version>1</version></parent>'
                  '<artifactId>a</artifactId><dependencies>'
                  '<dependency><groupId>org.apache.camel</groupId><artifactId>camel-core</artifactId></dependency>'
                  '<dependency><groupId>x</groupId><artifactId>unknown</artifactId></dependency></dependencies></project>')
            r = scan(d)
            mod = next(m for m in r['modules'] if m['artifactId'] == 'a')
            self.assertEqual(mod['javaResolved'], '11')
            self.assertEqual(mod['version'], '1')
            deps = {x['artifactId']: x for x in mod['dependencies']}
            self.assertEqual((deps['camel-core']['versionResolved'], deps['camel-core']['versionSource']), ('2.25.4', 'bom-local'))
            self.assertIsNone(deps['unknown']['versionResolved'])
            self.assertEqual(mod['resolution'], 'static-partial')
            self.assertTrue(any('ext-bom' in u for u in mod['unresolved']))
            rules = [f['ruleId'] for f in r['findings'] if f['file'] == 'a/pom.xml']
            self.assertEqual(rules.count('CAMEL2_VERSION'), 1)
            self.assertIn('MAVEN_UNRESOLVED', rules)

    def test_external_parent_unresolved(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, 'pom.xml', '<project><parent><groupId>corp</groupId><artifactId>corp-parent</artifactId><version>7</version>'
                  '<relativePath/></parent><artifactId>x</artifactId><properties><v>${corp.version}</v></properties></project>')
            mod = scan(d)['modules'][0]
            self.assertTrue(mod['unresolved'])
            self.assertEqual(mod['groupId'], 'corp')

    def test_rule_examples(self):
        """Every text rule must match its positive examples and not its negative ones."""
        for rule in load_catalog()['rules']:
            if rule['detector'] != 'text':
                continue
            for ex in rule['examples']['positive']:
                self.assertTrue(rule['_re'].search(ex), f"{rule['id']} debería detectar {ex!r}")
            for ex in rule['examples']['negative']:
                excluded = rule.get('_exclude') and rule['_exclude'].search(ex)
                self.assertFalse(rule['_re'].search(ex) and not excluded, f"{rule['id']} no debería detectar {ex!r}")

    def test_secret_evidence_redacted_and_integrations(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, 'app.properties', 'db.password=Sup3rS3cret\nok.password=${DB_PASSWORD}')
            write(d, 'R.java', 'class R { void c(){ from("quartz2://t?cron=x").to("sftp://h/in").to("http4://api/x"); } }')
            r = scan(d)
            sec = [f for f in r['findings'] if f['ruleId'] == 'HARDCODED_SECRET']
            self.assertEqual([f['line'] for f in sec], [1])
            self.assertNotIn('Sup3rS3cret', sec[0]['evidence'])
            self.assertEqual({i['type'] for i in r['integrations']}, {'SCHEDULING', 'FILES', 'REST/HTTP'})
            self.assertIn('QUARTZ2', {f['ruleId'] for f in r['findings']})

    def test_pilot_fixture(self):
        r = scan(ROOT / 'examples/pilot-orders')
        self.assertEqual(r['summary']['modules'], 2)
        self.assertEqual(r['summary']['routesJavaApproximate'], 3)
        rules = {f['ruleId'] for f in r['findings']}
        self.assertTrue({'CAMEL2_VERSION', 'CAMEL_VM', 'JAKARTA_JMS', 'JMS', 'JNDI', 'PACKAGING_WAR'} <= rules)
        self.assertNotIn('CAMEL_MOVED_API', rules)


if __name__ == '__main__':
    unittest.main()
