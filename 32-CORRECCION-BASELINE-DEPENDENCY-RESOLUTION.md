# Corrección: clasificación de fallos de baseline por dependencias externas

## Objetivo

Mejorar el comportamiento del migrador cuando un módulo no puede completar su baseline debido a una dependencia que Maven no logra resolver, aunque el código del proyecto no haya sido validado todavía.

Caso observado:

```text
com.oracle:ojdbc6:11.2.0.4
Could not find artifact
```

y después:

```text
Baseline: BASELINE_FAILED_KNOWN
```

Este estado es demasiado genérico.

El sistema debe distinguir entre:

```text
código que realmente no compila
```

y:

```text
baseline no reproducible por una dependencia externa
```

---

# 1. Problema actual

Cuando Maven devuelve:

```text
DependencyResolutionException
```

el migrador puede terminar clasificando:

```text
BASELINE_FAILED
```

o:

```text
BUILD FAIL
```

Esto puede inducir a error porque el compilador Java nunca llegó a ejecutarse.

Ejemplo:

```text
Could not resolve dependencies for project
com.posadas.common:common-datasource

dependency:
com.oracle:ojdbc6:jar:11.2.0.4
```

En este escenario no sabemos todavía si el código fuente compila.

---

# 2. Nuevos estados de baseline

Agregar:

```text
BASELINE_SUCCESS
BASELINE_FAILED_CODE
BASELINE_BLOCKED_DEPENDENCY
BASELINE_BLOCKED_REPOSITORY
BASELINE_BLOCKED_AUTH
BASELINE_BLOCKED_NETWORK
BASELINE_BLOCKED_INTERNAL_ARTIFACT
BASELINE_UNKNOWN
```

---

# 3. BASELINE_FAILED_CODE

Usar únicamente cuando:

```text
dependencies resolved
compiler started
source compilation failed
```

Ejemplo:

```text
CompilationFailureException
cannot find symbol
incompatible types
```

Resultado:

```text
BASELINE_FAILED_CODE
```

---

# 4. BASELINE_BLOCKED_DEPENDENCY

Usar cuando:

```text
DependencyResolutionException
```

y el artifact no puede resolverse.

Ejemplo:

```text
com.oracle:ojdbc6:11.2.0.4
```

Resultado:

```text
BASELINE_BLOCKED_DEPENDENCY
```

---

# 5. Clasificador de resolución de dependencias

Crear:

```text
baseline/
└── dependency_resolution_classifier.py
```

Debe detectar:

```text
ARTIFACT_NOT_FOUND
PRIVATE_REPOSITORY_REQUIRED
AUTH_REQUIRED
NETWORK_ERROR
VERSION_NOT_AVAILABLE
INTERNAL_ARTIFACT_MISSING
REPOSITORY_POLICY_BLOCK
CACHED_RESOLUTION_FAILURE
```

---

# 6. ARTIFACT_NOT_FOUND

Ejemplo:

```text
Could not find artifact
```

Resultado:

```json
{
  "baselineStatus": "BASELINE_BLOCKED_DEPENDENCY",
  "dependencyReason": "ARTIFACT_NOT_FOUND"
}
```

---

# 7. PRIVATE_REPOSITORY_REQUIRED

Cuando una dependencia parece interna o comercial y no está disponible en repos públicos.

Ejemplo:

```text
com.company.internal:legacy-client
```

o drivers comerciales antiguos.

Resultado:

```text
BASELINE_BLOCKED_REPOSITORY
```

---

# 8. AUTH_REQUIRED

Detectar errores como:

```text
401
403
authentication failed
authorization failed
```

Resultado:

```text
BASELINE_BLOCKED_AUTH
```

---

# 9. NETWORK_ERROR

Detectar:

```text
connection timed out
unknown host
connection refused
TLS failure
```

Resultado:

```text
BASELINE_BLOCKED_NETWORK
```

---

# 10. INTERNAL_ARTIFACT_MISSING

Si la dependencia pertenece al mismo workspace, por ejemplo:

```text
com.posadas.common:common-datasource
```

y el módulo interno no fue construido/instalado todavía:

```text
BASELINE_BLOCKED_INTERNAL_ARTIFACT
```

Esto debe integrarse con el grafo de dependencias y las waves.

---

# 11. CACHED_RESOLUTION_FAILURE

Maven puede registrar:

```text
was not found during a previous attempt
resolution is not reattempted until the update interval has elapsed
```

Clasificar:

```text
CACHED_RESOLUTION_FAILURE
```

No confundir con un nuevo intento real.

El reporte debe sugerir:

```text
mvn -U
```

solo como diagnóstico, no como autofix indiscriminado.

---

# 12. Política de continuación

No todos los baseline bloqueados deben detener análisis y migración.

## Análisis

Si:

```text
BASELINE_BLOCKED_DEPENDENCY
```

permitir:

```text
STATIC_ANALYSIS = CONTINUE
SEMANTIC_ANALYSIS = CONTINUE_WHERE_POSSIBLE
```

Estado:

```text
ANALYSIS_PARTIAL
```

---

# 13. Migración en modo degradado

Si la dependencia ausente no está relacionada con la transformación principal:

```text
migrationMode = DEGRADED
```

Ejemplo:

```text
ojdbc6
```

en una migración Camel.

El sistema puede continuar:

```text
analysis
migration plan
safe transformations
POM modernization
```

pero debe marcar:

```text
VALIDATION_CONFIDENCE = REDUCED
```

---

# 14. Cuándo bloquear migración

Bloquear cuando la dependencia ausente:

- es necesaria para resolver tipos usados por recipes;
- pertenece al framework que se está migrando;
- es un módulo interno requerido y aún no migrado;
- impide parseo semántico;
- impide validar un cambio estructural crítico.

Resultado:

```text
MIGRATION_BLOCKED_BY_BASELINE_DEPENDENCY
```

---

# 15. Ejemplo de decisión

Caso:

```text
com.oracle:ojdbc6:11.2.0.4
```

Contexto:

```text
target migration:
Camel 2/3 → Camel 4
```

Clasificación:

```text
dependencyCategory = DATABASE_DRIVER
migrationCriticality = LOW
```

Resultado:

```text
analysis = CONTINUE
migration = DEGRADED
buildValidation = BLOCKED
confidence = REDUCED
```

---

# 16. No reemplazar dependencias automáticamente sin contexto

No hacer:

```text
ojdbc6 → ojdbc11
```

automáticamente.

Antes se requiere conocer:

```text
Oracle DB version
JDBC compatibility
application server constraints
runtime target
driver licensing/distribution
```

Por tanto:

```text
MANUAL_REVIEW
```

---

# 17. Baseline report

Generar:

```text
baseline-report.md
```

Ejemplo:

```text
Project:
commons/common-datasource

Baseline status:
BASELINE_BLOCKED_DEPENDENCY

Dependency:
com.oracle:ojdbc6:11.2.0.4

Reason:
ARTIFACT_NOT_FOUND

Compiler executed:
NO

Code compile status:
UNKNOWN

Migration impact:
LOW

Analysis:
CONTINUE

Migration:
DEGRADED

Validation confidence:
REDUCED
```

---

# 18. Diferenciar build FAIL de build NOT_EXECUTABLE

Incorrecto:

```text
build FAIL
```

Correcto:

```text
build NOT_EXECUTABLE

reason:
DEPENDENCY_RESOLUTION_BLOCKED
```

Porque el build no llegó realmente a compilación.

---

# 19. Estados de build

Agregar:

```text
BUILD_SUCCESS
BUILD_FAILED_CODE
BUILD_FAILED_TEST
BUILD_BLOCKED_DEPENDENCY
BUILD_BLOCKED_REPOSITORY
BUILD_BLOCKED_AUTH
BUILD_BLOCKED_NETWORK
BUILD_NOT_EXECUTABLE
```

---

# 20. Integración con waves

Ejemplo:

```text
Wave 1

common-datasource
BASELINE_BLOCKED_DEPENDENCY

common-epay
BASELINE_SUCCESS
```

El sistema no debe detener la wave completa.

Resultado:

```text
common-datasource
analysis = PARTIAL
migration = DEGRADED/BLOCKED según política

common-epay
analysis = PASS
migration = CONTINUE
```

---

# 21. Dependientes

Si:

```text
common-epay
depends on common-datasource
```

y el migration plan de `common-epay` necesita el candidate migrado de `common-datasource`:

```text
common-epay
→ BLOCKED_BY_DEPENDENCY
```

Si no necesita ese candidate para análisis:

```text
common-epay analysis
→ CONTINUE
```

---

# 22. Política configurable

Agregar configuración:

```yaml
baseline:
  allowExternalDependencyFailure: true
  allowNetworkFailure: false
  allowAuthFailure: false
  allowInternalArtifactFailure: false
```

Opcional:

```text
--allow-baseline-external-dependency-failure
```

más específico que:

```text
--allow-baseline-failure
```

---

# 23. Evitar flag demasiado genérico

Actualmente:

```text
--allow-baseline-failure
```

puede permitir demasiado.

Preferir:

```text
--allow-baseline-dependency-failure
--allow-baseline-network-failure
--allow-baseline-repository-failure
```

y mantener una política por categoría.

---

# 24. Confidence scoring

Ejemplo:

```text
Baseline reproducible:
+30

Dependencies resolved:
+20

Compile passed:
+30

Tests passed:
+20
```

Si dependency resolution falla:

```text
Validation confidence:
45/100
```

El sistema debe comunicar que la migración no ha sido completamente validada.

---

# 25. UI recomendada

Ejemplo:

```text
common-datasource

Baseline
⚠ Bloqueado por dependencia externa

com.oracle:ojdbc6:11.2.0.4

Código:
No validado

Análisis:
Continuado parcialmente

Migración:
Modo degradado

Acción requerida:
Proveer artifact/repository compatible
```

---

# 26. Root cause

No mostrar únicamente:

```text
BASELINE_FAILED_KNOWN
```

Mostrar:

```text
BASELINE_BLOCKED_DEPENDENCY

rootCause:
ARTIFACT_NOT_FOUND

artifact:
com.oracle:ojdbc6:11.2.0.4
```

---

# 27. Criterios de aceptación

## Caso 1

DependencyResolutionException antes de compiler.

Resultado:

```text
BASELINE_BLOCKED_DEPENDENCY
```

PASS.

## Caso 2

CompilationFailureException.

Resultado:

```text
BASELINE_FAILED_CODE
```

PASS.

## Caso 3

Dependency externa ausente de baja criticidad.

Resultado:

```text
analysis continues
migration degraded
confidence reduced
```

PASS.

## Caso 4

Artifact interno del workspace ausente.

Resultado:

```text
BASELINE_BLOCKED_INTERNAL_ARTIFACT
```

PASS.

## Caso 5

Una dependencia bloqueada no cancela proyectos independientes de la wave.

PASS.

---

# 28. Arquitectura

```text
MavenBaselineRunner
       ↓
BaselineErrorClassifier
       ↓
DependencyResolutionClassifier
       ↓
BaselinePolicyEngine
       ↓
Analysis/Migration Decision
```

---

# 29. Prioridad

```text
HIGH
```

Razón:

En repositorios empresariales legacy es común depender de:

```text
drivers comerciales
repositorios privados
artifacts internos
versiones históricas
```

El migrador debe distinguir correctamente entre:

```text
el código está roto
```

y:

```text
el entorno no puede reproducir el baseline
```

para evitar falsos negativos y bloquear migraciones innecesariamente.
