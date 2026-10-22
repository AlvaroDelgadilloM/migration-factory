package demo.audit;

import org.apache.camel.EndpointInject;
import org.apache.camel.ProducerTemplate;

/** Camel 2 @EndpointInject(uri = ...): Camel 4 only has value. */
public class AuditClient {
    @EndpointInject(uri = "direct:audit")
    private ProducerTemplate audit;

    public String send(String id) {
        return audit.requestBody((Object) id, String.class);
    }
}
