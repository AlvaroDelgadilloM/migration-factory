# Validación y Autocorrección

## Pipeline

```text
Generate
 ↓
mvn clean test
 ↓
Parse errors
 ↓
Known fix?
 ├─ Yes -> apply -> retry
 └─ No  -> manual action
```

## Límites
Definir máximo de intentos de autocorrección para evitar ciclos infinitos.

Ejemplo:

```text
MAX_FIX_ROUNDS=5
```

## Errores corregibles

- dependencia Camel renombrada
- import removido
- package javax incompatible
- API Camel conocida eliminada
- configuración faltante generable

## Errores no autocorregibles

- lógica de negocio ambigua
- APIs internas propietarias
- JNDI sin configuración del broker
- comportamiento transaccional desconocido
- librerías sin reemplazo equivalente

## Verificación

Ejecutar:

```bash
mvn clean test
mvn verify
```

Guardar stdout/stderr por ejecución.

## Resultado

```text
Build: SUCCESS
Tests: 24/24
Auto fixes: 6
Manual actions: 3
```
