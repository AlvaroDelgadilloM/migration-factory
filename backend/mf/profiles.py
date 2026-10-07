"""Seed target profiles. Every coordinate below was checked against Maven Central on 2026-10-05:
- camel-spring-boot 4.14.9 (LTS line) declares spring-boot-version 3.5.16 in its parent POM.
- quarkus-camel-bom 3.40.1 manages camel-quarkus 3.40.0 and camel 4.22.1.
- camel-upgrade-recipes 4.14.0 is built with rewrite-recipe-bom 3.9.0 (rewrite-migrate-java 3.11.0) and
  documents rewrite-maven-plugin 6.15.0; 4.22.0 uses rewrite-recipe-bom 3.37.0 (3.42.0) and plugin 6.46.1.
- Recipe names were read from META-INF/rewrite/*.yml inside those jars.
Profiles are immutable once used: a change is a new version (seed refuses an edited profile with the same version
and retires older validated versions of the same key).
v2 (2026-10-05): mapping keyed by Camel 4 artifact names, core/used components, servlet, CXF soap/rest.
"""
import hashlib
import json

from sqlalchemy import select

# Camel 2 artifact -> artifact suffix in the target ecosystem. Anything else is reported as unmapped.
# Keys use Camel 4 artifact names: the camel-3-to-4 step first applies RENAMED_IN_CAMEL4 / REMOVED_IN_CAMEL4.
_COMPONENTS = {
    'camel-core': None, 'camel-jms': 'jms', 'camel-jdbc': 'jdbc', 'camel-sql': 'sql', 'camel-jackson': 'jackson',
    'camel-jaxb': 'jaxb', 'camel-mail': 'mail', 'camel-ftp': 'ftp', 'camel-kafka': 'kafka', 'camel-http': 'http',
    'camel-quartz': 'quartz', 'camel-cxf-soap': 'cxf-soap', 'camel-servlet': 'servlet', 'camel-bean': 'bean',
    # doc 33: there is no cxf-spring-soap starter/extension; camel-cxf-soap-starter and camel-quarkus-cxf-soap cover Spring XML
    # CXF endpoints (both verified in the BOM snapshots). camel-cxf-spring-rest has no verified equivalent: it stays unmapped.
    'camel-cxf-spring-soap': 'cxf-soap',
    'camel-direct': 'direct', 'camel-log': 'log', 'camel-seda': 'seda', 'camel-timer': 'timer', 'camel-file': 'file',
}
# Verified absent from org.apache.camel:camel-bom:4.14.0; their successors are present.
from . import rules as _rules  # declarative: rules/migration-rules.json
RENAMED_IN_CAMEL4 = _rules.renamed()
REMOVED_IN_CAMEL4 = _rules.removed()
# APIs the application server provided ('provided' scope) -> Jakarta EE 10 APIs (verified on Maven Central).
SERVER_APIS = {
    ('javax', 'javaee-api'): ('jakarta.platform', 'jakarta.jakartaee-api', '10.0.0'),
    ('javax', 'javaee-web-api'): ('jakarta.platform', 'jakarta.jakartaee-api', '10.0.0'),
    ('org.jboss.spec.javax.jms', 'jboss-jms-api_2.0_spec'): ('jakarta.jms', 'jakarta.jms-api', '3.1.0'),
}

# Components that lived inside camel-core in Camel 2 and are separate artifacts in Camel 3+ (all verified to exist).
CORE_COMPONENTS = ('bean', 'direct', 'log', 'seda', 'timer', 'file')

# Endpoint scheme observed in routes -> verified artifact suffix (JBoss often provided these as server modules,
# so the legacy POM may not declare them).
USED_COMPONENTS = {'quartz2': 'quartz', 'quartz': 'quartz', 'http4': 'http', 'https4': 'http', 'http': 'http', 'https': 'http',
                   'ftp': 'ftp', 'sftp': 'ftp', 'ftps': 'ftp', 'jms': 'jms', 'jdbc': 'jdbc', 'sql': 'sql', 'smtp': 'mail',
                   'smtps': 'mail', 'imap': 'mail', 'pop3': 'mail', 'cxf': 'cxf-soap', 'kafka': 'kafka'}

SPRING_CAMEL = '4.14.9'
QUARKUS_CAMEL_EXT = '3.40.0'

PROFILES = [
    {
        'key': 'spring-boot-3.5-camel-4.14', 'version': 3, 'runtime': 'spring',
        'name': 'Spring Boot 3.5.16 + Camel 4.14.9 (LTS)',
        'java_version': '17', 'camel_version': SPRING_CAMEL, 'runtime_version': '3.5.16',
        'bom_coordinates': ['org.apache.camel.springboot:camel-spring-boot-bom:4.14.9',
                            'org.springframework.boot:spring-boot-dependencies:3.5.16'],
        'tooling': {
            'pluginVersion': '6.15.0',
            'artifacts': ['org.apache.camel.upgrade:camel-upgrade-recipes:4.14.0',
                          'org.openrewrite.recipe:rewrite-migrate-java:3.11.0'],
            'camelRecipe': 'org.apache.camel.upgrade.CamelMigrationRecipe',
            'camelRecipeTarget': '4.14.0',
        },
        'dependency_mapping': {
            'groupId': 'org.apache.camel.springboot', 'version': SPRING_CAMEL,
            'artifacts': {f'org.apache.camel:{k}': ('camel-spring-boot-starter' if v is None else f'camel-{v}-starter')
                          for k, v in _COMPONENTS.items()},
            'xmlDsl': 'camel-xml-io-dsl-starter',
            'coreComponent': 'camel-{}-starter',
            'routesProperty': 'camel.springboot.routes-include-pattern',
        },
        'sources': ['https://repo1.maven.org/maven2/org/apache/camel/springboot/spring-boot/4.14.9/',
                    'https://camel.apache.org/manual/camel-upgrade-recipes-tool.html'],
        'status': 'validated',
    },
    {
        'key': 'quarkus-3.40-camel-quarkus', 'version': 3, 'runtime': 'quarkus',
        'name': 'Quarkus 3.40.1 + Camel Quarkus 3.40.0 (Camel 4.22.1)',
        'java_version': '17', 'camel_version': '4.22.1', 'runtime_version': '3.40.1',
        'bom_coordinates': ['io.quarkus.platform:quarkus-bom:3.40.1', 'io.quarkus.platform:quarkus-camel-bom:3.40.1'],
        'tooling': {
            'pluginVersion': '6.46.1',
            'artifacts': ['org.apache.camel.upgrade:camel-upgrade-recipes:4.22.0',
                          'org.openrewrite.recipe:rewrite-migrate-java:3.42.0'],
            'camelRecipe': 'org.apache.camel.upgrade.CamelMigrationRecipe',
            'camelRecipeTarget': '4.22.0',
        },
        'dependency_mapping': {
            'groupId': 'org.apache.camel.quarkus', 'version': QUARKUS_CAMEL_EXT,
            'artifacts': {f'org.apache.camel:{k}': ('camel-quarkus-core' if v is None else f'camel-quarkus-{v}')
                          for k, v in _COMPONENTS.items()},
            'xmlDsl': 'camel-quarkus-xml-io-dsl',
            'coreComponent': 'camel-quarkus-{}',
            'routesProperty': 'camel.main.routes-include-pattern',
        },
        'sources': ['https://repo1.maven.org/maven2/io/quarkus/platform/quarkus-camel-bom/3.40.1/',
                    'https://camel.apache.org/camel-quarkus/3.40.x/migration-guide/camel-spring-boot-to-camel-quarkus.html'],
        'status': 'validated',
    },
]


def profile_digest(p: dict) -> str:
    keys = ('key', 'version', 'runtime', 'java_version', 'camel_version', 'runtime_version', 'bom_coordinates', 'tooling', 'dependency_mapping')
    return hashlib.sha256(json.dumps({k: p[k] for k in keys}, sort_keys=True).encode()).hexdigest()


def seed(db):
    from .models import RuleCatalog, TargetProfile
    from .analysis.scanner import load_catalog
    from .config import get_settings
    for p in PROFILES:
        exists = db.scalar(select(TargetProfile).where(TargetProfile.key == p['key'], TargetProfile.version == p['version']))
        if exists is None:
            for old in db.scalars(select(TargetProfile).where(TargetProfile.key == p['key'], TargetProfile.version < p['version'],
                                                              TargetProfile.status == 'validated')):
                old.status = 'retired'  # kept for history: plans/scans that used it still reference it
            db.add(TargetProfile(**p, digest=profile_digest(p)))
        elif exists.digest != profile_digest(p):
            raise RuntimeError(f"El perfil {p['key']} v{p['version']} cambió sin subir su versión: los perfiles son inmutables")
    cat = load_catalog(get_settings().rules_catalog)
    existing = db.scalar(select(RuleCatalog).where(RuleCatalog.version == cat['catalogVersion']))
    if existing is None:
        for old in db.scalars(select(RuleCatalog).where(RuleCatalog.status == 'active')):
            old.status = 'retired'  # history kept; scans keep pointing to the catalog they used
        rules = [{k: v for k, v in r.items() if not k.startswith('_')} for r in cat['rules']]
        db.add(RuleCatalog(version=cat['catalogVersion'], digest=cat['digest'], source_uri='rules/catalog.json',
                           status='active', rules=rules))
    elif existing.digest != cat['digest']:
        raise RuntimeError(f"rules/catalog.json cambió sin cambiar catalogVersion ({cat['catalogVersion']}): suba la versión")
    db.commit()
