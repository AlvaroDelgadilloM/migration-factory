# API y UX

## API sugerida

### Crear análisis
`POST /api/jobs`

### Estado
`GET /api/jobs/{id}`

### Hallazgos
`GET /api/jobs/{id}/findings`

### Plan
`GET /api/jobs/{id}/plan`

### Aprobar cambios
`POST /api/jobs/{id}/approvals`

### Descargar candidate
`GET /api/jobs/{id}/artifacts/candidate`

## UX

Pantallas:
1. Nuevo análisis
2. Resumen técnico
3. Inventario de integraciones
4. Riesgos
5. Plan de migración
6. Diff por recipe
7. Build/Test
8. Acciones manuales
9. Reporte final

## Semáforo

- verde: migrable automáticamente
- amarillo: requiere revisión
- rojo: bloqueante
