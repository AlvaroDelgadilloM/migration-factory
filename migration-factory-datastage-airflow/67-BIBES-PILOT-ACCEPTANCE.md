# 67 — Piloto BIBES

Corpus inicial revisado:
- 8 exportaciones DataStage
- ~55 jobs

Flujos:
```text
BB_BAK_TRANS
OM_ORDER
INF_ADDR_REFERENCE_HIS
INF_CONTACT_PERSON_HIS
AR_ACCOUNT_BALANCE
BB_UNBILL_CHARGE
INF_OFFERING_INSTALLMENT_HIS
OM_ORDER_HIS
```

Patrones:
```text
OracleConnectorPX
PxDataSet
PxSequentialFile
Transformer
Sort
Merge
RemoveDuplicates
Copy
Filter
Join
ColumnGenerator
RowGenerator
JobActivity
Condition
Terminator
ExceptionHandler
ExecCommand
```

Primera meta:
```text
100% parsed
100% inventoried
100% classified
dependency graph generated
manual actions explicit
```

Piloto:
- 1 LOW
- 1 MEDIUM
- 1 HIGH
- 1 master sequence

Estabilizar antes de migración masiva.
