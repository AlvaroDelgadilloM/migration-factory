# Resultado esperado del analizador

## Clasificación
- Complejidad: Media-Alta
- Riesgo: Medio-Alto
- Migración automática esperada: 45-60%
- Revisión manual: obligatoria

## Hallazgos principales
| ID | Hallazgo | Severidad | Acción sugerida |
|---|---|---|---|
| MIG-001 | Camel 2.24.3 | Crítica | Migrar APIs/DSL/componentes a Camel 4 |
| MIG-002 | Java 8 | Crítica | Subir mínimo a Java 17 |
| MIG-003 | `javax.*` | Alta | Migrar a `jakarta.*` |
| MIG-004 | `camel-http4` | Alta | Sustituir por `camel-http` |
| MIG-005 | WAR + JBoss | Alta | Reempaquetar como JAR ejecutable si se usa Spring Boot/Quarkus |
| MIG-006 | `jboss-web.xml` | Media | Eliminar/reemplazar configuración específica |
| MIG-007 | JMS administrado | Alta | Rediseñar configuración ConnectionFactory/broker |
| MIG-008 | Camel Servlet | Media | Replantear exposición REST |
| MIG-009 | Spring XML Camel | Media | Convertir a Java DSL/YAML o mantener solo si aplica |
| MIG-010 | URLs hardcodeadas | Alta | Externalizar propiedades |
| MIG-011 | Cobertura de pruebas baja | Alta | Añadir unitarias e integración antes de migrar |

## Recomendación de destino
Para una organización basada fuertemente en Spring: **Camel 4 + Spring Boot**.
Para contenedores con prioridad en arranque/memoria y estandarización cloud-native: **Camel 4 + Quarkus**.
