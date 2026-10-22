package demo.audit;

import org.apache.camel.builder.RouteBuilder;

public class AuditRoute extends RouteBuilder {
    @Override
    public void configure() {
        from("direct:lookup").routeId("lookup")
            .setBody(constant("gold"));

        from("direct:audit").routeId("audit")
            .setProperty("orderId", body())
            .enrich("direct:lookup", new LastWins())
            .log("auditando ${property.orderId}")
            .setBody(simple("audit ${property.orderId} ${body}"));
    }
}
