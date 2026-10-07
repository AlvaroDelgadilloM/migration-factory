# Tests de regresión: CXF y Evidence

## Test 1: SOAP Blueprint
Esperado:
```text
status=ARCHITECTURAL_MAPPING
usage=SOAP
runtime=OSGI_BLUEPRINT
autoMigration=CONDITIONAL
```

## Test 2: REST Spring XML
Esperado:
```text
target=camel-cxf-spring-rest
```

## Test 3: evidence isolation
Input:
```text
cxf:
javax.xml.bind
apiContextPath
```

Expected:
```text
camel-cxf -> cxf:
camel-jaxb -> javax.xml.bind
camel-swagger-java -> apiContextPath
```

## Test 4: sin evidencia específica
POM contiene `camel-jaxb`, pero no hay uso JAXB.

Expected:
```text
usageConfidence=LOW
migrationConfidence<=MEDIUM
```

## Test 5: Blueprint
Debe reportar runtimes objetivo, no artifacts alternativos.

## Test 6: CXF ambiguo
Nunca seleccionar `camel-cxf-soap` automáticamente.
