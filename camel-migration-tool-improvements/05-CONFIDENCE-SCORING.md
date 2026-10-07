# Confidence Scoring

## Objetivo

Evitar aplicar cambios inseguros de forma automática.

## Escala

- 0.90–1.00: auto-apply
- 0.75–0.89: auto-apply con warning
- 0.50–0.74: requiere aprobación
- <0.50: manual review

## Factores

- regla determinística
- versión exacta conocida
- cobertura de pruebas
- dependencia de runtime
- uso de APIs propietarias
- código dinámico/reflection
- configuración incompleta

## Ejemplos

### Alta confianza
`camel-http4 -> camel-http`

### Media
`javax.inject -> Spring constructor injection`

### Baja
`JBoss TransactionManager -> Spring TransactionManager`

## Reporte

Cada cambio debe incluir:
- confidence
- rationale
- file
- lines
- before
- after
- rollback available
