# P0 — Fix: `preflight.repair()` y default binding de Python

## Problema

La función está definida con una dependencia enlazada como argumento por defecto:

```python
def repair(root, modules, findings, exists=_artifact_exists):
```

En Python, el valor por defecto se evalúa cuando se define la función.

Eso provoca que:

```python
monkeypatch.setattr(preflight, "_artifact_exists", fake)
```

no cambie el callable realmente utilizado por `repair()`.

Esto rompe pruebas y dificulta inyección/mocking.

## Corrección

Cambiar a:

```python
def repair(root, modules, findings, exists=None):
    exists = exists or _artifact_exists
```

Mejor aún:

```python
def repair(root, modules, findings, *, exists=None):
    artifact_exists = exists if exists is not None else _artifact_exists
```

y usar:

```python
artifact_exists(...)
```

dentro de la función.

## Criterios de aceptación

- `monkeypatch` sobre `_artifact_exists` debe funcionar.
- La prueba `test_cli_preflight_and_migrate_gate` debe pasar.
- No cambiar el comportamiento productivo.
- No introducir network lookup adicional.
