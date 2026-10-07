package demo.orders;

import org.apache.camel.builder.RouteBuilder;

public class ProcessingRoute extends RouteBuilder {
    @Override
    public void configure() {
        from("direct:process-order").routeId("process-order")
            .bean(OrderTransformer.class, "normalize");
    }
}
