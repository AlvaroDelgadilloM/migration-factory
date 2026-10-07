# Analizador de Proyecto Legacy

## Objetivo
Construir un modelo técnico del proyecto antes de realizar cualquier modificación.

## Scanner
Debe excluir inicialmente:

```text
.git/
target/
build/
node_modules/
.idea/
```

Debe calcular:

- archivos por tipo
- líneas de código
- módulos Maven
- dependencias
- plugins
- recursos

## Maven Analyzer
Analizar pom.xml y extraer:

- Java source/target
- packaging
- Camel version
- Spring/CDI
- JBoss/WildFly dependencies
- CXF
- JMS provider
- JDBC drivers
- test frameworks

## Java Analyzer
No depender solamente de regex. Se recomienda parser de AST cuando sea posible.

Detectar:

- imports javax.*
- imports jakarta.*
- clases RouteBuilder
- anotaciones CDI
- JNDI InitialContext
- DataSource lookup
- JMS ConnectionFactory
- URLs hardcodeadas
- credenciales
- SQL embebido

## Camel Analyzer
Detectar endpoints dentro de:

```java
from(...)
to(...)
toD(...)
enrich(...)
wireTap(...)
```

Y XML:

```xml
<from uri="..."/>
<to uri="..."/>
```

Clasificar esquemas:

- direct
- seda
- jms
- activemq
- http/http4
- cxf
- sql
- jdbc
- ftp/sftp
- file
- quartz/quartz2
- smtp
- servlet
- rest

## Resultado
Generar `analysis.json` con una estructura similar a:

```json
{
  "java": "8",
  "camel": "2.24.3",
  "runtime": "jboss",
  "packaging": "war",
  "routes": [],
  "dependencies": [],
  "integrations": [],
  "findings": []
}
```

## Severity

- INFO
- LOW
- MEDIUM
- HIGH
- CRITICAL

## Confidence
Cada hallazgo debe tener un confidence entre 0 y 1.
