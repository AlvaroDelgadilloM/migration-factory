# Corrección: SUPPORTED no significa código compatible

Para `camel-core`, `camel-jaxb`, `camel-servlet` separar:

```text
Artifact support
Source compatibility
Runtime/config compatibility
```

Ejemplo `camel-core`:
```text
Artifact status: SUPPORTED
Auto dependency migration: YES
Source compatibility: REQUIRES_VALIDATION
```

Ejemplo `camel-jaxb`:
```text
Artifact status: SUPPORTED
Auto dependency migration: YES
Source migration: CONDITIONAL
Prerequisites:
- Jakarta JAXB migration
- runtime Jakarta-compatible
- compile validation
```

Ejemplo `camel-servlet`:
```text
Artifact status: SUPPORTED
Auto dependency migration: YES
Runtime/config migration: CONDITIONAL
```
