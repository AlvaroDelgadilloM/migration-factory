package demo.orders;

import static org.junit.Assert.assertTrue;

import java.util.Set;
import java.util.stream.Collectors;
import org.apache.camel.impl.DefaultCamelContext;
import org.apache.camel.model.RouteDefinition;
import org.junit.Test;

/** Generado por Migration Factory: presencia de rutas Java. No arranca el contexto ni contacta sistemas externos;
 *  no sustituye las pruebas de equivalencia. */
public class MigratedRoutesSmokeTest {
    @Test
    public void routesArePresent() throws Exception {
        DefaultCamelContext context = new DefaultCamelContext();
        context.addRoutes(new demo.orders.OrdersRoute());
        context.addRoutes(new demo.orders.ProcessingRoute());
        Set<String> ids = context.getRouteDefinitions().stream().map(RouteDefinition::getRouteId).collect(Collectors.toSet());
        assertTrue("se esperaban rutas: " + ids, ids.size() >= 2);
        for (String id : new String[] {"audit", "orders-in", "process-order"}) {
            assertTrue("ruta ausente: " + id, ids.contains(id));
        }
    }
}
