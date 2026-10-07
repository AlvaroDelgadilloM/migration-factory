# Motor de análisis semántico

## Objetivo

Pasar de búsqueda de texto a comprensión estructural del proyecto.

## Niveles

### Nivel 1 — archivos y configuración
Detectar:
- pom.xml
- application.properties/yml
- web.xml
- beans.xml
- jboss-web.xml
- persistence.xml
- log4j/logback

### Nivel 2 — dependencias
Extraer:
- groupId
- artifactId
- version
- scope
- dependencyManagement
- parents
- BOMs
- plugins Maven

### Nivel 3 — AST Java
Usar parser Java para identificar:
- imports
- anotaciones
- herencia
- métodos
- constructores
- llamadas Camel DSL
- InitialContext/JNDI
- JDBC manual
- HttpClient
- JMS

### Nivel 4 — grafo de integración
Construir un grafo:

```text
Route -> JMS -> Processor -> REST -> SOAP -> SQL -> JMS
```

Cada nodo debe incluir:
- archivo
- línea
- tipo
- endpoint
- configuración
- nivel de riesgo

### Nivel 5 — semántica de migración
Determinar si un hallazgo requiere:
- copy
- replace
- rewrite
- remove
- manual-review

## Salida recomendada

`analysis.json`

```json
{
  "runtime": "jboss",
  "java": "8",
  "camel": "2.24.3",
  "integrations": [],
  "routes": [],
  "risks": [],
  "migrationHints": []
}
```
