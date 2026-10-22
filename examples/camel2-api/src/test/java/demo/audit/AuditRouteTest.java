package demo.audit;

import static org.junit.Assert.assertEquals;

import org.apache.camel.CamelContext;
import org.apache.camel.Exchange;
import org.apache.camel.impl.DefaultCamelContext;
import org.apache.camel.impl.DefaultExchange;
import org.apache.camel.util.ExchangeHelper;
import org.junit.Test;

public class AuditRouteTest {
    @Test
    public void exchangePropertyAndEnrichment() throws Exception {
        CamelContext context = new DefaultCamelContext();
        context.addRoutes(new AuditRoute());
        context.start();
        try {
            assertEquals("audit A1 gold", context.createProducerTemplate().requestBody("direct:audit", "A1", String.class));
        } finally {
            context.stop();
        }
    }

    @Test
    public void movedHelpersStillWork() throws Exception {
        CamelContext context = new DefaultCamelContext();
        Exchange exchange = new DefaultExchange(context);
        exchange.getIn().setBody(42);
        assertEquals("42", ExchangeHelper.convertToType(exchange, String.class, exchange.getIn().getBody()));
        Heartbeat heartbeat = new Heartbeat();
        heartbeat.start();
        assertEquals(1, heartbeat.beats());
    }
}
