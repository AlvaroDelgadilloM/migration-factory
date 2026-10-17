# 58 — SQL / Python Generation

Preferencia:
```text
1. SQL pushdown
2. engine existente
3. Python
4. Spark/dbt si realmente se requiere
```

Convertir cuando sea posible:
- Filter
- Join
- Merge
- RemoveDuplicates
- Sort
- Column mapping
- Transformer simple

Transformer states:
```text
TRANSLATED_SQL
TRANSLATED_PYTHON
PARTIAL
MANUAL_REVIEW
```

Toda salida generada por IA debe pasar validadores determinísticos.
