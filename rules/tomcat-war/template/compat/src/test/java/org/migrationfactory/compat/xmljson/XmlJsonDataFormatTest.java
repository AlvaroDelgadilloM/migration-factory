package org.migrationfactory.compat.xmljson;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;

import org.apache.camel.CamelContext;
import org.apache.camel.Exchange;
import org.apache.camel.impl.DefaultCamelContext;
import org.apache.camel.support.DefaultExchange;
import org.junit.jupiter.api.Test;

/** El JSON debe conservar la forma que producía camel-xmljson (json-lib) en Camel 2. */
class XmlJsonDataFormatTest {

	@Test
	void xmlToJsonAndBack() throws Exception {
		try (CamelContext context = new DefaultCamelContext()) {
			context.start();
			XmlJsonDataFormat format = new XmlJsonDataFormat();
			format.start();
			Exchange exchange = new DefaultExchange(context);
			ByteArrayOutputStream json = new ByteArrayOutputStream();
			format.marshal(exchange, "<order><id>7</id><item>a</item><item>b</item></order>", json);
			assertEquals("{\"id\":\"7\",\"item\":[\"a\",\"b\"]}", json.toString(StandardCharsets.UTF_8));

			Exchange back = new DefaultExchange(context);
			Object xml = format.unmarshal(back, new ByteArrayInputStream("{\"id\":\"7\"}".getBytes(StandardCharsets.UTF_8)));
			String text = context.getTypeConverter().convertTo(String.class, back, xml);
			assertTrue(text.contains("<id>7</id>"), text);
			format.stop();
		}
	}
}
