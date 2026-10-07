# Prompt maestro para Claude

Implementa un módulo de modernización sobre el proyecto existente de migración Java/Camel.

## Objetivo

El sistema debe analizar código ya migrado, detectar deuda técnica y aplicar refactors seguros sin alterar el repositorio original.

## Flujo obligatorio

```text
ANALYZE
BASELINE REPAIR
MIGRATE
MODERNIZE
REFACTOR
BUILD
TEST
REPORT
```

## Requisitos

1. Crear paquete `modernization/`.
2. Analizar AST Java, Spring beans y Camel RouteBuilder.
3. Detectar code smells y problemas arquitectónicos.
4. Implementar reglas SAFE, REFACTOR y ARCHITECTURE.
5. No aplicar automáticamente cambios ARCHITECTURE.
6. Cada regla debe incluir confidence score.
7. Crear snapshot antes de cada refactor.
8. Ejecutar build y tests después de cada lote de cambios.
9. Hacer rollback automático si falla un quality gate.
10. Generar reportes before/after.

## Refactors mínimos

- field injection → constructor injection
- hardcoded config → application.yml / ConfigurationProperties
- separar lógica de negocio de Camel routes
- dividir rutas demasiado grandes
- centralizar manejo de excepciones
- detectar integraciones sin timeout/retry/circuit breaker
- detectar DB + JMS sin Outbox
- sugerir records para DTOs compatibles
- externalizar secretos y configuración

## Seguridad

No eliminar código ni modificar contratos públicos sin aprobación.

## Estados

- APPLIED
- ROLLED_BACK
- PROPOSED_ONLY
- BLOCKED

## Salidas

- proyecto modernizado
- modernization-report.md
- quality-before.json
- quality-after.json
- refactors-applied.json
- manual-recommendations.md
