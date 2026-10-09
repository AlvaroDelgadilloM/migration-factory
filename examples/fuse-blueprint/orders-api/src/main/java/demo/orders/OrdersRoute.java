package demo.orders;

import org.apache.camel.LoggingLevel;
import org.apache.camel.builder.RouteBuilder;
import org.apache.camel.processor.validation.PredicateValidationException;

public class OrdersRoute extends RouteBuilder {
	@Override
	public void configure() throws Exception {
		onException(PredicateValidationException.class)
			.handled(true)
			.setBody().simple("${exception.message");

		restConfiguration().component("servlet")
			.contextPath("/orders")
			.endpointProperty("servletName", "OrdersCamelServlet")
			.apiContextListing(false);

		rest().get("/healthz")
			.route().setBody().constant("OK");

		rest().get("/{orderId}")
			.to("direct:order");

		from("direct:order").routeId("OrderRoute")
			.validate(header("orderId").isNotNull())
			.setProperty("requested", header("orderId"))
			.log(LoggingLevel.DEBUG, "Pedido ${property.requested}")
			.to("direct-vm:normalize")
			.to("xslt:xsl/order.xsl?saxon=true")
			.filter(simple("${header.Accept} == 'application/json' and ${body} != null"))
				.marshal("xmljson")
			.end();
	}
}
