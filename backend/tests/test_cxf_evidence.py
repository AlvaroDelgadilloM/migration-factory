"""camel-cxf-evidence-corrections 33-42: CXF semantic mapping, ProjectEvidence vs DependencyEvidence, confidence levels,
report format, Blueprint, SUPPORTED semantics and Red Hat Fuse / Karaf vendor BOMs (Maven faked)."""
import functools
import json

from helpers import FakeMaven, install
from mf import camel_compat, cli_tool, preflight
from mf.analysis.scanner import scan

T = '4.14.0'


def ev(**tokens):
    return {k.replace('__', ':').replace('_', '.'): [{'file': f, 'line': ln}] for k, (f, ln) in tokens.items()}


def test_soap_blueprint_is_conditional_with_runtime_migration():                                        # doc 40 test 1
    e = {'cxf:bean:': [{'file': 'src/main/resources/OSGI-INF/blueprint/routes.xml', 'line': 42}],
         'OSGI-INF/blueprint': [{'file': 'src/main/resources/OSGI-INF/blueprint/routes.xml', 'line': None}]}
    d = camel_compat.decide('camel-cxf', {}, T, evidence=e, packagings=['bundle'])
    assert (d['status'], d['detectedUsage'], d['projectContext']['runtime'], d['autoMigration']) == \
        ('ARCHITECTURAL_MAPPING', 'SOAP', 'OSGI_BLUEPRINT', 'CONDITIONAL')
    assert d['runtimeMigration'] == 'REQUIRED' and d['migrationConfidence'] != 'HIGH'


def test_rest_spring_xml_and_ambiguous_cxf():                                                           # tests 2 and 6
    e = {'cxf:rsServer': [{'file': 'ctx.xml', 'line': 3}], 'springframework.org/schema/beans': [{'file': 'ctx.xml', 'line': 2}]}
    assert camel_compat.decide('camel-cxf', {}, T, evidence=e)['target'] == 'camel-cxf-spring-rest'
    for usage in ({}, {'cxf:': ['only-a-namespace-prefix.xml']}):
        d = camel_compat.decide('camel-cxf', usage, T)
        assert d['action'] == 'BLOCK' and d['code'] == 'CXF_USAGE_AMBIGUOUS' and d.get('target') is None   # never camel-cxf-soap by name


def test_evidence_is_isolated_per_dependency(tmp_path):                                                 # test 3, from a real scan
    (tmp_path / 'src/main/resources/OSGI-INF/blueprint').mkdir(parents=True)
    (tmp_path / 'src/main/java/demo').mkdir(parents=True)
    (tmp_path / 'src/main/resources/OSGI-INF/blueprint/routes.xml').write_text(
        '<blueprint xmlns="http://www.osgi.org/xmlns/blueprint/v1.0.0">\n<camelContext xmlns="http://camel.apache.org/schema/blueprint">\n'
        '<route><from uri="cxf:bean:orders"/><to uri="direct:x"/></route>\n</camelContext>\n</blueprint>\n')
    (tmp_path / 'src/main/java/demo/Order.java').write_text('package demo;\nimport javax.xml.bind.annotation.XmlRootElement;\n'
                                                            '@XmlRootElement\npublic class Order {}\n')
    (tmp_path / 'src/main/java/demo/Api.java').write_text('package demo;\npublic class Api extends org.apache.camel.builder.RouteBuilder {\n'
                                                          '  public void configure() {\n    restConfiguration().apiContextPath("/doc");\n  }\n}\n')
    s = scan(tmp_path)['summary']
    vals = lambda a: {(x['value'], x['file'].rsplit('/', 1)[-1], x['line']) for x in
                      camel_compat.decide(a, s['usage'], T, evidence=s['usageEvidence'])['dependencyEvidence']}
    assert vals('camel-cxf') == {('cxf:bean:', 'routes.xml', 3)}
    assert vals('camel-jaxb') == {('javax.xml.bind', 'Order.java', 2), ('@XmlRootElement', 'Order.java', 3)}
    assert vals('camel-swagger-java') == {('restConfiguration', 'Api.java', 4), ('apiContextPath', 'Api.java', 4)}
    d = camel_compat.decide('camel-cxf', s['usage'], T, evidence=s['usageEvidence'])
    assert d['projectContext']['runtime'] == 'OSGI_BLUEPRINT' and d['detectedUsage'] == 'SOAP'


def test_no_specific_evidence_lowers_usage_confidence():                                                # test 4
    d = camel_compat.decide('camel-jaxb', {}, T, evidence=ev(cxf__bean__=('r.xml', 5)), tests=4)
    assert d['dependencyEvidence'] == [] and d['usageConfidence'] == 'LOW' and d['migrationConfidence'] in ('MEDIUM', 'LOW')
    assert d['artifactConfidence'] == 'HIGH'                       # the artifact exists: that alone never makes the migration HIGH
    md = camel_compat.report_markdown([d])
    assert 'Dependency evidence:\nNONE' in md and 'Source migration:\nCONDITIONAL' in md and 'Prerequisites:' in md


def test_blueprint_reports_target_runtimes_not_artifacts():                                            # test 5 / doc 37
    md = camel_compat.report_markdown([camel_compat.decide('camel-blueprint', {}, T)])
    assert 'Artifact target:\nN/A' in md and '- Spring Boot 3\n- Quarkus 3\n- Camel Main' in md
    assert 'Auto dependency migration:\nNO' in md and 'Runtime migration:\nREQUIRED' in md and 'no verificado en el BOM' not in md


FUSE = 'org.jboss.redhat-fuse:fuse-karaf-bom:7.10.0.fuse-sb2-7_10_1-00008-redhat-00001'
FUSE_POM = f'''<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.acme</groupId>
  <artifactId>common-schedule-exchangerate-service</artifactId>
  <version>1.0.0</version>
  <packaging>bundle</packaging>
  <properties>
    <camel.version>2.23.2</camel.version>
  </properties>
  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>org.jboss.redhat-fuse</groupId>
        <artifactId>fuse-karaf-bom</artifactId>
        <version>{FUSE.split(':')[2]}</version>
        <type>pom</type>
        <scope>import</scope>
      </dependency>
    </dependencies>
  </dependencyManagement>
  <dependencies>
    <dependency><groupId>org.apache.camel</groupId><artifactId>camel-core</artifactId></dependency>
    <dependency><groupId>org.apache.camel</groupId><artifactId>camel-blueprint</artifactId></dependency>
    <dependency><groupId>org.slf4j</groupId><artifactId>slf4j-api</artifactId></dependency>
  </dependencies>
</project>
'''


class FuseMaven(FakeMaven):
    def mvn(self, ctx, cwd, goals, name, timeout=None):
        if goals == ['validate']:
            self.calls.append((name, tuple(goals)))
            return self._result(ctx, name, 1, '[ERROR] Non-resolvable import POM: Could not find artifact '
                                f"{FUSE.replace('bom:', 'bom:pom:')} in mf-approved (https://repo1.maven.org/maven2) @ line 13, column 19")
        return super().mvn(ctx, cwd, goals, name, timeout)


def test_fuse_vendor_bom_blocks_platform_not_analysis(tmp_path, monkeypatch):                          # doc 42 cases 1-5, 7
    src = tmp_path / 'svc'
    (src / 'src/main/resources/OSGI-INF/blueprint').mkdir(parents=True)
    (src / 'pom.xml').write_text(FUSE_POM)
    (src / 'src/main/resources/OSGI-INF/blueprint/routes.xml').write_text(
        '<blueprint xmlns="http://www.osgi.org/xmlns/blueprint/v1.0.0">\n<camelContext xmlns="http://camel.apache.org/schema/blueprint">\n'
        '<route id="rates"><from uri="timer:rates"/><to uri="log:rates"/></route>\n</camelContext>\n</blueprint>\n')
    install(monkeypatch, FuseMaven())
    monkeypatch.setattr(preflight, 'repair', functools.partial(preflight.repair, exists=lambda *a: True))  # camel-bom 2.23.2 "exists"
    out = tmp_path / 'out'
    assert cli_tool.main(['migrate', str(src), '--output', str(out), '--accept', 'CAMEL2_VERSION=revisado contra la guía 2→3']) != 0
    rep = json.loads((out / 'reports/migration-report.json').read_text())
    assert rep['status'] == 'BLOCKED' and rep['baseline'] == 'BASELINE_BLOCKED_PLATFORM_BOM'
    assert rep['primaryReason'] == 'PRIVATE_VENDOR_REPOSITORY_REQUIRED'
    assert {'RED_HAT_FUSE_PLATFORM_DETECTED', 'VENDOR_BOM_UNRESOLVED', 'BASELINE_REPAIR_PARTIAL', 'DEPENDENCY_MANAGEMENT_INCOMPLETE'} <= set(rep['reasonCodes'])
    assert rep['platform']['platform'] == 'RED_HAT_FUSE_KARAF' and rep['platform']['vendorBoms'][0]['status'] == 'UNRESOLVED'
    assert rep['platform']['repair'] == {'status': 'PARTIAL', 'recovered': 2, 'stillUnresolved': ['org.slf4j:slf4j-api'],
                                         'recoveredArtifacts': ['org.apache.camel:camel-blueprint', 'org.apache.camel:camel-core']}
    assert rep['steps'] and all(s['state'] == 'BLOCKED_BY_BASELINE' and s['reason'] == 'VENDOR_BOM_UNRESOLVED' for s in rep['steps'])
    assert rep['migrationPlan'] == 'AVAILABLE' and (out / 'reports/migration-plan.md').exists()                       # §15
    assert rep['baselineStages'] == {'validate': 'FAIL', 'compile': 'NOT_EXECUTABLE', 'test': 'NOT_RUN'}
    snap = json.loads((out / 'reports/dependency-management-snapshot.json').read_text())
    slf = next(x for x in snap if x['artifact'] == 'org.slf4j:slf4j-api')
    assert slf['resolvedVersion'] is None and slf['sourceBom'] == 'org.jboss.redhat-fuse:fuse-karaf-bom'                # never guessed
    baseline_pom = (out / '.work/baseline/pom.xml').read_text() if (out / '.work/baseline/pom.xml').exists() else None
    assert baseline_pom is None or 'fuse-karaf-bom' in baseline_pom                                 # the vendor BOM is never replaced
    md = (out / 'reports/baseline-report.md').read_text()
    assert 'Source platform:\nRED_HAT_FUSE_KARAF' in md and 'Baseline repair:\nPARTIAL' in md and 'Static analysis:\nPASS' in md
    analysis = json.loads((out / 'reports/analysis.json').read_text())
    assert analysis['routes'] and analysis['baseline']['analysisStatus'] == 'PARTIAL_SUCCESS'                          # §13-14
