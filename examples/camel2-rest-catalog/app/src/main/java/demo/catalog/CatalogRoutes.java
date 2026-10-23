package demo.catalog;

import org.apache.camel.Exchange;
import org.apache.camel.builder.RouteBuilder;
import org.apache.camel.model.rest.RestBindingMode;

public class CatalogRoutes extends RouteBuilder {
    @Override
    public void configure() {
        restConfiguration().component("servlet").bindingMode(RestBindingMode.off);

        rest("/items")
            .get().route().routeId("items")
                .setHeader(Exchange.CONTENT_TYPE, constant("application/json"))
                .process(e -> e.getIn().setBody(Catalog.all(e.getIn().getHeader("category", String.class))))
            .endRest()
            .get("/{id}").route().routeId("item")
                .setHeader(Exchange.CONTENT_TYPE, constant("application/json"))
                .process(e -> {
                    String json = Catalog.one(e.getIn().getHeader("id", String.class));
                    if (json == null) {
                        e.getIn().setHeader(Exchange.HTTP_RESPONSE_CODE, 404);
                        e.getIn().setBody("{\"error\":\"Artículo no encontrado\"}");
                    } else {
                        e.getIn().setBody(json);
                    }
                })
            .endRest()
            .post("/validate").route().routeId("validate")
                .setHeader(Exchange.CONTENT_TYPE, constant("application/json"))
                .process(e -> {
                    String r = Catalog.validate(e.getIn().getBody(String.class));
                    if (r.startsWith("ERROR:")) {
                        e.getIn().setHeader(Exchange.HTTP_RESPONSE_CODE, 400);
                        e.getIn().setBody("{\"error\":\"" + r.substring(6) + "\"}");
                    } else {
                        e.getIn().setBody(r);
                    }
                })
            .endRest();

        rest("/stats")
            .get().route().routeId("stats")
                .setHeader(Exchange.CONTENT_TYPE, constant("application/json"))
                .process(e -> e.getIn().setBody(Catalog.stats()))
            .endRest();
    }
}
