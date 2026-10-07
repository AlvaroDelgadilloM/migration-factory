import javax.jms.ConnectionFactory;
import javax.sql.DataSource;
import org.apache.camel.builder.RouteBuilder;
public class OrdersRoute extends RouteBuilder {
 public void configure() {
  from("activemq:queue:ORDERS").routeId("orders").to("jdbc:ordersDS");
 }
}
