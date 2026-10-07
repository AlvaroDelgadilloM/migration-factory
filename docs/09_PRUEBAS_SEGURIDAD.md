# Pruebas, gates y seguridad
## Pirámide
- Unitarias del scanner: POM inválido, namespaces, exclusiones, reglas, escapes HTML y target.
- Golden fixtures por regla: antes/después, no-op, falso positivo y caso límite.
- Contratos: códigos HTTP, headers, payload, SOAP/XML y schema.
- Integración: broker y BD representativos; timeouts, reconexión y fallas.
- Equivalencia: mismo dataset y entradas contra legacy y candidato; comparar salidas observables.

## Casos críticos JMS/JTA
Mensaje correcto; duplicado; poison message; excepción después de escritura DB; caída antes de ack; reconexión; DLQ; orden por clave; redelivery máximo; rollback coordinado. Verificar atomicidad real, no solo que el CamelContext inicia.

## Gates
G0 baseline documentado; G1 perfil/dependencias resueltos; G2 compile; G3 unit/contratos; G4 JMS/DB/transacciones; G5 permisos y secretos; G6 diff revisado. Cada proyecto define gates obligatorios. SKIPPED no equivale a PASS. Fallas baseline se diferencian de regresiones pero no se aceptan automáticamente.

## Seguridad
OIDC y roles de proyecto, control de acceso por artefacto, protección SSRF en URLs Git y callback, secretos en vault, redacción logs/diffs, límites de ZIP y rechazo path traversal/symlinks. Nunca exportar .env ni settings.xml. Runner sin socket Docker de host. Egress Maven controlado y dependencias escaneadas.

## Pruebas entregadas
Ver `tests/test_scanner.py`. Cubren casos significativos de inventario y seguridad de salida. Son pruebas del scanner, no evidencia de equivalencia de una migración Camel real.


## Estado
Pruebas del producto en `tests/` (scanner) y `backend/tests/` (API, worker, seguridad, migraciones) más `scripts/pilot.py`. Equivalencia de aplicaciones migradas: `docs/16_EQUIVALENCIA.md`.
