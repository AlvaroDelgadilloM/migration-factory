# Motor de Reglas de Migración

## Objetivo
Separar el conocimiento de migración del código Python.

Las reglas deben poder mantenerse en YAML.

## Ejemplo

```yaml
- id: CAMEL_HTTP4_TO_HTTP
  category: camel-component
  detect:
    dependency: camel-http4
  target:
    dependency: camel-http
  action: automatic
  confidence: 0.99
```

## Reglas iniciales

### Camel

- camel-http4 -> camel-http
- camel-quartz2 -> camel-quartz
- componentes eliminados -> equivalente Camel 4
- APIs Camel 2 eliminadas -> nuevas APIs

### Java/Jakarta/Spring

No realizar reemplazo ciego `javax` -> `jakarta`.

Clasificar primero:

- javax.inject
- javax.enterprise
- javax.servlet
- javax.jms
- javax.persistence
- javax.xml

Determinar si la migración requiere:

- Jakarta API
- Spring Bean
- Constructor Injection
- Spring configuration

### JBoss

Detectar:

- JNDI
- jboss-web.xml
- deployments overlays
- modules
- server-specific properties

Migrar cuando sea posible a configuración Spring Boot.

### Packaging

```text
WAR -> executable JAR
```

Eliminar configuraciones del servidor de aplicación que ya no sean necesarias.

## Tipos de acción

- AUTOMATIC
- AUTOMATIC_WITH_REVIEW
- MANUAL
- BLOCKED

## Regla de seguridad
Una transformación con confidence bajo no debe modificar automáticamente código crítico.
