# Reglas de migración
## Clasificación
| Nivel | Política |
|---|---|
| AUTO | Transformación determinista con precondiciones verificadas y diff |
| AUTO_TEST | Automática con compilación/pruebas obligatorias |
| REVIEW | Candidato sujeto a revisión y pruebas de comportamiento |
| MANUAL | Rediseño; no sustituir mecánicamente |

Incluso AUTO no implica aplicación validada. `javax.persistence` → `jakarta.persistence` requiere compatibilidad de dependencias y proveedores; clasificar AUTO_TEST. No sustituir todos los `javax.*`: `javax.sql`, `javax.crypto`, `javax.net`, `javax.naming` y APIs Java SE permanecen.

## Matriz inicial
| Caso | Acción | Nivel |
|---|---|---|
| Camel 2 | Revisar guía 2→3 y luego 3→4 y upgrades hasta versión destino | REVIEW |
| `http4:` | Evaluar `http:` y diferencias de opciones/cliente | REVIEW |
| `vm:` / `direct-vm:` | Diseñar límites de CamelContext y comunicación | MANUAL |
| `camel-cdi` | Elegir DI del runtime y adaptar lifecycle | REVIEW |
| `activemq:` | Verificar componente/cliente compatible y broker real | REVIEW |
| JMS | ConnectionFactory, acknowledge, retry, DLQ, transacciones | REVIEW |
| JNDI datasource | Externalizar configuración y preservar identidad del bean | REVIEW |
| EJB | Adaptar scopes, timers, seguridad y semántica transaccional | MANUAL |
| JTA/XA | Decidir gestor XA/compensación según atomicidad existente | MANUAL |
| Spring XML | Evaluar loader y beans soportados; convertir solo con justificación | REVIEW |
| EAR | Separar módulos y dependencias del servidor | MANUAL |
| Seguridad JBoss | Mapear realms/roles a mecanismo objetivo y probar permisos | MANUAL |

No reemplazar `vm:` por `seda:` a ciegas: no conservan automáticamente alcance ni semántica. No cambiar `jdbc` por `sql` ni nombres de DataSource sin necesidad. No asumir que retirar JBoss obliga a cambiar de broker o introducir Kafka.

## Catálogo de producción
Cada regla guarda id, versión, rango fuente/destino, precondiciones, detector, severidad, acción, receta opcional, fuente oficial, ejemplos positivos/negativos y pruebas. Estado: borrador → validada → retirada. Un catálogo congelado identifica el run.

Las reglas JSON entregadas son de diagnóstico textual, no una matriz completa de compatibilidad Camel. La disponibilidad de un componente depende del target exacto y de las extensiones Spring/Quarkus.
