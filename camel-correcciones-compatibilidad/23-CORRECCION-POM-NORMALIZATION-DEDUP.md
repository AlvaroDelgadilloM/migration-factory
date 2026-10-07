# Corrección: normalización y deduplicación del POM

## Objetivo

Evitar POMs malformados por dependencias o plugins duplicados después de recipes.

Ejemplo:

```text
duplicate declaration:
org.apache.camel:camel-openapi-java
```

## Normalización

Ejecutar después de cada fase que modifique el POM.

```text
RECIPE
  ↓
POM NORMALIZER
  ↓
POM SANITY GATE
```

## Dependency key

Usar:

```text
groupId
artifactId
type
classifier
```

Pseudocódigo:

```python
key = (
    dep.group_id,
    dep.artifact_id,
    dep.type or "jar",
    dep.classifier or ""
)
```

Si hay duplicados:

- conservar una sola declaración;
- preferir versión administrada por BOM;
- eliminar versiones redundantes;
- detectar conflictos de scope;
- detectar exclusions incompatibles.

## Plugins

Aplicar lógica equivalente a:

```text
groupId
artifactId
```

## Properties

Modernizar:

```text
${artifactId}
→
${project.artifactId}
```

clasificación:

```text
SAFE_AUTOFIX
```

## Sanity Gate

Validar:

- dependencias duplicadas;
- plugins duplicados;
- dependencyManagement duplicado;
- BOMs incompatibles;
- properties inválidas;
- placeholders sin resolver.

## Resultado

```text
POM_SANITY_GATE: PASS
```

antes de ejecutar Maven compile.

## Criterios de aceptación

- No debe quedar más de una dependencia equivalente.
- No debe aparecer warning de duplicate dependency.
- `${artifactId}` debe migrarse a `${project.artifactId}`.
