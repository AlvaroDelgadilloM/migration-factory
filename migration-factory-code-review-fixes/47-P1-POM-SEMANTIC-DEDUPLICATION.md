# P1 — Deduplicación semántica de dependencias Maven

## Problema

Deduplicar solo por:

```text
groupId
artifactId
type
classifier
```

puede borrar información semántica.

Dos dependencias con la misma clave pueden diferir en:

```text
scope
optional
exclusions
systemPath
version
```

## Regla

Primero calcular:

```text
identityKey =
groupId + artifactId + type + classifier
```

Después comparar semántica.

## Caso seguro

Si ambos nodos son equivalentes:

```text
AUTO_DEDUP
```

## Caso conflictivo

Si difieren:

```text
POM_DUPLICATE_SEMANTIC_CONFLICT
```

No eliminar automáticamente.

## Estructura

```python
def dependency_semantic_signature(dep):
    return {
        "scope": dep.scope or "compile",
        "optional": dep.optional or False,
        "systemPath": dep.system_path,
        "exclusions": sorted(...),
        "version": dep.version
    }
```

## Política de merge

Solo permitir merge automático cuando:

- exclusions compatibles;
- scopes equivalentes;
- optional equivalente;
- version redundante por BOM o idéntica.

Si no:

```text
MANUAL_REVIEW
```

## Criterios de aceptación

- No perder exclusions.
- No cambiar scope silenciosamente.
- No convertir optional=false/true.
- Duplicados idénticos sí se eliminan.
