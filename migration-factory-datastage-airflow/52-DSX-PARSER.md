# 52 — DSX Parser

## Objetivo
Convertir `.dsx` a un modelo interno estructurado.

Debe soportar:
- múltiples jobs por archivo;
- sequences;
- stages;
- links;
- parámetros;
- properties;
- connections;
- JobActivity;
- error handling;
- payload desconocido.

Salida:

```python
DataStageProject
```

Estados:

```text
PARSED
PARSED_WITH_WARNINGS
PARTIAL
FAILED
```

Cada elemento debe conservar evidencia:

```python
SourceEvidence(
    source_file="OM_ORDER_HIS.dsx",
    object_name="JP_...",
    record_type="CustomStage",
    property_name="StageType",
    raw_value="OracleConnectorPX"
)
```

No usar regex simples como parser único.
Persistir `analysis/datastage-ir.json`.

Tests mínimos:
- multi-job DSX
- sequence
- Transformer
- OracleConnectorPX
- malformed DSX
- property desconocida
