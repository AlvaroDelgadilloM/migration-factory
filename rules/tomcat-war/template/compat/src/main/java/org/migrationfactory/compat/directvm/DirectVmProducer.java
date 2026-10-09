package org.migrationfactory.compat.directvm;

import org.apache.camel.AsyncCallback;
import org.apache.camel.CamelExchangeException;
import org.apache.camel.Exchange;
import org.apache.camel.support.DefaultAsyncProducer;
import org.apache.camel.support.DefaultExchange;
import org.apache.camel.support.ExchangeHelper;

public class DirectVmProducer extends DefaultAsyncProducer {

	private final DirectVmEndpoint endpoint;

	public DirectVmProducer(DirectVmEndpoint endpoint) {
		super(endpoint);
		this.endpoint = endpoint;
	}

	@Override
	public boolean process(Exchange exchange, AsyncCallback callback) {
		DirectVmConsumer consumer;
		try {
			consumer = awaitConsumer();
		} catch (InterruptedException e) {
			Thread.currentThread().interrupt();
			exchange.setException(e);
			callback.done(true);
			return true;
		}

		if (consumer == null) {
			if (endpoint.isFailIfNoConsumers()) {
				exchange.setException(new CamelExchangeException(
						"No hay consumidores disponibles en direct-vm:" + endpoint.getName(), exchange));
			}
			callback.done(true);
			return true;
		}

		// El exchange se copia para que viaje con el CamelContext del modulo destino,
		// igual que hacia direct-vm en Camel 2/3.
		DirectVmEndpoint target = consumer.getEndpoint();
		Exchange copy = new DefaultExchange(target.getCamelContext(), exchange.getPattern());
		copy.getExchangeExtension().setFromEndpoint(target);
		copy.getIn().copyFrom(exchange.getIn());
		if (endpoint.isPropagateProperties()) {
			copy.getProperties().putAll(exchange.getProperties());
		}

		ClassLoader current = Thread.currentThread().getContextClassLoader();
		ClassLoader app = target.getCamelContext().getApplicationContextClassLoader();
		boolean changed = app != null && app != current;
		if (changed) {
			Thread.currentThread().setContextClassLoader(app);
		}
		try {
			return consumer.getAsyncProcessor().process(copy, doneSync -> {
				try {
					// regresa body, headers, properties y excepcion a la ruta que llamo
					ExchangeHelper.copyResults(exchange, copy);
				} finally {
					callback.done(doneSync);
				}
			});
		} finally {
			if (changed) {
				Thread.currentThread().setContextClassLoader(current);
			}
		}
	}

	private DirectVmConsumer awaitConsumer() throws InterruptedException {
		DirectVmConsumer consumer = DirectVmComponent.getConsumer(endpoint.getName());
		if (consumer != null || !endpoint.isBlock()) {
			return consumer;
		}
		long deadline = System.currentTimeMillis() + endpoint.getTimeout();
		while (consumer == null && System.currentTimeMillis() < deadline) {
			Thread.sleep(100);
			consumer = DirectVmComponent.getConsumer(endpoint.getName());
		}
		return consumer;
	}
}
