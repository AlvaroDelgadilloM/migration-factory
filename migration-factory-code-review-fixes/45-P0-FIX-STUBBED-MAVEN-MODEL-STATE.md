# P0 — Fix: separar Maven model estructural de dependency resolution real

## Problema

En modo degradado se instalan stubs en un repositorio Maven temporal para permitir parsing/OpenRewrite.

Después puede ejecutarse una validación del candidate que termina en:

```text
candidate/maven-model: PASS
```

aunque el proyecto real siga sin poder resolver una dependencia.

Ejemplo:

```text
com.oracle:ojdbc6:11.2.0.4
```

## Riesgo

Un usuario puede interpretar:

```text
PASS
```

como candidate sano.

## Nuevos estados

Separar:

```text
CANDIDATE_MODEL_STRUCTURAL
CANDIDATE_DEPENDENCY_RESOLUTION
CANDIDATE_COMPILE_VALIDATION
```

Ejemplo:

```text
CANDIDATE_MODEL_STRUCTURAL=PASS
CANDIDATE_DEPENDENCY_RESOLUTION=BLOCKED_EXTERNAL_DEPENDENCY
CANDIDATE_COMPILE_VALIDATION=UNVERIFIED
```

## Si hubo stubs

Registrar:

```text
resolutionMode=DEGRADED_WITH_STUBS
```

y nunca reportar simplemente:

```text
candidate/maven-model: PASS
```

Usar:

```text
candidate/maven-model: PASS_WITH_STUBS
```

o preferiblemente campos separados.

## Regla

Los stubs sirven para:

```text
OpenRewrite
source parsing
limited type attribution
migration planning
```

No sirven como evidencia de:

```text
real dependency resolution
final compile
tests
package correctness
```

## Criterios de aceptación

- Un candidate validado con overlay temporal nunca aparece como totalmente verde.
- El reporte enumera dependencias stubbed.
- `compileValidation=UNVERIFIED` hasta compilar sin stubs.
