# Generador del Proyecto Spring Boot

## Target inicial

- Java 21
- Spring Boot 3
- Apache Camel 4
- Maven
- JAR

## Principio
No copiar todo el proyecto legacy y modificarlo indiscriminadamente.

Crear una estructura destino limpia y migrar solamente recursos necesarios.

## Estructura

```text
migrated-project/
├── pom.xml
├── README.md
├── Dockerfile
├── src/main/java/com/company/app/
│   ├── Application.java
│   ├── routes/
│   ├── services/
│   ├── processors/
│   ├── config/
│   └── exception/
├── src/main/resources/
│   ├── application.yml
│   └── logback-spring.xml
└── src/test/java/
```

## POM
Usar dependency management de Spring Boot/Camel en lugar de versiones dispersas.

## Application

```java
@SpringBootApplication
public class Application {
    public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }
}
```

## Configuración
Migrar properties legacy a `application.yml`.

Nunca copiar secretos directamente.

Ejemplo:

```yaml
services:
  payment:
    url: ${PAYMENT_URL:http://localhost:8081}

spring:
  datasource:
    password: ${DB_PASSWORD}
```

## Artefactos JBoss
No copiar si ya no son necesarios:

- jboss-web.xml
- deployment descriptors específicos
- JBoss modules configuration
