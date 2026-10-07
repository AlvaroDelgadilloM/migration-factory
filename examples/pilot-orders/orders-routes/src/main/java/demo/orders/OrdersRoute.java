package demo.orders;

import org.apache.camel.builder.RouteBuilder;

public class OrdersRoute extends RouteBuilder {
    @Override
    public void configure() {
        // from("jms:queue:LEGACY_ORDERS") retirado: un comentario no es una ruta.
        from("jms:queue:ORDERS").routeId("orders-in")
            .to("direct:process-order")
            .to("jdbc:ordersDS");

        from("direct:audit").routeId("audit")
            .toD("log:audit-${header.channel}");
    }
}
