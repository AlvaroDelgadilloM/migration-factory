# Motor de análisis
## Pipeline objetivo
Descubrir módulos → parsear Maven → obtener effective-pom en sandbox → indexar Java/XML/config → identificar rutas/endpoints → detectar acoplamientos → evaluar catálogo → emitir evidencias.

## Scanner entregado
Lee POM XML local, dependencias explícitas y rutas aproximadas Java/XML; busca importaciones y referencias conocidas; reporta archivo y línea sin exportar el contenido de la línea. Ignora carpetas de build, Git y enlaces simbólicos. HTML escapa valores. No ejecuta Maven ni código del proyecto.

## Límites explícitos
Java DSL se detecta por expresión regular: puede incluir comentarios y no resolver concatenaciones, endpoints dinámicos o RouteTemplates. El número es una aproximación estática. POM local no resuelve herencia, perfiles, BOM ni propiedades externas. XML inválido se reporta; no se omite silenciosamente. Los hallazgos son indicios revisables.

## Próxima implementación
Usar parser Java/AST con resolución de tipos para transformaciones. XML con parser seguro sin entidades externas. Conservar namespaces, encoding y comentarios. Descubrir placeholders y crear grafo de referencias. Asociar rutas con routeId cuando exista y distinguir origen dinámico de endpoint literal.

## Complejidad
El MVP no da porcentaje de readiness. Reporta conteos por severidad y componentes observados. Una futura puntuación solo ordenará la cartera: pesos configurados, explicación por factor y calibración con horas reales. No se interpreta como probabilidad de éxito.

## Contrato hallazgo
id estable, regla/version, módulo, archivo, línea, tipo, severidad, clasificación, evidencia redactada, recomendación, estado y revisión. No contar dos detectores del mismo evento como dos problemas sin deduplicación.


## Estado (2026-10-05)
La sección «Próxima implementación» está parcialmente cubierta: XML seguro con líneas, multi-módulo, parent/BOM locales, POM efectivo con Maven en sandbox, rutas dinámicas marcadas como desconocidas y deduplicación. Pendiente: AST Java con resolución de tipos y grafo de placeholders. Ver `docs/15_IMPLEMENTACION.md`.
