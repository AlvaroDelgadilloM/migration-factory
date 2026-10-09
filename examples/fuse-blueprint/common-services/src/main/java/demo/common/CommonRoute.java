package demo.common;

import org.apache.camel.builder.RouteBuilder;

public class CommonRoute extends RouteBuilder {
	@Override
	public void configure() throws Exception {
		from("direct-vm:normalize").routeId("NormalizeRoute")
			.bean("normalizer", "clean");
	}
}
