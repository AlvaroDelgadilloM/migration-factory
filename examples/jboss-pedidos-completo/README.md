# Pedidos: aplicación JBoss legada completa (sintética)

Una aplicación Camel 2 como las que se migran: corre en JBoss/WildFly 10.1 con Java 8 y Spring XML, y junta en un solo WAR casi
todo lo que aparece en un proyecto real. Sirve para probar el flujo completo de la plataforma: subir, analizar, migrar a Spring
Boot, Quarkus o Tomcat WAR, comparar con el original y medir rendimiento. Sin datos reales.

| Qué | Dónde |
|---|---|
| REST con Rest DSL sobre servlet (`/api/*`) | `PedidosRoutes` |
| SOAP publicado con CXF, WSDL primero, modo PAYLOAD (`/ws/pedidos`) | `ServiciosRoutes`, `wsdl/pedidos.wsdl` |
| SOAP consumido: buró de crédito externo con timeout (3 s) y un reintento | `ServiciosRoutes`, `wsdl/credito.wsdl`, `mock/credito_mock.py` |
| Base de datos: datasource JNDI de JBoss y `camel-sql` | `jboss/pedidos-ds.xml`, `db/init.sql` |
| Transacción de Spring con rollback (descuenta existencias y registra el pedido) | ruta `registrar-pedido` |
| Excepciones: `onException` por tipo (400, 404, 409, 422, 503, 500), `doTry/doCatch/doFinally` | `PedidosRoutes`, `Errores` |
| JMS con ActiveMQ: productor, consumidor, reintentos y cola de rechazados (dead letter channel) | `FondoRoutes` |
| Tarea programada (timer) que vence pedidos viejos | ruta `pedidos-vencidos` |
| Archivos: CSV de reabastecimiento con `split` | ruta `reabastecimiento` |
| Transformaciones: JSON (Jackson), XML con XSLT | `xslt/inventario.xsl` |
| Rutas en Spring XML junto a las de Java | `applicationContext.xml` (ruta `salud`) |
| Acoplamientos de JBoss: `jboss-deployment-structure.xml` (CXF propio, sin el subsistema de web services) | `WEB-INF/` |

## Levantarlo (Docker)

```bash
cd examples/jboss-pedidos-completo
(cd app && mvn -q -DskipTests package)
docker compose up -d
python3 scripts/probar.py http://127.0.0.1:18083/pedidos evidence/legacy --entrada entrada
```

`probar.py` recorre 26 casos y guarda cada respuesta como evidencia para compararla con la versión migrada:

- consultas, validaciones, cliente inactivo, sin existencias;
- crédito rechazado (SOAP Fault) y buró sin respuesta;
- rollback y pedido válido confirmado por la cola;
- cancelación con `doTry`, pedido vencido por la tarea programada;
- SOAP publicado (respuesta y Fault), reporte XSLT;
- mensaje inválido en la cola de rechazados, aviso de error por cola, archivo CSV.

GitHub Actions lo corre completo en cada cambio (`.github/workflows/jboss-pedidos-e2e.yml`).

## Lo que enseña de Camel 2 (y que la migración debe conservar)

- **Rutas transaccionales:** siempre llevan su propio manejador de errores (`TransactionErrorHandler`). El `onException` de la ruta
  que las llama no vuelve a manejar esa excepción, así que la llamada va dentro de `doTry/doCatch`.
- **Rutas internas:** usan `noErrorHandler()` para que el error llegue intacto a quien las llamó, con sus reintentos.
- **SOAP Fault del buró:** el cliente CXF devuelve la respuesta por su propio hilo; el Fault se atrapa en la misma llamada y se
  traduce a 422.
- **Cuerpo de la petición en WildFly:** el mensaje HTTP de Camel vuelve a leer la petición cuando el cuerpo queda vacío y Undertow
  ya cerró el stream. Las rutas `rest:` copian el mensaje a uno normal al entrar.
