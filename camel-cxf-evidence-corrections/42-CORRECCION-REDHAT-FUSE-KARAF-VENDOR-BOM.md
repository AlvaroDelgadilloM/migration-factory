# Corrección: Red Hat Fuse / Karaf Platform Detection + Vendor BOM Recovery

## Objetivo

Agregar una capa explícita de detección de plataforma legacy para proyectos que no son únicamente aplicaciones Camel, sino que dependen de Red Hat Fuse / JBoss Fuse / Karaf y de BOMs de proveedor.

Caso observado:

```text
org.jboss.redhat-fuse:fuse-karaf-bom:
7.10.0.fuse-sb2-7_10_1-00008-redhat-00001
```

El BOM no puede resolverse desde los repositorios configurados y el Maven model queda inválido.

Esto no debe tratarse simplemente como:

```text
BASELINE_MAVEN_MODEL_INVALID
```

Debe identificarse como un problema de plataforma/vendor repository.

---

# 1. Nuevo clasificador de plataforma

Crear:

```text
PlatformDetector
```

Debe inspeccionar:

- parent POM;
- dependencyManagement;
- imported BOMs;
- plugins;
- packaging;
- repositorios;
- namespaces XML;
- dependencias Camel/Karaf/OSGi;
- properties relacionadas con Fuse.

Señales:

```text
org.jboss.redhat-fuse
org.jboss.fuse
fuse-karaf-bom
fuse-springboot-bom
org.apache.karaf
maven-bundle-plugin
packaging=bundle
OSGI-INF/blueprint
```

Resultado:

```text
SOURCE_PLATFORM = RED_HAT_FUSE_KARAF
```

---

# 2. Nuevos reason codes

Agregar:

```text
RED_HAT_FUSE_PLATFORM_DETECTED
VENDOR_BOM_UNRESOLVED
PRIVATE_VENDOR_REPOSITORY_REQUIRED
DEPENDENCY_MANAGEMENT_INCOMPLETE
BASELINE_REPAIR_PARTIAL
VENDOR_BOM_RECOVERY_REQUIRED
```

---

# 3. Estado de baseline

Cuando el BOM de proveedor no pueda resolverse:

```text
BASELINE_BLOCKED_PLATFORM_BOM
```

No usar únicamente:

```text
BASELINE_POM_STILL_INVALID
```

Ejemplo:

```text
Baseline state:
BASELINE_BLOCKED_PLATFORM_BOM

Platform:
RED_HAT_FUSE_KARAF

Missing BOM:
org.jboss.redhat-fuse:fuse-karaf-bom:
7.10.0.fuse-sb2-7_10_1-00008-redhat-00001
```

---

# 4. No reemplazar el BOM de proveedor a ciegas

Incorrecto:

```text
fuse-karaf-bom
→
camel-bom
```

automáticamente.

Un BOM Fuse puede administrar versiones de:

```text
Camel
CXF
Karaf
OSGi
SLF4J
Jackson
Quartz
Mail
logging
otros componentes
```

Reemplazarlo por `camel-bom` puede arreglar Camel pero dejar el modelo Maven inconsistente.

---

# 5. Reparación parcial

Si puede inferirse una versión Camel inequívoca:

```text
Camel 2.23.2
```

y el BOM oficial correspondiente existe, se puede usar:

```text
org.apache.camel:camel-bom:2.23.2
```

para recuperar únicamente las dependencias Camel sin versión.

Pero el resultado debe ser:

```text
BASELINE_REPAIR_PARTIAL
```

no:

```text
BASELINE_REPAIR_SUCCESS
```

si siguen existiendo dependencias no resueltas administradas originalmente por Fuse.

---

# 6. Reporte de reparación parcial

Ejemplo:

```text
Baseline repair:
PARTIAL

Recovered:
10 Camel dependencies

Still unresolved:
- org.slf4j:slf4j-api
- org.slf4j:slf4j-log4j12

Vendor BOM:
UNRESOLVED
```

---

# 7. VendorBomAnalyzer

Crear:

```text
baseline/vendor_bom_analyzer.py
```

Responsabilidades:

```text
1. identificar BOM importado;
2. identificar proveedor;
3. determinar si está disponible;
4. detectar repositorio esperado;
5. inferir qué dependencias probablemente eran administradas por ese BOM;
6. separar recoverable vs unresolved;
7. generar acciones manuales.
```

---

# 8. Clasificación del BOM

Ejemplo:

```json
{
  "groupId": "org.jboss.redhat-fuse",
  "artifactId": "fuse-karaf-bom",
  "version": "7.10.0.fuse-sb2-7_10_1-00008-redhat-00001",
  "type": "VENDOR_PLATFORM_BOM",
  "vendor": "RED_HAT",
  "platform": "FUSE_KARAF",
  "status": "UNRESOLVED"
}
```

---

# 9. Dependency Management Snapshot

Antes de migrar, generar:

```text
dependency-management-snapshot.json
```

Debe contener:

```text
artifact
resolvedVersion
versionSource
sourceBom
confidence
```

Ejemplo:

```json
{
  "artifact": "org.slf4j:slf4j-api",
  "resolvedVersion": null,
  "versionSource": "fuse-karaf-bom",
  "sourceBom": "org.jboss.redhat-fuse:fuse-karaf-bom",
  "confidence": "HIGH"
}
```

Esto permite saber qué quedó sin versión cuando el BOM no está disponible.

---

# 10. No inventar versiones

Si quedan dependencias como:

```text
org.slf4j:slf4j-api
org.slf4j:slf4j-log4j12
```

sin versión porque el BOM Fuse no pudo resolverse:

```text
NO AUTO GUESS
```

a menos que exista evidencia confiable desde:

- parent POM;
- lockfile equivalente;
- effective POM previo;
- repositorio corporativo;
- otro módulo del mismo workspace;
- metadata del proyecto;
- BOM vendor recuperado.

---

# 11. Recuperación desde el workspace

El analizador puede buscar la misma plataforma/versiones en otros proyectos del workspace.

Ejemplo:

```text
workspace/
├ common-a
├ common-b
└ common-c
```

Si otro módulo tiene:

```text
slf4j.version = X
```

y utiliza el mismo Fuse BOM/version:

```text
candidateEvidence = SHARED_PLATFORM_VERSION
```

pero debe registrarse como inferencia, no como certeza absoluta.

---

# 12. Política de repositorios

Clasificar:

```text
MAVEN_CENTRAL
CORPORATE_REPOSITORY
VENDOR_REPOSITORY
PRIVATE_REPOSITORY
UNKNOWN
```

Si un artifact vendor no está en Central:

```text
PRIVATE_VENDOR_REPOSITORY_REQUIRED
```

No reportarlo simplemente como artifact inexistente.

---

# 13. Política de continuación

Aunque el Maven model esté bloqueado por el BOM vendor:

```text
STATIC_ANALYSIS = CONTINUE
XML_ROUTE_ANALYSIS = CONTINUE
INTEGRATION_INVENTORY = CONTINUE
DEPENDENCY_INVENTORY = CONTINUE
```

Mientras que:

```text
SEMANTIC_MAVEN_ANALYSIS = PARTIAL
RECIPES_REQUIRING_VALID_MODEL = BLOCKED
COMPILE = NOT_EXECUTABLE
TEST = NOT_RUN
```

---

# 14. Estado de análisis

Ejemplo:

```text
Analysis status:
PARTIAL_SUCCESS

Reason:
Vendor BOM unavailable
```

No convertir todo el workspace automáticamente en:

```text
BLOCKED
```

si el análisis estático sí puede continuar.

---

# 15. Migración

La migración debe poder generar:

```text
MIGRATION_PLAN
```

aunque la ejecución automática esté bloqueada.

Ejemplo:

```text
Migration plan:
AVAILABLE

Automatic recipes:
BLOCKED

Reason:
VENDOR_BOM_UNRESOLVED
```

---

# 16. Runtime migration

Si se detecta:

```text
Red Hat Fuse
Karaf
OSGi
Blueprint
bundle packaging
```

el plan debe reflejar:

```text
ARCHITECTURAL_MIGRATION_REQUIRED
```

Ejemplo target:

```text
Spring Boot 3
Camel 4
Java 21
```

No tratar el cambio como únicamente:

```text
Camel 2.x → Camel 4.x
```

---

# 17. Relación con camel-blueprint

Si además existe:

```text
camel-blueprint
```

registrar:

```text
Source runtime:
RED_HAT_FUSE_KARAF

Blueprint:
DETECTED

Runtime migration:
REQUIRED

Artifact migration:
camel-blueprint → N/A
```

---

# 18. Integración con waves

Un módulo bloqueado por vendor BOM no debe detener otros módulos independientes.

Ejemplo:

```text
Wave 1

common-schedule-exchangerate-service
BASELINE_BLOCKED_PLATFORM_BOM

common-epay
BASELINE_SUCCESS

common-util
BASELINE_SUCCESS
```

Resultado:

```text
common-schedule-exchangerate-service
analysis = PARTIAL
migration execution = BLOCKED

common-epay
continue

common-util
continue
```

---

# 19. Dependientes internos

Si otro proyecto depende de un módulo bloqueado:

```text
dependency graph
```

debe decidir si:

```text
analysis can continue
```

pero:

```text
migration build = BLOCKED_BY_DEPENDENCY
```

solo cuando realmente necesite el candidate del módulo bloqueado.

---

# 20. UI recomendada

```text
Platform
Red Hat Fuse / Karaf

Vendor BOM
⚠ No disponible

org.jboss.redhat-fuse:fuse-karaf-bom
7.10.0.fuse-sb2-7_10_1-00008-redhat-00001

Baseline repair
Parcial

Camel dependencies recovered
10

Dependencies still unresolved
2

Static analysis
✓ Disponible

Automatic migration
Bloqueada

Required action
Configurar repositorio/vendor BOM o proveer dependency-management equivalente
```

---

# 21. Reporte esperado

```text
Source platform:
RED_HAT_FUSE_KARAF

Camel:
2.23.2

Vendor BOM:
org.jboss.redhat-fuse:fuse-karaf-bom:
7.10.0.fuse-sb2-7_10_1-00008-redhat-00001

Vendor BOM status:
UNRESOLVED

Baseline repair:
PARTIAL

Static analysis:
PASS

Semantic Maven analysis:
PARTIAL

Recipes:
BLOCKED

Compile:
NOT_EXECUTABLE

Migration status:
BLOCKED

Primary reason:
PRIVATE_VENDOR_REPOSITORY_REQUIRED
```

---

# 22. Criterios de aceptación

## Caso 1

Fuse BOM no disponible.

Resultado:

```text
BASELINE_BLOCKED_PLATFORM_BOM
```

PASS.

## Caso 2

Camel versions pueden recuperarse por `camel-bom`.

Resultado:

```text
BASELINE_REPAIR_PARTIAL
```

PASS.

## Caso 3

SLF4J u otras dependencias siguen sin versión.

No inventar versión.

PASS.

## Caso 4

Análisis XML/estático continúa.

PASS.

## Caso 5

Recipes dependientes de Maven model válido quedan bloqueadas.

PASS.

## Caso 6

Otros proyectos independientes de la wave continúan.

PASS.

## Caso 7

El reporte identifica explícitamente Red Hat Fuse/Karaf.

PASS.

---

# 23. Arquitectura

```text
PlatformDetector
      ↓
VendorBomAnalyzer
      ↓
DependencyManagementSnapshot
      ↓
BaselineRepairEngine
      ↓
BaselinePolicyEngine
      ↓
Analysis / Migration Decision
```

---

# 24. Prioridad

```text
HIGH
```

Razón:

En repositorios empresariales legacy, especialmente los basados en JBoss Fuse / Red Hat Fuse / Karaf, el BOM de plataforma puede administrar gran parte del stack.

El migrador debe reconocer la plataforma y evitar reemplazos parciales que produzcan un POM aparentemente reparado pero semánticamente incorrecto.
