# Alcance y reglas funcionales
## Objetivo
Reducir tareas repetitivas de evaluación y migración conservando el comportamiento de integración: contratos, transacciones, orden, duplicados, reintentos y seguridad.

## Actores
| Rol | Facultades |
|---|---|
| Administrador | Usuarios, permisos, perfiles objetivo y catálogo de reglas |
| Arquitecto | Crear plan, decidir runtime y aceptar excepciones justificadas |
| Desarrollador | Escanear, proponer cambios y ejecutar validación |
| Revisor | Revisar diff y evidencia; aprobar propuesta para PR |
| Auditor | Consultar reportes y trazabilidad; sin escritura |

## Flujo
Registrar repositorio → fijar commit → analizar → revisar inventario → seleccionar perfil compatible → planificar → generar candidato en checkout aislado → revisar diff → compilar y probar → revisión → PR borrador.

## Reglas
- Un run pertenece a un commit y una versión del catálogo de reglas.
- Análisis y preview no modifican el origen.
- Una regla nunca puede declarar éxito a partir de solo una coincidencia textual.
- Una dependencia ausente del código no significa ausencia en el runtime.
- Configuración efectiva Maven debe considerar parents, módulos, perfiles y BOM.
- No publicar ni desplegar a producción desde esta herramienta en el MVP.
- Hallazgos: abierto, propuesto, resuelto, descartado. Descartar exige motivo y evidencia.
- PR aprobado exige validaciones obligatorias y revisión independiente configurable.
- XML DSL no debe convertirse obligatoriamente a Java DSL: conservarlo cuando el runtime lo soporte y resulte verificable.

## MVP / evolución
MVP: inventario, hallazgos, plan, reportes y ejecución controlada de recetas elegidas para un piloto Spring Boot. Segunda fase: Quarkus, procesamiento de cartera, integración Git y cobertura dinámica. IA y compilación nativa son opcionales posteriores.

## Criterios de éxito
Inventario trazable, errores visibles, reproducibilidad por commit/perfil/catálogo y equivalencia verificada en rutas piloto. Medir horas reales de intervención; no prometer porcentaje de ahorro antes del piloto.
