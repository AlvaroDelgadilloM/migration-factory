package org.migrationfactory.compat.spring;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.net.InetSocketAddress;
import java.util.concurrent.atomic.AtomicInteger;

import com.sun.net.httpserver.HttpServer;
import org.apache.camel.CamelContext;
import org.apache.camel.Exchange;
import org.apache.camel.ProducerTemplate;
import org.apache.camel.impl.DefaultCamelContext;
import org.junit.jupiter.api.Test;

/** Como el http4 de Camel 2: un 503 de un sistema externo se devuelve tal cual, sin esperar 1 s y repetir la llamada. */
class NoAutomaticHttpRetriesTest {

	@Test
	void a503IsNotRetriedAfterASecond() throws Exception {
		AtomicInteger calls = new AtomicInteger();
		HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
		server.createContext("/icp", ex -> {
			calls.incrementAndGet();
			ex.sendResponseHeaders(503, -1);
			ex.close();
		});
		server.start();
		try (CamelContext context = new DefaultCamelContext()) {
			context.addLifecycleStrategy(new LegacyCamelSettings.NoAutomaticHttpRetries());
			context.start();
			ProducerTemplate template = context.createProducerTemplate();
			long t0 = System.nanoTime();
			Exchange out = template.request("http://127.0.0.1:" + server.getAddress().getPort() + "/icp?throwExceptionOnFailure=false",
					e -> e.getIn().setHeader(Exchange.HTTP_METHOD, "POST"));
			long ms = (System.nanoTime() - t0) / 1_000_000;
			assertEquals(503, out.getMessage().getHeader(Exchange.HTTP_RESPONSE_CODE, Integer.class));
			assertEquals(1, calls.get(), "una sola llamada al sistema externo");
			assertTrue(ms < 900, "sin la espera de 1 s del reintento: " + ms + " ms");
		} finally {
			server.stop(0);
		}
	}
}
