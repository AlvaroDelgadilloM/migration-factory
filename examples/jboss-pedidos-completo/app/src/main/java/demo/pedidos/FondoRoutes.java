package demo.pedidos;

import org.apache.camel.builder.RouteBuilder;
import org.apache.camel.component.sql.SqlConstants;

/** Procesos de fondo: confirmación por cola (con cola de rechazados), tarea programada de vencidos y reabastecimiento por archivo. */
public class FondoRoutes extends RouteBuilder {

    private static final String DS = PedidosRoutes.DS;

    @Override
    public void configure() {
        // un mensaje que no se puede procesar se reintenta dos veces y va, tal cual llegó, a la cola de rechazados
        errorHandler(deadLetterChannel("activemq:queue:pedidos.dlq").maximumRedeliveries(2).redeliveryDelay(200).useOriginalMessage());

        from("activemq:queue:pedidos.confirmados").routeId("confirmar-pedido")
            .convertBodyTo(String.class)
            .process(e -> e.getIn().setHeader("pedidoId", Integer.valueOf(e.getIn().getBody(String.class).trim())))
            .to("sql:update pedidos set estado = 'CONFIRMADO' where id = :#pedidoId and estado = 'PENDIENTE'?" + DS)
            .to("sql:insert into pedido_eventos (pedido_id, evento, detalle) values (:#pedidoId, 'CONFIRMADO', 'confirmado por la cola')?" + DS);

        from("activemq:queue:pedidos.dlq").routeId("mensajes-rechazados")
            .errorHandler(loggingErrorHandler())
            .convertBodyTo(String.class)
            .setHeader("detalle", body())
            .to("sql:insert into pedido_eventos (evento, detalle) values ('DLQ', :#detalle)?" + DS);

        from("activemq:queue:pedidos.errores").routeId("errores-credito")
            .errorHandler(loggingErrorHandler())
            .convertBodyTo(String.class)
            .setHeader("detalle", body())
            .to("sql:insert into pedido_eventos (evento, detalle) values ('ERROR_CREDITO', :#detalle)?" + DS);

        from("timer:vencidos?delay=5000&period={{vencidos.periodo}}").routeId("pedidos-vencidos")
            .errorHandler(defaultErrorHandler())
            .to("sql:update pedidos set estado = 'VENCIDO' where estado = 'PENDIENTE' and creado < now() - interval '1 day'?" + DS)
            .filter(header(SqlConstants.SQL_UPDATE_COUNT).isGreaterThan(0))
                .setHeader("detalle", simple("${header.CamelSqlUpdateCount} pedido(s)"))
                .to("sql:insert into pedido_eventos (evento, detalle) values ('VENCIDOS', :#detalle)?" + DS);

        from("file:{{entrada.dir}}?include=.*\\.csv&move=.procesados&moveFailed=.error&readLock=changed&readLockCheckInterval=500")
            .routeId("reabastecimiento")
            .errorHandler(defaultErrorHandler())
            .setHeader("archivo", header("CamelFileName"))
            .convertBodyTo(String.class)
            .split(body().tokenize("\n")).streaming()
                .filter(simple("${body} regex '^\\s*\\d+\\s*,\\s*\\d+\\s*$'"))
                .process(e -> {
                    String[] p = e.getIn().getBody(String.class).trim().split("\\s*,\\s*");
                    e.getIn().setHeader("productoId", Integer.valueOf(p[0]));
                    e.getIn().setHeader("cantidad", Integer.valueOf(p[1]));
                })
                .to("sql:update inventario set existencias = existencias + :#cantidad where producto_id = :#productoId?" + DS)
            .end()
            .to("sql:insert into pedido_eventos (evento, detalle) values ('REABASTO', :#archivo)?" + DS);
    }
}
