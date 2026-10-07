# Corrección: formato del reporte Camel

## Formato recomendado
```text
## camel-cxf

Source dependency:
camel-cxf

Target status:
ARCHITECTURAL_MAPPING

Target:
DETECT_BY_USAGE

Possible targets:
- camel-cxf-soap
- camel-cxf-spring-soap
- camel-cxf-rest
- camel-cxf-spring-rest

Project context:
- packaging=bundle
- runtime=OSGI_BLUEPRINT

Dependency evidence:
- type=CAMEL_URI_SCHEME
- value=cxf:
- file=src/main/resources/OSGI-INF/blueprint/routes.xml
- line=42

Detected usage:
SOAP

Artifact confidence:
HIGH

Usage confidence:
HIGH

Migration confidence:
MEDIUM

Auto dependency migration:
CONDITIONAL

Runtime migration:
REQUIRED
```

Eliminar el bloque genérico repetido `Code usage found`.
