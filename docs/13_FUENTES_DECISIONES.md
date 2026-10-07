# Fuentes y decisiones
Consulta: 5 octubre 2026. Usar documentación de la versión elegida al implementar, no asumir que la más reciente es compatible con todo.

- Guía Camel 2→3: https://camel.apache.org/manual/camel-3-migration-guide.html
- Guía Camel 3→4: https://camel.apache.org/manual/camel-4-migration-guide.html
- Migración a Camel 4: https://camel.apache.org/blog/2023/10/migrate4/
- Camel Upgrade Recipes: https://camel.apache.org/manual/camel-upgrade-recipes-tool.html
- Upgrades Camel 4: https://camel.apache.org/manual/camel-4x-upgrade-guide.html
- Spring Boot → Camel Quarkus: https://camel.apache.org/camel-quarkus/3.40.x/migration-guide/camel-spring-boot-to-camel-quarkus.html
- Recetas Jakarta: https://docs.openrewrite.org/recipes/java/migrate/jakarta

## Decisiones corregidas respecto a ideas iniciales
No toda integración requiere servidor HTTP. No toda app Java EE migra más fácil a Spring que a Quarkus; depende de sus acoplamientos. No todo javax pasa a jakarta. ActiveMQ requiere evaluación de versión/componente, no sustitución obligatoria por Artemis. No publicar porcentajes de readiness o ahorro sin metodología y evidencia.

Java 21 es un candidato para el piloto, no una garantía universal: confirmar con BOM del runtime seleccionado. Versiones exactas quedan pendientes porque no hay pom.xml real. No inventar una receta OpenRewrite que cubra automáticamente toda la migración 2→4.

## ADR iniciales
ADR-001 Python orquesta, OpenRewrite transforma Java/Maven.
ADR-002 API modular + worker aislado; evitar microservicios por analizador.
ADR-003 preservar origen y generar candidato por SHA.
ADR-004 Spring y Quarkus mediante perfiles explícitos; pruebas en JVM primero.
ADR-005 IA opcional para propuestas revisables, sin permisos de escritura.
ADR-006 catálogo versionado y outputs sin secretos.
