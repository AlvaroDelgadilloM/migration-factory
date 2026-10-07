# Corrección: orden y aislamiento de recipes OpenRewrite

## Objetivo

Evitar que varias recipes modifiquen las mismas dependencias en una sola ejecución y generen estados inconsistentes.

Caso actual:

```text
mf.camel.Camel4Artifacts
org.apache.camel.upgrade.CamelMigrationRecipe
```

activas simultáneamente.

## Problema

Dos recipes pueden:

- reescribir el mismo artifact;
- aplicar versiones diferentes;
- introducir reemplazos duplicados;
- volver a agregar un artifact ya reemplazado.

## Nuevo modelo

Separar fases:

```text
PHASE 1
Official Camel Migration Recipe
        ↓
POM NORMALIZATION
        ↓
PHASE 2
Custom Compatibility Mapping
        ↓
POM NORMALIZATION
        ↓
CAMEL VERSION CONSISTENCY GATE
        ↓
ARTIFACT AVAILABILITY GATE
```

No ejecutar recipes que escriben dependencias incompatibles en la misma fase.

## Recipe Execution Contract

Cada recipe debe declarar:

```yaml
name: mf.camel.Camel4Artifacts
writes:
  - pom.dependencies
  - pom.dependencyManagement
reads:
  - camel.version
requires:
  - targetRuntime
```

Si dos recipes escriben el mismo dominio:

```text
recipe conflict
```

El orquestador debe serializarlas.

## Snapshot por fase

Antes de cada recipe:

```text
snapshot
```

Después:

```text
diff
normalize
validate
```

Si falla:

```text
rollback phase
```

## Criterios de aceptación

- No debe haber dos recipes mutando el mismo artifact sin normalización intermedia.
- Cada fase debe producir diff independiente.
- Un fallo de fase debe revertir solo esa fase.
