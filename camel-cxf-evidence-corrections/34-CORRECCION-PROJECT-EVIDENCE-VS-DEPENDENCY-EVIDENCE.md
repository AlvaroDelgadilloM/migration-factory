# Corrección: ProjectEvidence vs DependencyEvidence

## Problema
La misma lista de señales se está copiando a todas las dependencias Camel.

Ejemplo incorrecto:
```text
OSGI-INF/blueprint
apiContextPath
apiProperty
cxf:
restConfiguration
```

## Nuevo modelo
```text
ProjectEvidence
DependencyEvidence
```

### ProjectEvidence
Contexto global:
- packaging
- runtime
- Blueprint/OSGi
- Rest DSL
- presencia CXF

### DependencyEvidence
Evidencia específica del componente.

Ejemplo `camel-cxf`:
```text
cxf:
CXF bean
JAX-WS/JAX-RS
archivo y línea
```

Ejemplo `camel-jaxb`:
```text
javax.xml.bind
jakarta.xml.bind
@XmlRootElement
JAXBContext
Marshaller
Unmarshaller
```

Ejemplo `camel-swagger-java`:
```text
restConfiguration
apiContextPath
apiProperty
Swagger/OpenAPI APIs
```

## Regla
Nunca:
```python
dependency.evidence = project.evidence
```

Usar:
```python
dependency.evidence = correlate(dependency, project_evidence, source_index)
```

## Criterios
- `cxf:` no aparece como evidencia JAXB.
- Cada finding incluye archivo/línea cuando sea posible.
- Sin evidencia específica -> `Dependency evidence: NONE`.
