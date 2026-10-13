package demo.orders;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** Generado por Migration Factory: registra los RouteBuilder legacy como beans para que Camel Spring Boot los cargue.
 *  Sustituye al descubrimiento de JBoss/CDI; revisar antes de aprobar. */
@Configuration
public class MigratedRoutesConfig {
    @Bean
    public demo.orders.OrdersRoute ordersRoute() {
        return new demo.orders.OrdersRoute();
    }

    @Bean
    public demo.orders.ProcessingRoute processingRoute() {
        return new demo.orders.ProcessingRoute();
    }
}
