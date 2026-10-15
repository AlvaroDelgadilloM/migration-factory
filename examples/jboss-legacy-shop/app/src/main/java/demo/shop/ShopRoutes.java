package demo.shop;

import java.util.HashMap;
import java.util.Map;

import org.apache.camel.Exchange;
import org.apache.camel.builder.RouteBuilder;
import org.apache.camel.model.dataformat.JsonLibrary;
import org.apache.camel.model.rest.RestBindingMode;

/**
 * Servicios REST de la tienda (estilo JBoss legado): Rest DSL sobre el servlet de Camel (web.xml) y SQL contra el
 * datasource JNDI "shopDS" declarado en applicationContext.xml.
 */
public class ShopRoutes extends RouteBuilder {

    private static final String DS = "dataSource=#shopDS";

    @Override
    public void configure() {
        restConfiguration().component("servlet").bindingMode(RestBindingMode.json).dataFormatProperty("prettyPrint", "false");

        // errors leave through onException, outside the REST binding: marshal to JSON here
        onException(IllegalArgumentException.class).handled(true)
            .setHeader(Exchange.HTTP_RESPONSE_CODE, constant(400))
            .setHeader(Exchange.CONTENT_TYPE, constant("application/json"))
            .process(e -> e.getIn().setBody(error(e.getProperty(Exchange.EXCEPTION_CAUGHT, Exception.class).getMessage())))
            .marshal().json(JsonLibrary.Jackson);

        rest("/customers")
            .get("/{id}").to("direct:getCustomer")
            .get("/{id}/orders").to("direct:customerOrders");
        rest("/products")
            .get().to("direct:products");
        rest("/orders")
            .post().type(Map.class).to("direct:createOrder");
        rest("/stats")
            .get("/top-products").to("direct:topProducts");

        from("direct:getCustomer").routeId("get-customer")
            .setHeader("id").simple("${header.id}", Integer.class)
            .to("sql:select id, name, email, city, to_char(created_at, 'YYYY-MM-DD') as created_at from customers where id = :#id?" + DS)
            .choice()
                .when(simple("${body.size} == 0"))   // SelectOne without rows keeps the request body in Camel 2: use the list
                    .setHeader(Exchange.HTTP_RESPONSE_CODE, constant(404))
                    .setHeader(Exchange.CONTENT_TYPE, constant("application/json"))
                    .process(e -> e.getIn().setBody(error("Cliente no encontrado")))
                    .marshal().json(JsonLibrary.Jackson)   // Camel 2 does not apply the REST binding to non-2xx bodies
                .endChoice()
                .otherwise()
                    .setBody(simple("${body[0]}"))
            .end();

        from("direct:customerOrders").routeId("customer-orders")
            .setHeader("id").simple("${header.id}", Integer.class)
            .to("sql:select o.id, p.name as product, o.quantity, o.status, to_char(o.ordered_at, 'YYYY-MM-DD') as ordered_at, "
                + "p.price * o.quantity as total from orders o join products p on p.id = o.product_id "
                + "where o.customer_id = :#id order by o.ordered_at desc, o.id desc?" + DS);

        from("direct:products").routeId("products")
            .choice()
                .when(header("category").isNotNull())
                    .to("sql:select id, name, category, price from products where category = :#category order by id?" + DS)
                .otherwise()
                    .to("sql:select id, name, category, price from products order by id?" + DS)
            .end();

        from("direct:createOrder").routeId("create-order")
            .process(new OrderValidator())
            .to("sql:insert into orders (customer_id, product_id, quantity, status, ordered_at) "
                + "values (:#customerId, :#productId, :#quantity, 'NEW', current_date) returning id?outputType=SelectOne&" + DS)
            .setHeader(Exchange.HTTP_RESPONSE_CODE, constant(201))
            .process(e -> {
                Map<String, Object> created = new HashMap<>();
                created.put("id", e.getIn().getBody());
                created.put("status", "NEW");
                e.getIn().setBody(created);
            });

        from("direct:topProducts").routeId("top-products")
            .to("sql:select p.id, p.name, sum(o.quantity) as units from orders o join products p on p.id = o.product_id "
                + "where o.status <> 'CANCELLED' group by p.id, p.name order by units desc, p.id limit 5?" + DS);
    }

    static Map<String, Object> error(String message) {
        Map<String, Object> body = new HashMap<>();
        body.put("error", message);
        return body;
    }
}
