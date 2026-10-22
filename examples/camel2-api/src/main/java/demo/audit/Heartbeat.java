package demo.audit;

import org.apache.camel.support.ServiceSupport;

/** Camel 2: org.apache.camel.support.ServiceSupport (Camel 3: org.apache.camel.support.service.ServiceSupport). */
public class Heartbeat extends ServiceSupport {
    private int beats;

    @Override
    protected void doStart() {
        beats++;
    }

    @Override
    protected void doStop() {
    }

    public int beats() {
        return beats;
    }
}
