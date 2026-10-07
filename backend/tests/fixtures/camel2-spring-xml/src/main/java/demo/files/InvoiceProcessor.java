package demo.files;

import org.apache.camel.Exchange;
import org.apache.camel.Processor;

public class InvoiceProcessor implements Processor {
    @Override
    public void process(Exchange exchange) {
        double total = exchange.getIn().getHeader("total", Double.class);
        exchange.getIn().setHeader("totalWithTax", total * 1.16);
    }
}
