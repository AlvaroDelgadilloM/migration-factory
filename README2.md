# Camel 2 + JBoss Legacy Demo

Proyecto deliberadamente legado para probar herramientas de análisis y migración hacia Camel 4 con Spring Boot o Quarkus.

## Stack simulado
- Java 8
- Maven
- WAR
- Apache Camel 2.24.3
- CDI / Java EE 7 (`javax.*`)
- JBoss / WildFly
- JMS
- Camel Servlet
- Camel HTTP4
- Camel REST DSL
- Ruta Camel XML adicional

## Casos que debería detectar un analizador
1. Uso de `javax.*` en lugar de `jakarta.*`.
2. Empaquetado WAR dependiente de servidor de aplicaciones.
3. Archivo `jboss-web.xml` específico de JBoss.
4. Dependencia `camel-http4`, reemplazada en Camel moderno por `camel-http`.
5. Camel 2.x, incompatible directamente con Camel 4.x.
6. Dependencia de JMS administrado por contenedor/JNDI.
7. Configuración REST basada en `camel-servlet`.
8. Mezcla de rutas Java DSL y Spring XML.
9. Uso de Java 8, insuficiente para Camel 4 actual.
10. Endpoints hardcodeados (`localhost:8080`).
11. Uso de `doubleValue()` para lógica monetaria.
12. Falta de configuración externa / profiles.
13. Pruebas prácticamente inexistentes.
14. Acoplamiento a headers de Camel (`Exchange.HTTP_*`).
15. Uso de `toD()` con URL dinámica que requiere revisión de seguridad/configuración.

## Objetivo del ejercicio
La herramienta de migración debería producir algo semejante a:
- inventario de dependencias;
- versión Java requerida;
- componentes Camel usados;
- APIs `javax` que deben migrar;
- recursos JBoss específicos;
- rutas detectadas;
- endpoints externos;
- dificultad estimada por archivo/ruta;
- plan de migración Camel 2 -> 4;
- destino recomendado: Spring Boot o Quarkus;
- backlog técnico sugerido.

## No es un proyecto productivo
Hay problemas intencionales para que sirva como fixture de análisis.
