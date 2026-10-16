package org.migrationfactory.compat.sql;

import java.util.Map;

import org.apache.camel.Consumer;
import org.apache.camel.Endpoint;
import org.apache.camel.Exchange;
import org.apache.camel.Processor;
import org.apache.camel.Producer;
import org.apache.camel.component.sql.SqlComponent;
import org.apache.camel.support.DefaultEndpoint;
import org.apache.camel.support.DefaultProducer;
import org.apache.camel.support.service.ServiceHelper;

/**
 * Componente SQL con el comportamiento de Camel 2 cuando una consulta no devuelve filas.
 *
 * Con outputType=SelectOne y sin resultado, Camel 2 dejaba el cuerpo del mensaje como estaba; Camel 4 lo pone en null.
 * Las rutas que despues preguntan por el cuerpo toman otra rama, sin ningun error. Aqui se conserva el cuerpo anterior.
 * El conversor de Blueprint usa esta clase en lugar de org.apache.camel.component.sql.SqlComponent.
 */
public class LegacySqlComponent extends SqlComponent {

	@Override
	protected Endpoint createEndpoint(String uri, String remaining, Map<String, Object> parameters) throws Exception {
		return new KeepBodyEndpoint(uri, this, super.createEndpoint(uri, remaining, parameters));
	}

	/** Mismo endpoint SQL; solo el productor conserva el cuerpo cuando la consulta lo dejaria en null. */
	static final class KeepBodyEndpoint extends DefaultEndpoint {

		private final Endpoint delegate;

		KeepBodyEndpoint(String uri, SqlComponent component, Endpoint delegate) {
			super(uri, component);
			this.delegate = delegate;
		}

		@Override
		public void configureProperties(Map<String, Object> options) {
			delegate.configureProperties(options);
		}

		@Override
		public Producer createProducer() throws Exception {
			Producer producer = delegate.createProducer();
			return new DefaultProducer(this) {
				@Override
				public void process(Exchange exchange) throws Exception {
					Object before = exchange.getIn().getBody();
					producer.process(exchange);
					if (before != null && exchange.getException() == null && exchange.getIn().getBody() == null) {
						exchange.getIn().setBody(before);
					}
				}

				@Override
				protected void doStart() throws Exception {
					super.doStart();
					ServiceHelper.startService(producer);
				}

				@Override
				protected void doStop() throws Exception {
					ServiceHelper.stopService(producer);
					super.doStop();
				}
			};
		}

		@Override
		public Consumer createConsumer(Processor processor) throws Exception {
			return delegate.createConsumer(processor);
		}

		@Override
		public boolean isSingleton() {
			return true;
		}

		@Override
		protected void doStart() throws Exception {
			super.doStart();
			ServiceHelper.startService(delegate);
		}

		@Override
		protected void doStop() throws Exception {
			ServiceHelper.stopService(delegate);
			super.doStop();
		}
	}
}
