package org.migrationfactory.compat.cxf;

import org.apache.camel.Exchange;
import org.apache.camel.Processor;

/**
 * Sustituye a .setFaultBody(exchangeProperty("x")), que desaparecio en Camel 3.
 *
 * En Camel 2 marcar el mensaje como fault hacia que CXF respondiera un SOAP Fault. En Camel 4
 * el consumidor CXF responde un fault cuando el exchange termina con una excepcion, asi que se
 * lanza el fault guardado en la propiedad. Si la propiedad no tiene una excepcion, se deja la
 * excepcion original.
 */
public class ThrowFaultProperty implements Processor {

	private final String property;

	public ThrowFaultProperty(String property) {
		this.property = property;
	}

	@Override
	public void process(Exchange exchange) throws Exception {
		Object fault = exchange.getProperty(property);
		if (fault instanceof Exception) {
			throw (Exception) fault;
		}
	}
}
