package org.migrationfactory.compat.spring;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

import org.apache.camel.Message;
import org.apache.camel.impl.DefaultCamelContext;
import org.apache.camel.support.DefaultExchange;
import org.junit.jupiter.api.Test;

/** Nombre de la operacion SOAP con los headers de Camel 2 (operationName) y de Camel 4 (CamelCxfOperationName). */
class CxfOperationHeadersTest {

	private static Message message() {
		return new DefaultExchange(new DefaultCamelContext()).getIn();
	}

	@Test
	void theServiceSideGetsTheCamel2Names() {
		Message in = message();
		in.setHeader("CamelCxfOperationName", "busquedaSocio");
		in.setHeader("CamelCxfOperationNamespace", "http://PmsBepFR.servicios.posadas.com/");
		LegacyCamelSettings.CxfOperationHeaders.copy(in, 0, 1);
		assertEquals("busquedaSocio", in.getHeader("operationName"));
		assertEquals("http://PmsBepFR.servicios.posadas.com/", in.getHeader("operationNamespace"));
	}

	@Test
	void aCallChoosesTheOperationTheRouteSet() {
		Message in = message();
		in.setHeader("CamelCxfOperationName", "busquedaSocio");  // left by the service that received the request
		in.setHeader("operationName", "FRPremierAccrual");        // what the route asks Siebel for
		LegacyCamelSettings.CxfOperationHeaders.copy(in, 1, 0);
		assertEquals("FRPremierAccrual", in.getHeader("CamelCxfOperationName"));
		assertNull(in.getHeader("CamelCxfOperationNamespace"));  // nothing to copy: it stays as it was
	}
}
