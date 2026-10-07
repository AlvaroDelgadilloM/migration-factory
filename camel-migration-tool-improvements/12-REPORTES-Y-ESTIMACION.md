# Reportes y estimación

## Reportes

- executive-summary.md
- technical-analysis.md
- integration-inventory.md
- migration-plan.md
- migration-report.md
- manual-actions.md
- security-report.md
- build-report.md

## Estimación

Calcular esfuerzo con factores:
- número de módulos
- rutas Camel
- endpoints externos
- SOAP
- JMS
- SQL
- APIs propietarias
- cobertura de pruebas
- dependencias obsoletas

## Score sugerido

```text
complexityScore =
  routes*1 +
  soap*3 +
  jms*2 +
  proprietaryApis*5 +
  modules*2
```

El score no debe presentarse como horas exactas sin calibración histórica.
