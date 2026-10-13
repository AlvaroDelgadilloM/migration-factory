package org.migrationfactory.compat.directvm;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.apache.camel.CamelContext;
import org.apache.camel.CamelExecutionException;
import org.apache.camel.ProducerTemplate;
import org.apache.camel.builder.RouteBuilder;
import org.apache.camel.impl.DefaultCamelContext;
import org.junit.jupiter.api.Test;

/** direct-vm entre dos CamelContext de la misma JVM, como entre dos bundles de Karaf. */
class DirectVmComponentTest {

	@Test
	void callsARouteOfAnotherContextSynchronously() throws Exception {
		try (CamelContext service = new DefaultCamelContext(); CamelContext client = new DefaultCamelContext()) {
			service.addRoutes(new RouteBuilder() {
				@Override
				public void configure() {
					from("direct-vm:greet").setBody(simple("hola ${body}"));
				}
			});
			service.start();
			client.start();
			ProducerTemplate template = client.createProducerTemplate();
			assertEquals("hola mundo", template.requestBody("direct-vm:greet", "mundo", String.class));
		}
	}

	@Test
	void failsWhenNobodyConsumes() throws Exception {
		try (CamelContext client = new DefaultCamelContext()) {
			client.start();
			CamelExecutionException e = assertThrows(CamelExecutionException.class,
					() -> client.createProducerTemplate().requestBody("direct-vm:nadie-escucha?timeout=200", "x"));
			assertTrue(e.getCause().getMessage().contains("nadie-escucha"), e.getCause().getMessage());
		}
	}

	@Test
	void twoConsumersOfTheSameNameAreRejected() throws Exception {
		try (CamelContext first = new DefaultCamelContext(); CamelContext second = new DefaultCamelContext()) {
			RouteBuilder routes = new RouteBuilder() {
				@Override
				public void configure() {
					from("direct-vm:duplicado").log("x");
				}
			};
			first.addRoutes(routes);
			first.start();
			second.addRoutes(new RouteBuilder() {
				@Override
				public void configure() {
					from("direct-vm:duplicado").log("y");
				}
			});
			Exception e = assertThrows(Exception.class, second::start);
			Throwable root = e;
			while (root.getCause() != null) {
				root = root.getCause();
			}
			assertTrue(root.getMessage().contains("direct-vm:duplicado"), root.getMessage());
		}
	}
}
