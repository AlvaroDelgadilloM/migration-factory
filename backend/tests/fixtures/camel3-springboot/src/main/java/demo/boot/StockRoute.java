package demo.boot;

import org.apache.camel.builder.RouteBuilder;
import org.springframework.stereotype.Component;

@Component
public class StockRoute extends RouteBuilder {
    @Override
    public void configure() {
        from("timer:stock?period=30000").routeId("stock-poll")
            .setBody(constant("ping"))
            .to("jms:queue:stock.requests");
    }
}
