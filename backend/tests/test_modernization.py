"""Modernization (12-PROMPT-CLAUDE-MODERNIZATION): tiers, approval, gate + automatic rollback, reports, source untouched.
Maven/OpenRewrite are faked; a real run is recorded in ESTADO_IMPLEMENTACION.md."""
import json

import pytest

from helpers import FakeMaven, install
from mf import cli_tool
from mf.worker.source import content_hash

POM = """<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>demo</groupId>
<artifactId>app</artifactId><version>1.0</version><properties><maven.compiler.release>21</maven.compiler.release></properties>
<dependencies><dependency><groupId>org.apache.camel.springboot</groupId><artifactId>camel-spring-boot-starter</artifactId>
<version>4.14.9</version></dependency></dependencies></project>"""

FILES = {
    'pom.xml': POM,
    'src/main/resources/application.yml': 'services:\n  erp-internal-8080-api:\n    url: ${X:http://other/}\n',
    'src/main/java/demo/app/OrderService.java': '''package demo.app;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

@Service
public class OrderService {
    @Autowired
    private OrderRepository repository;
    @Autowired
    private PriceClient prices;

    public String find(String id) {
        try { return repository.find(id); } catch (RuntimeException e) { }
        return null;
    }
}
''',
    'src/main/java/demo/app/OrderDto.java': '''package demo.app;

public class OrderDto {
    private final String id;
    private final long amount;

    public OrderDto(String id, long amount) { this.id = id; this.amount = amount; }
    public String getId() { return id; }
    public long getAmount() { return amount; }
}
''',
    'src/main/java/demo/app/OrdersRoute.java': '''package demo.app;

import org.apache.camel.builder.RouteBuilder;

public class OrdersRoute extends RouteBuilder {
    @Override
    public void configure() {
        from("direct:orders").routeId("orders")
            .process(exchange -> {
                String body = exchange.getIn().getBody(String.class);
                body = body.trim();
                body = body.toUpperCase();
                exchange.getIn().setHeader("len", body.length());
                exchange.getIn().setBody(body);
            })
            .to("sql:insert into orders(body) values (:#${body})")
            .to("jms:queue:orders.created")
            .to("http://erp.internal:8080/api/orders");
    }
}
''',
    'src/main/java/demo/app/AuditRoute.java': '''package demo.app;

import org.apache.camel.builder.RouteBuilder;

public class AuditRoute extends RouteBuilder {
    @Override
    public void configure() {
        from("direct:audit").routeId("audit").to("log:audit");
    }
}
''',
    'src/test/java/demo/app/OrderServiceTest.java': 'package demo.app;\nimport org.junit.jupiter.api.Test;\nclass OrderServiceTest { @Test void ok() {} }\n',
}


@pytest.fixture
def project(tmp_path):
    p = tmp_path / 'migrated'
    for rel, text in FILES.items():
        (p / rel).parent.mkdir(parents=True, exist_ok=True)
        (p / rel).write_text(text)
    return p


def _results(out):
    return {(e['rule'], e['status']): e for e in json.loads((out / 'reports' / 'refactors-applied.json').read_text())}


def test_default_run_applies_safe_refactors_and_only_proposes_the_rest(project, tmp_path, monkeypatch):
    install(monkeypatch, FakeMaven())
    before = content_hash(project)
    out = tmp_path / 'mod'
    assert cli_tool.main(['modernize', str(project), '--output', str(out)]) == 0
    assert content_hash(project) == before
    res = _results(out)
    status = {r: s for r, s in res}
    assert status['MOD_HARDCODED_CONFIG'] == 'APPLIED' and status['MOD_SAFE_CLEANUP'] == 'NO_CHANGES'
    assert status['MOD_FIELD_INJECTION'] == 'PROPOSED_ONLY'  # public contract: needs --approve
    for rule in ('MOD_LOGIC_IN_ROUTE', 'MOD_OUTBOX', 'MOD_ERROR_HANDLING', 'MOD_RESILIENCE', 'MOD_RECORD_DTO', 'MOD_EMPTY_CATCH'):
        assert status[rule] == 'PROPOSED_ONLY', rule  # ARCHITECTURE and behaviour/contract changes are never applied
    mp = out / 'modernized-project'
    route = (mp / 'src/main/java/demo/app/OrdersRoute.java').read_text()
    assert '"{{services.erp-internal-8080-api-2.url}}"' in route  # existing key reserved, never redefined
    yml = (mp / 'src/main/resources/application.yml').read_text()
    assert 'erp-internal-8080-api-2:\n    url: ${SERVICES_ERP_INTERNAL_8080_API_2_URL:http://erp.internal:8080/api/orders}' in yml
    assert '@Autowired' in (mp / 'src/main/java/demo/app/OrderService.java').read_text()
    cfg = res[('MOD_HARDCODED_CONFIG', 'APPLIED')]
    assert cfg['rollback']['available'] and (out / 'reports' / cfg['patch']).exists()
    assert any('http://erp.internal' in c['before'] for c in cfg['changes'])
    names = {p.name for p in (out / 'reports').iterdir()}
    assert {'modernization-report.md', 'quality-before.json', 'quality-after.json', 'refactors-applied.json', 'manual-recommendations.md'} <= names
    qb, qa = (json.loads((out / 'reports' / f).read_text()) for f in ('quality-before.json', 'quality-after.json'))
    assert qb['findingsByRule']['MOD_HARDCODED_CONFIG'] == 1 and 'MOD_HARDCODED_CONFIG' not in qa['findingsByRule']
    rec = (out / 'reports' / 'manual-recommendations.md').read_text()
    assert 'public record OrderDto(String id, long amount)' in rec and 'public OrderService(OrderRepository repository, PriceClient prices)' in rec


def test_approved_contract_change_is_gated_and_rolled_back(project, tmp_path, monkeypatch):
    fake = install(monkeypatch, FakeMaven())
    orig = fake.mvn
    monkeypatch.setattr('mf.worker.mavenops.mvn', lambda ctx, cwd, goals, name, timeout=None:
                        fake._result(ctx, name, 1, '[ERROR] /x/OrderServiceTest.java:[3,5] constructor OrderService in class OrderService cannot be applied')
                        if name == 'modernize-gate-MOD_FIELD_INJECTION' else orig(ctx, cwd, goals, name, timeout))
    out = tmp_path / 'rb'
    assert cli_tool.main(['modernize', str(project), '--output', str(out), '--approve', 'MOD_FIELD_INJECTION']) == 0
    assert ('MOD_FIELD_INJECTION', 'ROLLED_BACK') in _results(out)
    assert '@Autowired' in (out / 'modernized-project/src/main/java/demo/app/OrderService.java').read_text()  # restored snapshot
    monkeypatch.setattr('mf.worker.mavenops.mvn', orig)
    out2 = tmp_path / 'ok'
    assert cli_tool.main(['modernize', str(project), '--output', str(out2), '--approve', 'MOD_FIELD_INJECTION']) == 0
    assert ('MOD_FIELD_INJECTION', 'APPLIED') in _results(out2)
    svc = (out2 / 'modernized-project/src/main/java/demo/app/OrderService.java').read_text()
    assert 'private final OrderRepository repository;' in svc and 'public OrderService(OrderRepository repository, PriceClient prices) {' in svc


def test_field_injection_with_no_arg_callers_is_blocked_not_attempted(project, tmp_path, monkeypatch):
    """Regression (real run on the demo): MigratedRoutesConfig called `new LegacyRoute()`; the refactor broke compilation."""
    fake = install(monkeypatch, FakeMaven())
    cfg = project / 'src/main/java/demo/app/Config.java'
    cfg.write_text('package demo.app;\nclass Config { Object svc() { return new demo.app.OrderService(); } }\n')
    out = tmp_path / 'blk'
    assert cli_tool.main(['modernize', str(project), '--output', str(out), '--approve', 'MOD_FIELD_INJECTION']) == 0
    blocked = _results(out)[('MOD_FIELD_INJECTION', 'BLOCKED')]
    assert 'src/main/java/demo/app/Config.java:2' in blocked['items'][0]['detail']
    assert not any(n == 'modernize-gate-MOD_FIELD_INJECTION' for n, _ in fake.calls)  # never attempted


def test_red_baseline_blocks_every_refactor(project, tmp_path, monkeypatch):
    fake = install(monkeypatch, FakeMaven())
    monkeypatch.setattr('mf.worker.mavenops.mvn', lambda ctx, cwd, goals, name, timeout=None: fake._result(ctx, name, 1, '[ERROR] There are test failures.'))
    out = tmp_path / 'red'
    assert cli_tool.main(['modernize', str(project), '--output', str(out)]) == 2
    res = _results(out)
    assert ('MOD_SAFE_CLEANUP', 'BLOCKED') in res and ('MOD_HARDCODED_CONFIG', 'BLOCKED') in res
    assert 'http://erp.internal:8080' in (out / 'modernized-project/src/main/java/demo/app/OrdersRoute.java').read_text()
