package demo.shop;

import java.util.Map;

import org.apache.camel.Exchange;
import org.apache.camel.Processor;

/** Valida el pedido y deja sus campos como headers para la consulta SQL. */
public class OrderValidator implements Processor {

    @Override
    public void process(Exchange exchange) {
        Map<?, ?> order = exchange.getIn().getBody(Map.class);
        if (order == null) {
            throw new IllegalArgumentException("Pedido vacío");
        }
        exchange.getIn().setHeader("customerId", positive(order, "customerId", 5000));
        exchange.getIn().setHeader("productId", positive(order, "productId", 300));
        exchange.getIn().setHeader("quantity", positive(order, "quantity", 100));
    }

    static int positive(Map<?, ?> order, String field, int max) {
        Object value = order.get(field);
        if (!(value instanceof Number)) {
            throw new IllegalArgumentException("Campo obligatorio numérico: " + field);
        }
        int n = ((Number) value).intValue();
        if (n < 1 || n > max) {
            throw new IllegalArgumentException(field + " fuera de rango (1-" + max + ")");
        }
        return n;
    }
}
