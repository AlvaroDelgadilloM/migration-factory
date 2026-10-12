# Prompt para Claude — aplicar correcciones P0/P1 de Migration Factory

Trabaja sobre el código existente de Migration Factory.

No reestructures el proyecto innecesariamente. Implementa las correcciones siguientes respetando las APIs existentes cuando sea posible.

## P0-1 — `preflight.repair()` default binding

Localiza la función que actualmente enlaza `_artifact_exists` como argumento por defecto.

Cambiar:

```python
def repair(..., exists=_artifact_exists):
```

por inyección en runtime:

```python
def repair(..., exists=None):
    artifact_exists = exists if exists is not None else _artifact_exists
```

Actualizar llamadas internas y pruebas.

La prueba con monkeypatch debe pasar.

---

## P0-2 — No perder DependencyEvidence

Localiza el Camel component planning y el preflight compatibility gate.

El planning ya calcula información semántica rica.

No volver a llamar `decide()` con un contexto reducido.

Preferencia:
- persistir/reutilizar `CamelComponentDecision`;
- preflight valida la decisión existente.

Si se recalcula, debe recibir el mismo `ProjectEvidence`, `DependencyEvidence`, endpoints, packaging, runtime/platform y usage.

Agregar tests que garanticen que un `camel-cxf` identificado como SOAP/Blueprint no vuelva a `CXF_USAGE_AMBIGUOUS`.

---

## P0-3 — Estados Maven cuando se usan stubs

Cuando el modo degradado instala stubs:

```text
candidate/maven-model PASS
```

no debe equivaler a dependencia real resuelta.

Agregar estado explícito:

```text
PASS_WITH_STUBS
```

o separar:

```text
candidateModelStructural
candidateDependencyResolution
candidateCompileValidation
```

Registrar:

```text
resolutionMode=DEGRADED_WITH_STUBS
```

y lista de stubs.

---

## P1-1 — StubLeakGuard

Crear un gate reutilizable que se ejecute antes de compile/test/package/export.

Debe validar:

- no repo stub en candidate;
- no dependency stub en POM;
- no `.m2-degraded` dentro del candidate;
- overlay deshabilitado;
- classpath final sin stubs.

Bloquear packaging si falla.

---

## P1-2 — POM semantic dedupe

La deduplicación de dependencias debe comparar:

```text
scope
optional
exclusions
systemPath
version
```

Además de la identity key Maven.

Solo eliminar automáticamente duplicados semánticamente equivalentes.

Si hay diferencias:

```text
POM_DUPLICATE_SEMANTIC_CONFLICT
```

---

## P1-3 — Estado único API/CLI/workspace

Crear o consolidar un modelo de estado compartido con:

```text
baselineModel
baselineDependencyResolution
baselineCompile
recipeExecution
candidateModel
candidateDependencyResolution
candidateCompile
candidateTest
stubMode
validationConfidence
primaryReason
secondaryReasons
```

Todos los caminos deben calcular el estado final con las mismas reglas.

---

## Tests obligatorios

Agregar o corregir tests para:

1. monkeypatch de `_artifact_exists`;
2. CXF evidence preservation;
3. Maven model con stubs != real dependency resolution;
4. StubLeakGuard;
5. dependency dedupe con exclusions distintas;
6. mismos estados en CLI y workspace.

Ejecuta el test suite relevante y corrige cualquier regresión causada por estos cambios.

No modifiques behavior unrelated.
