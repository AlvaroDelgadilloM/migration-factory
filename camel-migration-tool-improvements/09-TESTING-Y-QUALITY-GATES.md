# Testing y quality gates

## Tipos de pruebas

### Unitarias
- parsers
- reglas
- diff
- scoring

### Golden tests
Proyecto input + output esperado.

### Integration tests
Ejecutar migraciones completas en proyectos fixture.

### Regression tests
Cada bug encontrado debe convertirse en fixture.

## Fixtures mínimos

- Camel 2 + JBoss
- Camel 2 + Spring XML
- Camel 3 + Spring Boot
- proyecto Maven roto
- secrets hardcodeados
- JMS/JNDI
- SOAP CXF

## Gate final

Candidate válido si:
- `mvn validate` PASS
- `mvn compile` PASS
- tests requeridos PASS
- no critical secrets
