# Interfaz Angular
## Estructura
`core/{auth,http,guards}`; `shared/{ui,models}`; `features/{portfolio,scan,findings,plans,execution,diff,validation,rules,audit}`.
Standalone components, rutas lazy, Signals para estado local y RxJS para HTTP/SSE. PrimeNG para tablas, dialogs, steps y messages. Servicio por feature; componentes no hacen fetch directamente. No duplicar DTO y dominio sin justificación. Tipos generados desde OpenAPI.

## Pantallas y aceptación
| Pantalla | Contenido | Acción y condición |
|---|---|---|
| Cartera | Proyectos, SHA, runtime, último scan | Nuevo proyecto con validación URL |
| Inventario | Módulos, versiones observadas, rutas aproximadas | Iniciar scan o ver errores de parseo |
| Hallazgos | Severidad, regla, archivo/línea, clasificación | Revisar; descarte exige motivo |
| Plan | Perfil, pasos, dependencias, bloqueos | Preview solo con perfil validado |
| Ejecución | Etapas, logs redactados, cancelación | Reintentar fase técnica si idempotente |
| Diff | Archivos, antes/después y receta origen | Aprobar/rechazar por archivo |
| Validación | Compile/tests/contratos/JMS y evidencia | PR solo al pasar gates obligatorios |
| Reglas | Versión, fuente, pruebas, estados | Activar nueva versión; no editar histórico |
| Auditoría | Actor, evento, entidad, timestamp | Exportación autorizada |

## Estados UI
Cada vista: cargando, vacía, error con requestId y reintento, datos parciales y permisos insuficientes. Mostrar fuente de cada versión: observada o resuelta. Un scan fallido no borra el último resultado correcto. Logs con autoscroll desactivable. Diff grande cargado por archivo.

## Diseño
Desktop primero, sidebar y área central; azul oscuro/teal, fondos claros, tablas legibles. Nunca comunicar riesgo solo por color. Teclado, foco visible, labels y contraste WCAG AA. Adaptar cartera a móvil; revisión de diff requiere ancho amplio.

`mockups/index.html` contiene nueve vistas navegables con datos ficticios. Es una referencia visual sin backend. Los botones operativos están identificados como demostración.


## Estado
Implementado en `frontend/` con Angular 21 + PrimeNG 21 (PrimeNG 22 requiere licencia con clave). Nueve pantallas más login y configuración de proyecto. Acciones no implementadas aparecen deshabilitadas con el motivo visible.
