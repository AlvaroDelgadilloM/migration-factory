# 55 — Complejidad y migrabilidad

Complejidad:
```text
LOW
MEDIUM
HIGH
VERY_HIGH
```

Migrabilidad:
```text
AUTO_MIGRATABLE
SEMIAUTOMATIC
MANUAL_REVIEW
ARCHITECTURAL_REDESIGN
```

Factores:
- número de stages
- connectors
- Transformer count
- Join/Merge
- routines
- shell commands
- temp DataSets
- branches
- parameters
- sources/targets
- CDC/SCD
- error handling

Salida:

```json
{
  "job": "JP_...",
  "score": 78,
  "complexity": "HIGH",
  "migratability": "SEMIAUTOMATIC",
  "confidence": "MEDIUM",
  "reasons": []
}
```
