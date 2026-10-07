# Corrección: camel-blueprint

La clasificación:
```text
ARCHITECTURAL_MIGRATION
```
es correcta.

Cambiar:
```text
Alternatives:
spring-boot (no verificado en el BOM)
quarkus (no verificado en el BOM)
```

por:
```text
Artifact target:
N/A

Target runtime options:
- Spring Boot 3
- Quarkus 3
- Camel Main

Auto dependency migration:
NO

Runtime migration:
REQUIRED
```

Acciones:
- convertir Blueprint beans;
- convertir CamelContext;
- convertir referencias/servicios OSGi;
- quitar `camel-blueprint`;
- retirar metadata OSGi cuando aplique;
- `bundle -> jar` si el runtime destino lo requiere.
