# Validación y Autocorrección

## Principio
La validación debe ejecutarse en dos momentos distintos:

1. `BASELINE`: validar el proyecto legacy antes de migrar.
2. `TARGET`: validar el proyecto nuevo después de aplicar la migración.

Nunca mezclar errores previos del proyecto con errores generados por la transformación.

## Pipeline completo

```text
Environment check
 ↓
POM/build-model validation
 ↓
Baseline repair
 ↓
Baseline build
 ↓
Generate target
 ↓
Transform
 ↓
Target build
 ↓
Parse errors
 ↓
Known fix?
 ├─ Yes -> apply -> retry
 └─ No  -> manual action
```

## Límites

Definir un máximo de intentos de autocorrección para evitar ciclos infinitos:

```text
MAX_FIX_ROUNDS=5
```

Además, cada regla debe llevar un fingerprint para no aplicar la misma corrección repetidamente sobre el mismo error.

## Categorías de error

- `ENVIRONMENT`
- `BUILD_MODEL`
- `DEPENDENCY`
- `SOURCE`
- `MIGRATION`
- `TEST`
- `SECURITY`

## Errores corregibles en baseline

- dependencia Maven sin versión cuando la versión puede inferirse con alta confianza
- BOM Camel ausente
- propiedad Maven referenciada pero declarable de forma inequívoca
- Jansi fallando por `/tmp noexec`
- directorio temporal inválido
- flags de Maven compatibles con ejecución CI/container

### Ejemplo Camel 2

Si se detecta Camel `2.24.3` y dependencias como:

```text
org.apache.camel:camel-cdi
org.apache.camel:camel-http4
org.apache.camel:camel-cxf
```

sin versión, importar `org.apache.camel:camel-bom:2.24.3` o asignar `${camel.version}` explícitamente.

Esta corrección pertenece al `BASELINE REPAIR`; todavía NO se debe cambiar `camel-http4` por `camel-http`.

## Errores corregibles en target

- dependencia Camel renombrada
- import removido
- package `javax` incompatible
- API Camel conocida eliminada
- configuración faltante generable
- starters Spring Boot faltantes
- propiedades renombradas conocidas

## Errores no autocorregibles

- lógica de negocio ambigua
- APIs internas propietarias sin contrato
- JNDI sin información del broker
- comportamiento transaccional desconocido
- librerías sin reemplazo equivalente
- errores funcionales en tests cuyo comportamiento esperado no pueda inferirse

## Maven Builder robusto

El builder debe crear un tmp propio:

```text
<workspace>/.migration-tmp/jansi
```

Configurar:

```text
MAVEN_OPTS=-Djansi.tmpdir=<workspace>/.migration-tmp/jansi -Djansi.force=false
```

Y usar Maven en modo batch:

```bash
mvn -B -e -Dstyle.color=never clean test
```

Alternativa cuando no existen tests:

```bash
mvn -B -e -Dstyle.color=never clean compile
```

## Parseo de errores Maven

El parser debe reconocer al menos:

- `ProjectBuildingException`
- dependencia sin versión
- dependencia no resoluble
- plugin no resoluble
- package/import inexistente
- symbol not found
- incompatibilidad de Java source/release
- tests fallidos
- errores Surefire/Failsafe

## Política de reparación

Una reparación automática requiere:

- confidence >= umbral configurado
- regla conocida
- transformación reversible o auditada
- no modificar el proyecto fuente original
- registro del before/after

## Verificación final

Ejecutar:

```bash
mvn clean test
mvn verify
```

Guardar stdout/stderr por ejecución.

## Resultado ejemplo

```text
Environment: PASS with 1 workaround
Baseline: REPAIRED
Baseline auto fixes: 2
Target build: SUCCESS
Tests: 24/24
Target auto fixes: 6
Manual actions: 3
```
