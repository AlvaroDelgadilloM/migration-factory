# Corrección: Compile Error Classifier + autofix multi-round

## Objetivo

Mejorar la fase posterior al candidate compile para que no termine inmediatamente con:

```text
Sin correcciones conocidas para los errores restantes
```

sin haber clasificado los errores reales.

## Errores soportados

```text
cannot find symbol
package ... does not exist
method ... cannot be applied
constructor ... cannot be applied
incompatible types
method does not override
CompilationFailureException
```

## Modelo estructurado

```json
{
  "type": "JAVA_COMPILATION_ERROR",
  "file": "src/main/java/.../Route.java",
  "line": 42,
  "errorType": "CANNOT_FIND_SYMBOL",
  "symbol": "SwaggerDefinition",
  "confidence": 0.95
}
```

## Rondas

```text
Round 0
compile
↓
parse errors
↓
apply known fixes

Round 1
compile
↓
parse remaining errors
↓
apply known fixes

Round 2
compile
↓
manual actions
```

Configurable:

```text
maxAutofixRounds = 3
```

## Reglas

- No repetir el mismo fix infinitamente.
- Guardar snapshot por ronda.
- Si el número de errores aumenta, rollback.
- Si no hay progreso, detener.

## Criterios de aceptación

- Los errores Java deben aparecer estructurados en el reporte.
- El sistema debe intentar fixes conocidos.
- Debe distinguir error desconocido de ausencia de parser.
