# Corrección: confidence basado en evidencia

Separar:
```text
Artifact confidence
Usage confidence
Migration confidence
```

## Artifact confidence
HIGH cuando el artifact existe y está validado contra el BOM.

## Usage confidence
HIGH solo con evidencia directa en código/configuración.

## Migration confidence
HIGH solo si:
- target semántico identificado;
- runtime compatible;
- artifact validado;
- transformación validable.

## Scoring sugerido
```text
artifact verified    +30
direct code evidence +25
config evidence      +20
runtime compatible   +15
tests available      +10
```

```text
85-100 HIGH
60-84  MEDIUM
0-59   LOW
```

No asignar HIGH solo porque el artifact exista.
