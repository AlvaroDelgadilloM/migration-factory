# Plan de implementación P0/P1

## P0

### 1. Fix Python binding
Archivo principal esperado:
```text
backend/mf/preflight.py
```

### 2. Preservar decisiones Camel
Archivos esperados:
```text
backend/mf/dependency/preflight_gates.py
backend/mf/.../planning.py
backend/mf/.../camel_compat.py
```

### 3. Estados con stubs
Archivos esperados:
```text
backend/mf/cli_tool.py
backend/mf/mavenops.py
backend/mf/reporting/*
```

## P1

### 4. StubLeakGuard
Crear módulo dedicado, preferentemente:
```text
backend/mf/dependency/stub_leak_guard.py
```

### 5. Semantic POM dedupe
Archivo esperado:
```text
backend/mf/pom/normalizer.py
```

### 6. Unified state model
Crear/reusar modelo compartido para:
```text
API
CLI
workspace orchestrator
```

## Orden recomendado

```text
P0-1
↓
P0-2
↓
P0-3
↓
tests
↓
P1-1
↓
P1-2
↓
P1-3
↓
full regression
```

## Definition of Done

- Todos los tests existentes pasan.
- Nuevos tests P0/P1 pasan.
- No hay falso `PASS` por stubs.
- CXF conserva evidence.
- Dedupe no cambia semántica Maven.
- API/CLI/workspace coinciden en estados.
