# Corrección: compatibilidad Camel 3→4 + salida local descargable / PR remoto

## Objetivo

Corregir dos comportamientos del migrador:

1. Evitar que la receta Camel 3 → Camel 4 genere dependencias inexistentes o inválidas, por ejemplo:
   - `camel-xmljson:4.14.0`
   - `camel-swagger-java:4.14.0`
   - `camel-swagger:4.14.0`
   - otros componentes removidos, renombrados o sustituidos.

2. Ajustar la experiencia final según el origen del proyecto:
   - si el proyecto es LOCAL, permitir descargar el proyecto corregido/migrado en ZIP;
   - si el proyecto viene de GIT remoto, permitir crear PR y también descargar el ZIP;
   - no mostrar “Integrar en PR” como acción principal para proyectos locales.

---

# PARTE A — CORRECCIÓN DEL MOTOR CAMEL 3 → CAMEL 4

## 1. Problema actual

El migrador ejecuta la receta `camel-3-to-4` y después el candidate falla al compilar.

Ejemplo:

```text
Could not find artifact org.apache.camel:camel-xmljson:jar:4.14.0
Could not find artifact org.apache.camel:camel-swagger-java:jar:4.14.0
Could not find artifact org.apache.camel:camel-swagger:jar:4.14.0
```

El problema raíz es que el motor está aplicando una transformación del tipo:

```text
artifact Camel 3
     ↓
mismo artifact
     ↓
version = 4.14.0
```

Esto no es válido para todos los componentes Camel.

Algunos artefactos:

```text
- siguen existiendo
- cambian de nombre
- son reemplazados
- fueron removidos
- requieren migración arquitectónica
- requieren revisión manual
```

Por tanto, no debe existir una estrategia genérica de “subir versión”.

---

## 2. Nueva clasificación de componentes Camel

Crear un registro central:

```text
CamelComponentCompatibilityRegistry
```

Estados soportados:

```text
SUPPORTED
RENAMED
REPLACED
REMOVED
ARCHITECTURAL_MIGRATION
MANUAL_REVIEW
```

Ejemplo conceptual:

```yaml
camel-swagger-java:
  status: REPLACED
  target: camel-openapi-java
  confidence: HIGH

camel-rest-swagger:
  status: REPLACED
  target: camel-openapi-rest
  confidence: HIGH

camel-cdi:
  status: ARCHITECTURAL_MIGRATION
  alternatives:
    - camel-spring-boot
    - camel-quarkus

camel-blueprint:
  status: ARCHITECTURAL_MIGRATION
  alternatives:
    - spring-boot
    - quarkus

camel-xmljson:
  status: MANUAL_REVIEW
  action: inspect_usage
```

La tabla debe vivir fuera del código duro, preferentemente en YAML/JSON versionado.

Ejemplo:

```text
rules/
└── camel/
    └── camel-3-to-4-components.yaml
```

---

## 3. Nueva secuencia de migración de dependencias

Incorrecto:

```text
detectar dependencia
   ↓
cambiar versión a 4.14.0
   ↓
escribir POM
   ↓
mvn compile
   ↓
fallo
```

Correcto:

```text
detectar dependencia
   ↓
consultar Compatibility Registry
   ↓
clasificar
   ↓
determinar replacement/removal/manual
   ↓
validar artefacto objetivo
   ↓
validar compatibilidad de uso
   ↓
escribir POM
   ↓
mvn validate
   ↓
mvn compile
```

---

## 4. Artifact Availability Validation

Antes de escribir cualquier dependencia objetivo:

```text
PROPOSED ARTIFACT
      ↓
CATALOG CHECK
      ↓
MAVEN AVAILABILITY CHECK
      ↓
COMPATIBILITY CHECK
      ↓
WRITE POM
```

Crear componente:

```text
dependency/
└── artifact_validator.py
```

Responsabilidades:

- comprobar que groupId/artifactId/version objetivo sea válido;
- consultar repositorios aprobados;
- distinguir:
  - artefacto inexistente;
  - repo no disponible;
  - credenciales;
  - política corporativa;
  - artefacto removido;
- impedir que una recipe escriba dependencias no validadas.

---

## 5. Tipo de error correcto

No reportar únicamente:

```text
MANDATORY_RECIPE_NOT_APPLIED
```

cuando la causa real fue una dependencia inválida.

Agregar:

```text
INVALID_CAMEL_COMPONENT_MAPPING
UNSUPPORTED_TARGET_ARTIFACT
REMOVED_CAMEL_COMPONENT
ARTIFACT_REPLACEMENT_REQUIRED
ARCHITECTURAL_MIGRATION_REQUIRED
```

Ejemplo:

```text
Migration status: FAILED

Primary reason:
INVALID_CAMEL_COMPONENT_MAPPING

Artifact:
org.apache.camel:camel-swagger-java

Source:
Camel 3

Target:
Camel 4.14.0

Action:
REPLACE_WITH camel-openapi-java
```

---

## 6. Validar uso antes de reemplazar

No reemplazar artefactos únicamente por nombre.

Analizar cómo se usan.

Ejemplo Swagger/OpenAPI:

Buscar:

```text
RestConfiguration
apiContextPath
swagger
swagger.json
swagger.yaml
SwaggerFeature
Swagger2Feature
restConfiguration()
```

Y decidir si corresponde:

```text
camel-openapi-java
```

o:

```text
camel-openapi-rest
```

o:

```text
manual review
```

---

## 7. Reglas para Blueprint y OSGi

Si se detecta:

```xml
<packaging>bundle</packaging>
```

o:

```text
OSGI-INF/blueprint/
maven-bundle-plugin
Bundle-SymbolicName
Import-Package
Export-Package
```

clasificar:

```text
OSGI_PROJECT = true
```

No hacer:

```text
camel-blueprint:3.x
      ↓
camel-blueprint:4.x
```

Si el target es Spring Boot:

```text
camel-blueprint
      ↓
ARCHITECTURAL_MIGRATION
      ↓
convert Blueprint beans
      ↓
convert CamelContext
      ↓
convert OSGi service references
      ↓
remove camel-blueprint
      ↓
bundle → jar
```

---

## 8. Quality gate de dependencias

Antes de candidate-build:

```text
DEPENDENCY_VALIDATION_GATE
```

Debe comprobar:

```text
- todos los artifacts target existen;
- no hay componentes Camel removidos sin acción;
- no hay placeholders de dependencias;
- no hay artefactos legacy con versión Camel 4 forzada;
- no hay mappings sin confidence;
```

Si falla:

```text
candidate-build = SKIPPED
```

y el motivo debe ser explícito.

Ejemplo:

```text
DEPENDENCY_VALIDATION_FAILED
```

No esperar a que Maven falle varios minutos después.

---

## 9. Reporte de compatibilidad

Generar:

```text
camel-component-migration-report.md
```

Ejemplo:

```text
Source dependency:
camel-swagger-java

Target status:
REPLACED

Target:
camel-openapi-java

Confidence:
HIGH

Code usage found:
Rest DSL + apiContextPath

Auto migration:
YES
```

Otro:

```text
Source dependency:
camel-xmljson

Target status:
MANUAL_REVIEW

Reason:
Usage requires semantic inspection

Auto migration:
NO
```

---

# PARTE B — SALIDA LOCAL DESCARGABLE Y PR SOLO CUANDO APLICA

## 10. Problema actual de UX

Al terminar una corrección o migración aparece una acción del tipo:

```text
Integrar en un PR
```

aunque el proyecto haya sido cargado localmente.

Esto no es correcto.

Un proyecto local no tiene necesariamente:

```text
remote
branch
provider
repository
credentials
```

Por tanto, la acción natural debe ser:

```text
Descargar proyecto corregido
```

---

## 11. Nuevo modelo SourceType

Agregar:

```text
LOCAL
GIT_REMOTE
CONNECTED_REPOSITORY
```

Ejemplo:

```python
class SourceType(Enum):
    LOCAL = "LOCAL"
    GIT_REMOTE = "GIT_REMOTE"
    CONNECTED_REPOSITORY = "CONNECTED_REPOSITORY"
```

El contexto debe guardar:

```python
source.type
source.repository_url
source.branch
source.provider
source.commit_sha
```

Los campos remotos son opcionales para `LOCAL`.

---

## 12. Acciones finales según origen

### LOCAL

Mostrar:

```text
[ Descargar proyecto corregido .zip ]
[ Descargar reporte ]
[ Descargar diff ]
[ Descargar acciones manuales ]
```

No mostrar:

```text
Crear PR
```

### GIT_REMOTE

Mostrar:

```text
[ Crear Pull Request ]
[ Descargar proyecto .zip ]
[ Descargar diff ]
[ Descargar reporte ]
```

### CONNECTED_REPOSITORY

Mostrar:

```text
[ Crear Pull Request ]
[ Descargar proyecto .zip ]
[ Ver diff ]
[ Ver reporte ]
```

según permisos del conector.

---

## 13. Regla de UI

Pseudocódigo:

```python
if source.type == SourceType.LOCAL:
    show_download_candidate = True
    show_create_pr = False

elif source.type in (
    SourceType.GIT_REMOTE,
    SourceType.CONNECTED_REPOSITORY
):
    show_download_candidate = True
    show_create_pr = True
```

Nunca inferir PR solo porque el proyecto contiene `.git`.

Debe validarse:

```text
remote configurado
provider soportado
credenciales disponibles
permiso de escritura
```

---

## 14. Artefacto principal para LOCAL

Al finalizar:

```text
output/
├── migrated-project.zip
├── migration-report.md
├── manual-actions.md
├── diff.patch
├── analysis.json
├── build-report.json
└── test-report.json
```

La descarga principal debe apuntar a:

```text
migrated-project.zip
```

---

## 15. Contenido del ZIP

El ZIP debe contener exactamente el candidate final validado.

Flujo:

```text
SOURCE
  ↓
BASELINE REPAIR
  ↓
MIGRATION
  ↓
MODERNIZATION
  ↓
AUTOFIX
  ↓
BUILD
  ↓
TEST
  ↓
FINAL CANDIDATE
  ↓
ZIP
```

No comprimir:

```text
source original
```

ni:

```text
candidate anterior a autofix
```

---

## 16. Estado y descarga

### SUCCEEDED

```text
Download label:
Descargar proyecto migrado
```

### PARTIAL_SUCCESS

Permitir descarga:

```text
Descargar proyecto migrado con pendientes
```

Mostrar advertencia:

```text
Existen acciones manuales pendientes.
```

### FAILED

No llamarlo:

```text
Proyecto corregido
```

Usar:

```text
Descargar candidate para diagnóstico
```

y ofrecer:

```text
logs
report
diff
manual-actions
```

### BLOCKED

Ofrecer:

```text
Descargar análisis
Descargar acciones requeridas
```

---

## 17. Multiproyecto

Si el origen es un workspace local multiproyecto, descargar el workspace completo.

Ejemplo:

```text
migrated-workspace.zip
└── develop/
    └── commons/
        ├── common-activemq/
        ├── common-catalogue/
        ├── common-datasource/
        ├── common-epay/
        └── common-rate-exchange/
```

No descargar únicamente:

```text
common-activemq
```

si el workspace original tenía 5 proyectos.

---

## 18. Preservar estructura

El ZIP final debe preservar:

```text
relative paths
module relationships
resource folders
scripts
config files
documentation
```

y excluir:

```text
target/
.git/
IDE caches
temporary work dirs
internal migration snapshots
```

salvo que exista una opción explícita para incluirlos.

---

## 19. Manifest del resultado

Agregar dentro del ZIP:

```text
.migration/
├── manifest.json
├── migration-report.md
├── manual-actions.md
└── diff.patch
```

Ejemplo de `manifest.json`:

```json
{
  "status": "SUCCEEDED",
  "sourceType": "LOCAL",
  "target": {
    "java": "21",
    "camel": "4.14.0",
    "springBoot": "3.x"
  },
  "projects": 5,
  "buildPassed": true,
  "testsPassed": true,
  "manualActions": 0
}
```

---

## 20. Endpoint de descarga

Agregar:

```text
GET /api/migrations/{migrationId}/download
```

Respuesta:

```text
Content-Type: application/zip
Content-Disposition: attachment; filename="migrated-project.zip"
```

Para multiproyecto:

```text
migrated-workspace.zip
```

---

## 21. Endpoint de artefactos

Opcional:

```text
GET /api/migrations/{migrationId}/artifacts
```

Respuesta:

```json
{
  "candidateZip": true,
  "report": true,
  "diff": true,
  "manualActions": true,
  "pullRequestAvailable": false
}
```

---

## 22. PR creation gate

Antes de mostrar Crear PR:

```text
PR_CAPABILITY_GATE
```

Debe comprobar:

```text
source.type != LOCAL
repository provider supported
remote exists
credentials/reference available
write permission available
target branch resolved
candidate status allowed
```

Si no cumple:

```text
pullRequestAvailable = false
```

---

## 23. No mezclar integración con generación

El migrador debe separar:

```text
GENERATE RESULT
```

de:

```text
INTEGRATE RESULT
```

Arquitectura:

```text
Migration Engine
      ↓
Result Artifact
      ↓
Integration Adapter
      ├── ZIP Download
      ├── GitHub PR
      ├── GitLab MR
      └── Bitbucket PR
```

Esto evita acoplar el migrador a GitHub.

---

## 24. Nuevo módulo recomendado

```text
output/
├── artifact_builder.py
├── zip_packager.py
├── manifest_generator.py
└── result_service.py

integration/
├── integration_adapter.py
├── github_adapter.py
├── gitlab_adapter.py
└── local_download_adapter.py
```

---

## 25. Flujo final completo

```text
SOURCE
   ↓
DISCOVERY
   ↓
ANALYSIS
   ↓
BASELINE REPAIR
   ↓
MIGRATION PLAN
   ↓
CAMEL COMPATIBILITY REGISTRY
   ↓
ARTIFACT VALIDATION
   ↓
MIGRATION
   ↓
MODERNIZATION
   ↓
AUTOFIX
   ↓
DEPENDENCY VALIDATION GATE
   ↓
BUILD
   ↓
TEST
   ↓
FINAL CANDIDATE
   ↓
PACKAGE RESULT
   ↓
┌───────────────────────┬────────────────────────┐
│ LOCAL                 │ REMOTE GIT             │
│                       │                        │
│ Download ZIP          │ Create PR              │
│ Download Report       │ Download ZIP           │
│ Download Diff         │ Download Report        │
└───────────────────────┴────────────────────────┘
```

---

# 26. Criterios de aceptación — Camel

## Caso 1

Source:

```text
camel-swagger-java
```

Target Camel 4.

Resultado:

```text
No generar camel-swagger-java:4.x
```

Debe aplicar:

```text
REPLACED
```

o:

```text
MANUAL_REVIEW
```

PASS.

## Caso 2

Source:

```text
camel-blueprint
```

Target:

```text
Spring Boot + Camel 4
```

Resultado:

```text
ARCHITECTURAL_MIGRATION
```

No version bump.

PASS.

## Caso 3

Propuesta de artifact inexistente.

Resultado:

```text
DEPENDENCY_VALIDATION_FAILED
```

antes de `candidate-build`.

PASS.

---

# 27. Criterios de aceptación — Local download

## Caso 1

Proyecto subido localmente.

Resultado UI:

```text
Descargar proyecto corregido
```

No:

```text
Crear PR
```

PASS.

## Caso 2

Repositorio remoto.

Resultado:

```text
Crear PR
Descargar proyecto
```

PASS.

## Caso 3

Migración local `SUCCEEDED`.

Debe existir:

```text
migrated-project.zip
```

PASS.

## Caso 4

Workspace local con 5 proyectos.

ZIP contiene los 5 proyectos.

PASS.

## Caso 5

Migration status `FAILED`.

UI muestra:

```text
Descargar candidate para diagnóstico
```

No:

```text
Descargar proyecto corregido
```

PASS.

---

# 28. Prioridad

## Camel Compatibility Registry

```text
CRITICAL
```

Razón: actualmente una recipe puede introducir artefactos que no existen y romper el candidate.

## Local Download / PR behavior

```text
HIGH
```

Razón: el flujo actual no representa correctamente la naturaleza del origen y obliga visualmente a una acción de integración Git que no aplica a proyectos locales.

---

# 29. Resultado esperado

Para un proyecto local migrado correctamente:

```text
Migration status:
SUCCEEDED

Source:
LOCAL

Actions:

[ Descargar proyecto migrado ]
[ Descargar reporte ]
[ Descargar diff ]
```

Para un repositorio Git:

```text
Migration status:
SUCCEEDED

Source:
GIT_REMOTE

Actions:

[ Crear Pull Request ]
[ Descargar proyecto migrado ]
[ Descargar reporte ]
[ Descargar diff ]
```

Y para Camel 3 → 4:

```text
No artifact should be written to the target POM
without passing the compatibility and availability gates.
```
