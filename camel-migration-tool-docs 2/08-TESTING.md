# Estrategia de Pruebas

## Objetivo
Una aplicación que compila no necesariamente está correctamente migrada.

## Tests mínimos generados

### Context Load
Validar que Spring Boot y Camel inician.

### Route Presence
Comprobar que todas las rutas esperadas existen.

### AdviceWith / Mock Endpoints
Sustituir endpoints externos por mocks para probar flujo Camel.

### REST Contract
Comparar paths y estructuras públicas cuando sea posible.

### JMS
Verificar producción/consumo mediante broker embebido o test containers según alcance.

### SQL
Validar queries en base temporal cuando sea viable.

## Comparación Legacy vs Migrado
Generar matriz:

| Capability | Legacy | Migrated | Status |
|---|---|---|---|
| Order REST | yes | yes | OK |
| Payment API | yes | yes | OK |
| JMS input | yes | yes | REVIEW |
| SOAP inventory | yes | yes | OK |
