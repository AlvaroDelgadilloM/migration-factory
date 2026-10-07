package demo.orders;

import javax.jms.ConnectionFactory;
import org.apache.camel.component.jms.JmsComponent;

public final class JmsConfig {
    private JmsConfig() {
    }

    public static JmsComponent jms(ConnectionFactory connectionFactory) {
        return JmsComponent.jmsComponentAutoAcknowledge(connectionFactory);
    }
}
