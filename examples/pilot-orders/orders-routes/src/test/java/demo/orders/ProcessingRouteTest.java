package demo.orders;

import static org.junit.Assert.assertEquals;

import org.apache.camel.CamelContext;
import org.apache.camel.impl.DefaultCamelContext;
import org.junit.Test;

public class ProcessingRouteTest {
    @Test
    public void routeNormalizesBody() throws Exception {
        CamelContext context = new DefaultCamelContext();
        context.addRoutes(new ProcessingRoute());
        context.start();
        try {
            Object out = context.createProducerTemplate().requestBody("direct:process-order", "B7;3");
            assertEquals("ORDER id=B7 amount=3.00", out);
        } finally {
            context.stop();
        }
    }
}
