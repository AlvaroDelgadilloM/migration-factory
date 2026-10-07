# Corrección: Camel Compatibility Registry v2

## Objetivo
Pasar de mappings por nombre a decisiones semánticas.

```yaml
camel-cxf:
  status: ARCHITECTURAL_MAPPING
  resolver: CxfUsageAnalyzer
  targets:
    SOAP:
      plain: camel-cxf-soap
      spring_xml: camel-cxf-spring-soap
    REST:
      plain: camel-cxf-rest
      spring_xml: camel-cxf-spring-rest

camel-blueprint:
  status: ARCHITECTURAL_MIGRATION
  artifactTarget: null
  runtimeMigrationRequired: true

camel-jaxb:
  status: SUPPORTED
  artifactTarget: camel-jaxb
  sourceMigration: CONDITIONAL

camel-servlet:
  status: SUPPORTED
  artifactTarget: camel-servlet
  runtimeConfigMigration: CONDITIONAL
```

Cada resolver devuelve status, target, autoMigration, projectContext, dependencyEvidence y los tres niveles de confidence.
