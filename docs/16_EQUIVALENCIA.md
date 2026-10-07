# Validación de equivalencia de una aplicación migrada

Las pruebas de `tests/` y `backend/tests/` validan **Migration Factory**. Este documento describe cómo validar que una
**aplicación migrada** se comporta igual que la legacy. Una compilación exitosa y las pruebas unitarias en verde (gates G2/G3)
no lo demuestran; el gate G4 exige registrar esta evidencia (pantalla Validación o `POST /executions/{id}/validations`).

## Preparación
Mismo dataset sintético y mismas entradas contra legacy (JBoss) y candidato; broker y BD representativos y desechables;
relojes y IDs controlados para comparar salidas; captura de mensajes de salida, filas de BD y logs de error.

## Casos mínimos
| Área | Caso | Comparar |
|---|---|---|
| Contratos | Peticiones válidas e inválidas (HTTP/SOAP/XML/JSON) | código, cabeceras, payload, esquema |
| JMS | Mensaje correcto | mensaje de salida, cabeceras, efecto en BD |
| JMS | Duplicado (mismo JMSMessageID / clave de negocio) | idempotencia: un solo efecto |
| JMS | Poison message | nº de reentregas, destino DLQ, cabeceras de error |
| JMS | Caída antes del ack | reentrega sin pérdida ni doble efecto |
| JMS | Reconexión del broker | recuperación sin intervención y sin pérdida |
| JMS | Orden por clave | orden preservado donde la legacy lo garantizaba |
| Redelivery | Máximo de reintentos y backoff | mismos valores efectivos |
| BD | Excepción tras escribir | rollback real (JTA/XA o compensación) |
| BD | Rollback coordinado JMS + BD | ni mensaje consumido sin fila ni fila sin mensaje |
| Seguridad | Roles y realms | mismos permitidos/denegados |

## Registro
Para cada caso: entrada, salida legacy, salida candidato, diferencia y decisión. Las fallas de la legacy (línea base) se
documentan aparte y no se aceptan automáticamente. Registrar el resultado global como evidencia `equivalence`
con referencia al informe.
