# Plan de implementación
Estimación inicial por validar con repositorios piloto. No es cotización ni plazo comprometido.

| Fase | Trabajo | Entregable / aceptación |
|---|---|---|
| 0 Descubrimiento | Versiones, repos, brokers, JTA, DS, contratos y volúmenes | Perfil de origen y dos pilotos representativos |
| 1 Inventario | Scanner robusto, Maven efectivo, AST Java, XML seguro | Evidencia archivo/línea y límites visibles |
| 2 Plataforma | API, DB, OIDC, cola, artefactos y aislamiento | Jobs recuperables y permisos por proyecto |
| 3 Planificación | Catálogo versionado, hallazgos, perfiles y UI | Plan reproducible con bloqueos |
| 4 Spring piloto | Recetas Java/Jakarta/Camel y runtime | Candidato compilado con contratos equivalentes |
| 5 Quarkus piloto | BOM/extensions/CDI y configuración | Equivalencia en JVM; native opcional |
| 6 Cartera | Scheduling, cuotas, Git PR, auditoría | Operación simultánea sin contaminar repos |
| 7 Optimización | Métricas ahorro y asistencia IA opcional | Mejora medida sobre línea base |

## Tickets prioritarios
MF-001 ingestión Git por SHA con allowlist; MF-002 resolución efectiva Maven; MF-003 parser de rutas; MF-004 catálogo de reglas con fixtures; MF-005 worker y lease; MF-006 API y membresías; MF-007 inventario Angular; MF-008 hallazgos/revisión; MF-009 dry-run recipes; MF-010 diff y artifacts; MF-011 suite de equivalencia; MF-012 PR borrador; MF-013 perfil Quarkus; MF-014 auditoría/exportación.

Definition of Done: código revisado, pruebas asociadas al riesgo, contratos actualizados, telemetría sin secretos, errores documentados y demo con fixture. Toda receta tiene positivo/negativo y rollback.

## Descubrimiento requerido
Cantidad de repos/módulos/rutas; Java/Camel/JBoss exactos; WAR/EAR; parents/BOMs privados; Spring XML/CDI/EJB; brokers/colas; datasource/JTA/XA; SSO; CI; cobertura existente; repositorios de dependencias y restricciones de salida.

Dependencias: descubrimiento precede perfiles; inventario precede plan; aislamiento precede builds; equivalencia precede PR. Evitar estimar esfuerzo solo por número de rutas: una ruta con XA puede superar muchas rutas simples.
