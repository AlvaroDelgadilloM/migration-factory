package demo.audit;

import org.apache.camel.Exchange;
import org.apache.camel.processor.aggregate.AggregationStrategy;

/** Camel 2: org.apache.camel.processor.aggregate.AggregationStrategy (Camel 3: org.apache.camel.AggregationStrategy). */
public class LastWins implements AggregationStrategy {
    @Override
    public Exchange aggregate(Exchange original, Exchange resource) {
        original.getIn().setBody(resource.getIn().getBody(String.class));
        return original;
    }
}
